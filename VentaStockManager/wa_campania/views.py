"""
Vistas del panel de conexión WhatsApp.

Pantalla `/wa-campania/conexion/`: el operador (superuser) ve el
estado del wa-bot, escanea QR cuando hace falta, y puede
desconectar/reiniciar. Todo proxy-eado a través de Django para no
exponer el puerto del wa-bot al browser ni a internet.

Endpoints:
  GET  /wa-campania/conexion/                   → render del template
  GET  /wa-campania/api/conexion/status/        → JSON con estado
  GET  /wa-campania/api/conexion/qr.png         → PNG del QR (o 204)
  POST /wa-campania/api/conexion/logout/        → cierra sesión
  POST /wa-campania/api/conexion/restart/       → reinicia bot
  POST /wa-campania/api/conexion/test/          → envía mensaje de prueba

Auth: TODO endpoint requiere `is_superuser=True`. La integración con
WhatsApp toca la cuenta personal de Osvaldo — no queremos que un
vendedor cualquiera desconecte o spamee a sus contactos.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from math import ceil
from statistics import median

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.conf import settings
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import wa_client
from .geografia import aplicar_zona, distancia_km, normalizar_zona, ubicaciones_de_clientes


log = logging.getLogger(__name__)


def _solo_superuser(user) -> bool:
    return user.is_authenticated and user.is_superuser


# Decorator combinado: tiene que estar logueado al admin Y ser
# superuser. `staff_member_required` sería más laxo (cualquier staff
# basta) pero el panel hace acciones destructivas (logout) y manda
# mensajes que llegan desde la cuenta del dueño — solo el dueño los
# autoriza.
_superuser_required = user_passes_test(_solo_superuser, login_url='/admin/login/')


@_superuser_required
def panel_conexion(request: HttpRequest) -> HttpResponse:
    """Render del panel. El front carga el estado vía AJAX."""
    return render(request, 'wa_campania/panel_conexion.html', {})


@_superuser_required
@require_GET
def api_clientes_campania(request: HttpRequest) -> JsonResponse:
    """Previsualiza la audiencia definitiva y permite revisar excepciones."""
    from cliente.models import Cliente
    from venta.models import Venta

    qs = Cliente.objects.filter(
        puede_recibir_whatsapp=True,
    ).exclude(whatsapp_number='').order_by('nombre', 'apellido', 'id')

    vendedor_ids = []
    for raw_id in request.GET.getlist('vendedor'):
        try:
            vendedor_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    campania_origen_id = request.GET.get('campania')
    barrio = (request.GET.get('barrio') or '').strip()

    # WhatsApp no entrega de forma visible mensajes enviados a la misma
    # cuenta que está vinculada al bot. Evitamos ofrecerla como destinataria
    # y devolvemos el dato para que el widget pueda explicárselo al operador.
    sender_number = ''
    excluded_sender_client_ids = []
    try:
        status = wa_client.get_status_detail()
        raw_user = (
            ((status.get('me') or {}).get('id') or {}).get('user') or ''
        )
        sender_number = raw_user.split(':', 1)[0]
    except Exception:
        log.exception('No se pudo identificar el número conectado al wa-bot')

    if sender_number:
        excluded_sender_client_ids = list(
            qs.filter(whatsapp_number=sender_number).values_list('id', flat=True)
        )
        qs = qs.exclude(whatsapp_number=sender_number)

    todos = request.GET.get('todos') == '1'
    filtros_aplicados = todos
    if not todos:
        if vendedor_ids:
            qs = qs.filter(vendedor_asignado_id__in=vendedor_ids)
            filtros_aplicados = True
        if campania_origen_id and str(campania_origen_id).isdigit():
            qs = qs.filter(envios_whatsapp__campania_id=int(campania_origen_id))
            filtros_aplicados = True
        if barrio:
            qs = qs.filter(
                Q(direccion__icontains=barrio)
                | Q(direcciones__direccion_texto__icontains=barrio)
                | Q(direcciones__localidad__icontains=barrio)
            )
            filtros_aplicados = True

        dias = request.GET.get('dias')
        if dias and str(dias).isdigit():
            desde = timezone.now().date() - timedelta(days=int(dias))
            qs = qs.filter(Exists(Venta.objects.filter(
                cliente=OuterRef('pk'), fecha_compra__gte=desde,
            )))
            filtros_aplicados = True

        if request.GET.get('favor') == '1' or request.GET.get('deudor') == '1':
            qs = qs.annotate(saldo_calc=Sum('cuenta__movimientos__monto'))
            if request.GET.get('favor') == '1':
                qs = qs.filter(saldo_calc__gt=0)
            if request.GET.get('deudor') == '1':
                qs = qs.filter(saldo_calc__lt=0)
            filtros_aplicados = True

    qs = qs.distinct()
    qs_antes_de_zona = qs
    if not todos and normalizar_zona(request.GET):
        filtros_aplicados = True
    qs, ubicaciones, zona, sin_coordenadas = aplicar_zona(
        qs, {} if todos else request.GET,
    )

    audiencia_total = qs.order_by().values('pk').distinct().count() if filtros_aplicados else 0
    excluidos_ids = []
    for raw_id in request.GET.getlist('excluido'):
        try:
            excluidos_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    excluidos_aplicables = (
        qs.filter(pk__in=excluidos_ids).order_by().values('pk').distinct().count()
        if filtros_aplicados and excluidos_ids else 0
    )
    audiencia_total_final = max(0, audiencia_total - excluidos_aplicables)

    # Una referencia operativa, no un límite de WhatsApp: mediana de clientes
    # únicos visitados por día en los últimos 90 días para cada vendedor.
    recomendacion_diaria = None
    radio_sugerido_km = None
    if vendedor_ids:
        desde = timezone.now().date() - timedelta(days=90)
        muestras = Venta.objects.filter(
            vendedor_id__in=vendedor_ids, fecha_compra__gte=desde,
        ).values('vendedor_id', 'fecha_compra').annotate(
            clientes=Count('cliente_id', distinct=True),
        )
        por_vendedor = {}
        for muestra in muestras:
            por_vendedor.setdefault(muestra['vendedor_id'], []).append(muestra['clientes'])
        estimaciones = [round(median(valores)) for valores in por_vendedor.values() if valores]
        if estimaciones:
            recomendacion_diaria = max(1, sum(estimaciones))

    zona_solicitada = normalizar_zona(request.GET)
    if recomendacion_diaria and zona_solicitada:
        latitud, longitud, _ = zona_solicitada
        distancias = sorted(
            distancia_km(latitud, longitud, punto['latitud'], punto['longitud'])
            for punto in ubicaciones_de_clientes(qs_antes_de_zona).values()
        )
        if distancias:
            indice = min(recomendacion_diaria, len(distancias)) - 1
            radio_sugerido_km = max(0.5, ceil(distancias[indice] * 10) / 10)

    # La búsqueda solo ayuda a revisar la lista; no altera el total a enviar.
    lista_qs = qs
    buscar = (request.GET.get('q') or '').strip()
    if buscar:
        lista_qs = lista_qs.filter(
            Q(nombre__icontains=buscar)
            | Q(apellido__icontains=buscar)
            | Q(direccion__icontains=buscar)
            | Q(whatsapp_number__icontains=buscar)
        )

    paginator = Paginator(lista_qs.distinct(), 10)
    pagina = paginator.get_page(request.GET.get('page') or 1)
    from vendedor.models import Vendedor
    from .models import Campania

    clientes_mapa = {
        cliente.pk: cliente
        for cliente in Cliente.objects.filter(pk__in=ubicaciones.keys())
    }
    puntos_mapa = []
    for cliente_id, ubicacion in ubicaciones.items():
        cliente = clientes_mapa.get(cliente_id)
        if cliente is None:
            continue
        puntos_mapa.append({
            'id': cliente_id,
            'nombre': cliente.nombre_completo().strip(),
            **ubicacion,
        })
    puntos_mapa.sort(key=lambda punto: punto['nombre'].lower())

    return JsonResponse({
        'results': [
            {
                'id': cliente.id,
                'nombre': cliente.nombre_completo().strip(),
                'direccion': cliente.direccion or '',
                'whatsapp': cliente.whatsapp_number,
            }
            for cliente in pagina.object_list
        ],
        'page': pagina.number,
        'pages': paginator.num_pages,
        'total': paginator.count,
        'audiencia_activa': filtros_aplicados,
        'audiencia_total': audiencia_total,
        'audiencia_total_final': audiencia_total_final,
        'excluidos_aplicables': excluidos_aplicables,
        'recomendacion_diaria': recomendacion_diaria,
        'radio_sugerido_km': radio_sugerido_km,
        'tiempo_estimado_minutos': ceil(
            audiencia_total_final * settings.WHATSAPP_DELAY_SECONDS / 60
        ),
        'has_previous': pagina.has_previous(),
        'has_next': pagina.has_next(),
        'excluded_sender_number': sender_number,
        'excluded_sender_client_ids': excluded_sender_client_ids,
        'map_points': puntos_mapa,
        'clientes_sin_coordenadas': sin_coordenadas,
        'zona': ({
            'latitud': zona[0],
            'longitud': zona[1],
            'radio_km': zona[2],
        } if zona else None),
        'vendedores': [
            {'id': vendedor.id, 'nombre': vendedor.display_name()}
            for vendedor in Vendedor.objects.select_related('usuario').order_by(
                'usuario__username', 'id',
            )
        ],
        'campanias': [
            {
                'id': campania.id,
                'nombre': campania.nombre,
                'fecha': campania.created_at.strftime('%d/%m/%Y'),
            }
            for campania in Campania.objects.filter(
                envios__isnull=False,
            ).distinct().order_by('-created_at')[:50]
        ],
    })


@_superuser_required
@require_GET
def api_conexion_status(request: HttpRequest) -> JsonResponse:
    """Proxy a wa-bot /status. El template lo polea cada 3s."""
    return JsonResponse(wa_client.get_status_detail())


@_superuser_required
@require_GET
def api_conexion_qr(request: HttpRequest) -> HttpResponse:
    """
    Devuelve el PNG del QR actual, o 204 si no hay QR (ya está
    conectado o todavía no se generó). El front muestra el <img> y
    lo refresca con cache-bust en cada poll para captar QR rotados.
    """
    png_bytes, motivo = wa_client.get_qr_bytes()
    if png_bytes is None:
        # 204 No Content: el front lo trata como "todavía no hay QR".
        resp = HttpResponse(status=204)
        resp['X-WA-Bot-Reason'] = motivo
        return resp
    resp = HttpResponse(png_bytes, content_type='image/png')
    # No cachear: el QR rota y necesitamos siempre el último.
    resp['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp


@_superuser_required
@require_POST
def api_conexion_logout(request: HttpRequest) -> JsonResponse:
    """
    Cierra la sesión actual de WhatsApp en el wa-bot. El próximo
    arranque va a pedir QR nuevo (la sesión anterior queda invalidada
    en el lado de WhatsApp también).
    """
    resultado = wa_client.logout()
    return JsonResponse(resultado)


@_superuser_required
@require_POST
def api_conexion_restart(request: HttpRequest) -> JsonResponse:
    """
    Reinicia el proceso del wa-bot SIN borrar la sesión. Útil si
    quedó pegado (CONFLICT, UNPAIRED) pero la sesión sigue siendo
    válida en el volume.
    """
    resultado = wa_client.restart()
    return JsonResponse(resultado)


@csrf_exempt
@require_POST
def api_incoming_message(request: HttpRequest) -> JsonResponse:
    """
    POST /wa-campania/api/incoming/

    Endpoint que llama el wa-bot por cada mensaje entrante. Decide si
    auto-responder algo o ignorar.

    `@csrf_exempt` porque el bot NO es un browser — no tiene cookies
    de sesión ni puede leer el csrftoken cookie de Django. La seguridad
    de este endpoint la da el header X-Bot-Token (validado abajo),
    NO el sistema de CSRF que está diseñado para forms web.

    Sin esto, Django rechazaba todos los POSTs con 403 (Forbidden) ANTES
    de llegar al view → el auto-responder nunca se ejecutaba, "lista"
    nunca generaba SolicitudListaCliente.

    Body:
      {
        "from": "5491155551234",   // número del remitente (sin @)
        "text": "lista",            // texto del mensaje
        "message_id": "...",        // id único del mensaje (idempotencia)
      }

    Respuesta:
      {
        "action": "ignore" | "reply_text" | "reply_media",
        "text": "..."  // si action != ignore
        "attachment": { mime, filename, base64 }  // si reply_media
        "reason": "..." // motivo del ignore (debug)
      }

    Autenticación: usa el MISMO token del bot (X-Bot-Token). El bot
    nos llama desde dentro de la red docker, así que el token nos
    confirma que es el bot legítimo y no algo random. Esto NO usa
    session auth (el bot no es un usuario humano).
    """
    # Auth por token. En dev (WHATSAPP_API_TOKEN vacío) se permite,
    # mismo criterio que el bot.
    from django.conf import settings
    expected = getattr(settings, 'WHATSAPP_API_TOKEN', '') or ''
    if expected:
        provided = request.headers.get('X-Bot-Token') or ''
        if provided != expected:
            return JsonResponse({'action': 'ignore', 'reason': 'unauthorized'}, status=401)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'action': 'ignore', 'reason': 'bad_json'}, status=400)

    phone = (payload.get('from') or '').strip()
    text = (payload.get('text') or '').strip()
    if not phone:
        return JsonResponse({'action': 'ignore', 'reason': 'no_phone'}, status=400)

    from . import auto_responder
    resultado = auto_responder.procesar_mensaje_entrante(phone, text, request)
    return JsonResponse(resultado)


@_superuser_required
@require_POST
def api_conexion_test(request: HttpRequest) -> JsonResponse:
    """
    Manda un mensaje de prueba al número que viene en el body
    ({"phone": "5491155551234", "message": "..."}). Útil para que el
    operador valide que la cuenta conectada anda OK antes de mandar
    una campaña grande.

    Sin restricción de rate (es manual, una vez). Si alguien lo
    spammea desde el admin, es problema del propio admin.
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido.'}, status=400)

    phone = (payload.get('phone') or '').strip()
    message = (payload.get('message') or '').strip()
    if not phone:
        return JsonResponse({'ok': False, 'error': 'Falta el número.'}, status=400)
    if not message:
        message = 'Prueba de conexión desde VentaStockManager. Si lo recibís, ¡todo OK!'

    resultado = wa_client.send_text(phone, message)
    return JsonResponse(resultado)
