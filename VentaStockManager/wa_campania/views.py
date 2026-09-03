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

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import wa_client


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
    """Lista paginada de clientes elegibles para selección manual."""
    from cliente.models import Cliente

    qs = Cliente.objects.filter(
        puede_recibir_whatsapp=True,
    ).exclude(whatsapp_number='').order_by('nombre', 'apellido', 'id')

    vendedor_ids = []
    for raw_id in request.GET.getlist('vendedor'):
        try:
            vendedor_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    if vendedor_ids:
        qs = qs.filter(ventas__vendedor_id__in=vendedor_ids)

    barrio = (request.GET.get('barrio') or '').strip()
    if barrio:
        qs = qs.filter(
            Q(direccion__icontains=barrio)
            | Q(direcciones__direccion_texto__icontains=barrio)
            | Q(direcciones__localidad__icontains=barrio)
        )

    qs = qs.distinct()

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

    buscar = (request.GET.get('q') or '').strip()
    if buscar:
        qs = qs.filter(
            Q(nombre__icontains=buscar)
            | Q(apellido__icontains=buscar)
            | Q(direccion__icontains=buscar)
            | Q(whatsapp_number__icontains=buscar)
        )

    paginator = Paginator(qs, 10)
    pagina = paginator.get_page(request.GET.get('page') or 1)
    from vendedor.models import Vendedor

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
        'has_previous': pagina.has_previous(),
        'has_next': pagina.has_next(),
        'excluded_sender_number': sender_number,
        'excluded_sender_client_ids': excluded_sender_client_ids,
        'vendedores': [
            {'id': vendedor.id, 'nombre': vendedor.display_name()}
            for vendedor in Vendedor.objects.select_related('usuario').order_by(
                'usuario__username', 'id',
            )
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
