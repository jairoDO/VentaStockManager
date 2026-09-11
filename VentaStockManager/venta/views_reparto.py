"""Pantallas operativas para asignar y completar repartos."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from cliente.models import CuentaCliente, MovimientoCuenta
from vendedor.models import Repartidor, Vendedor
from venta.models import Pedido, PedidoEstadoHistorial
from venta.utils import subtotal_linea, total_venta


ZONA_HORARIA_OPERATIVA = ZoneInfo('America/Argentina/Cordoba')
PEDIDOS_POR_PAGINA = 20


def _fecha_hoy_operativa():
    return datetime.now(ZONA_HORARIA_OPERATIVA).date()


class FormularioAcceso(AuthenticationForm):
    """Mensajes claros sin alterar la pantalla histórica de django-material."""

    error_messages = {
        'invalid_login': 'El usuario o la contraseña no son correctos. Intentá nuevamente.',
        'inactive': 'Este usuario está desactivado. Consultá al administrador.',
    }


class AccesoSistemaView(LoginView):
    """Login único: redirige según el rol del usuario autenticado."""

    # Misma estructura visual que el login histórico del admin, pero en
    # una plantilla independiente para aceptar también repartidores.
    template_name = 'registration/login_admin.html'
    authentication_form = FormularioAcceso

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Import local para evitar el ciclo urls -> admin -> venta.admin.
        from VentaStockManager.admin import admin_site

        context.update(admin_site.each_context(self.request))
        context.update({
            'title': 'Iniciar sesión',
            'app_path': self.request.get_full_path(),
        })
        return context

    def get_success_url(self):
        # Un repartidor puede llegar desde /admin/ y traer next=/admin/.
        # Si respetamos ese destino, el admin lo rechaza (no es staff) y lo
        # devuelve al login, dando la impresión de que la clave era inválida.
        if not (self.request.user.is_superuser or self.request.user.is_staff):
            try:
                if self.request.user.repartidor.activo:
                    return reverse('reparto_panel')
            except Repartidor.DoesNotExist:
                pass

        destino_solicitado = self.get_redirect_url()
        if destino_solicitado:
            return destino_solicitado
        if self.request.user.is_superuser or self.request.user.is_staff:
            return reverse('admin:index')
        return reverse('reparto_panel')


def _parse_ids(raw: str) -> list[int]:
    ids = []
    for value in (raw or '').split(','):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in ids:
            ids.append(parsed)
    return ids


def _decimal_cobro(valor, nombre, errores):
    try:
        monto = Decimal(str(valor if valor not in (None, '') else '0').replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        errores.append(f'{nombre}: ingresá un monto válido.')
        return Decimal('0')
    if monto < 0:
        errores.append(f'{nombre}: el monto no puede ser negativo.')
    return monto.quantize(Decimal('0.01'))


def _es_admin(user):
    return user.is_authenticated and user.is_superuser


def _filtros_planificacion(request):
    """Normaliza los filtros GET/POST usados para listar y asignar pedidos."""
    datos = request.POST if request.method == 'POST' else request.GET
    fecha_raw = datos.get('fecha') or str(_fecha_hoy_operativa())
    try:
        fecha = date.fromisoformat(fecha_raw)
    except ValueError:
        fecha = _fecha_hoy_operativa()

    vendedores_ids = _parse_ids(','.join(datos.getlist('vendedor')))
    asignacion = datos.get('asignacion') or 'sin_asignar'
    if asignacion not in {'sin_asignar', 'asignados', 'todos'}:
        asignacion = 'sin_asignar'

    return {
        'fecha': fecha,
        'vendedores_ids': vendedores_ids,
        'asignacion': asignacion,
        'localidad': (datos.get('localidad') or '').strip(),
    }


def _pedidos_para_planificar(filtros):
    """Fuente única de resultados; también protege la selección global."""
    queryset = (
        Pedido.objects
        .filter(venta__fecha_entrega=filtros['fecha'])
        .exclude(estado=Pedido.ENTREGADO)
        .select_related(
            'venta__cliente', 'venta__vendedor__usuario', 'repartidor__usuario',
        )
        .order_by('localidad_entrega', 'venta__cliente__nombre', 'pk')
    )
    if filtros['vendedores_ids']:
        queryset = queryset.filter(venta__vendedor_id__in=filtros['vendedores_ids'])
    if filtros['asignacion'] == 'sin_asignar':
        queryset = queryset.filter(repartidor__isnull=True)
    elif filtros['asignacion'] == 'asignados':
        queryset = queryset.filter(repartidor__isnull=False)
    if filtros['localidad']:
        queryset = queryset.filter(localidad_entrega=filtros['localidad'])
    return queryset


def _url_planificacion(filtros):
    parametros = {
        'fecha': filtros['fecha'].isoformat(),
        'asignacion': filtros['asignacion'],
    }
    if filtros['vendedores_ids']:
        parametros['vendedor'] = filtros['vendedores_ids']
    if filtros['localidad']:
        parametros['localidad'] = filtros['localidad']
    return f"{reverse('reparto_planificar')}?{urlencode(parametros, doseq=True)}"


@user_passes_test(_es_admin, login_url='/admin/login/')
def planificar_reparto(request):
    """Bandeja paginada para filtrar y asignar una tanda de reparto."""
    filtros = _filtros_planificacion(request)
    queryset = _pedidos_para_planificar(filtros)
    if request.method == 'POST':
        repartidor = get_object_or_404(
            Repartidor,
            pk=request.POST.get('repartidor_id'),
            activo=True,
        )
        if request.POST.get('seleccionar_todos') == '1':
            pedidos_ids = list(queryset.values_list('pk', flat=True))
        else:
            elegidos = _parse_ids(','.join(request.POST.getlist('pedido')))
            pedidos_ids = list(
                queryset.filter(pk__in=elegidos).values_list('pk', flat=True)
            )

        if not pedidos_ids:
            messages.error(request, 'Seleccioná al menos un pedido para asignar.')
            return redirect(_url_planificacion(filtros))

        asignados = 0
        omitidos = 0
        with transaction.atomic():
            pedidos = list(
                Pedido.objects
                .select_for_update()
                .filter(pk__in=pedidos_ids)
                .order_by('pk')
            )
            for pedido in pedidos:
                if pedido.estado == Pedido.ENTREGADO:
                    omitidos += 1
                    continue
                anterior = pedido.estado
                pedido.repartidor = repartidor
                pedido.asignado_en = timezone.now()
                pedido.actualizado_por = request.user
                if pedido.estado in (Pedido.PENDIENTE, Pedido.REPROGRAMADO, Pedido.NO_ENTREGADO):
                    pedido.estado = Pedido.ASIGNADO
                pedido.save(update_fields=[
                    'repartidor', 'asignado_en', 'actualizado_por', 'estado',
                ])
                PedidoEstadoHistorial.objects.create(
                    pedido=pedido,
                    estado_anterior=anterior,
                    estado_nuevo=pedido.estado,
                    usuario=request.user,
                    observacion=f'Planificación de reparto: asignado a {repartidor}',
                )
                asignados += 1

        mensaje = f'{asignados} pedido(s) asignados a {repartidor}.'
        if omitidos:
            mensaje += f' {omitidos} entregado(s) no se modificaron.'
        messages.success(request, mensaje)
        return redirect(_url_planificacion(filtros))

    total_resultados = queryset.count()
    sin_ubicacion = queryset.filter(
        Q(direccion_confirmada=False)
        | Q(latitud_entrega__isnull=True)
        | Q(longitud_entrega__isnull=True)
    ).count()
    pagina = Paginator(queryset, PEDIDOS_POR_PAGINA).get_page(request.GET.get('page'))

    vendedores = list(
        Vendedor.objects.select_related('usuario').order_by('usuario__username')
    )
    vendedores_elegidos = {str(pk) for pk in filtros['vendedores_ids']}
    for vendedor in vendedores:
        vendedor.seleccionado_planificacion = str(vendedor.pk) in vendedores_elegidos

    base_localidades = (
        Pedido.objects
        .filter(venta__fecha_entrega=filtros['fecha'])
        .exclude(estado=Pedido.ENTREGADO)
    )
    if filtros['vendedores_ids']:
        base_localidades = base_localidades.filter(
            venta__vendedor_id__in=filtros['vendedores_ids']
        )
    localidades = list(
        base_localidades
        .exclude(localidad_entrega='')
        .order_by('localidad_entrega')
        .values_list('localidad_entrega', flat=True)
        .distinct()
    )

    query_sin_pagina = request.GET.copy()
    query_sin_pagina.pop('page', None)
    return render(request, 'venta/reparto_planificar.html', {
        'pagina': pagina,
        'total_resultados': total_resultados,
        'sin_ubicacion': sin_ubicacion,
        'repartidores': Repartidor.objects.filter(activo=True).select_related('usuario'),
        'vendedores': vendedores,
        'vendedores_ids': filtros['vendedores_ids'],
        'fecha': filtros['fecha'],
        'hoy': _fecha_hoy_operativa(),
        'asignacion': filtros['asignacion'],
        'localidad_actual': filtros['localidad'],
        'localidades': localidades,
        'filtros_query': query_sin_pagina.urlencode(),
    })


def _repartidor_para_request(request):
    if request.user.is_superuser and request.GET.get('repartidor_id'):
        return get_object_or_404(
            Repartidor.objects.select_related('usuario'),
            pk=request.GET['repartidor_id'],
        )
    try:
        repartidor = request.user.repartidor
        return repartidor if repartidor.activo else None
    except Repartidor.DoesNotExist:
        return None


@login_required(login_url='login')
def reparto_panel(request):
    es_admin = request.user.is_superuser
    repartidor = _repartidor_para_request(request)
    if not repartidor and not es_admin:
        return HttpResponseForbidden('Este usuario no tiene perfil de repartidor.')

    # No usamos timezone.localdate(): este proyecto trabaja con datetimes
    # ingenuos. La fecha operativa sí debe corresponder a Córdoba aunque el
    # contenedor de producción esté configurado en UTC.
    fecha_raw = request.GET.get('fecha') or str(_fecha_hoy_operativa())
    try:
        fecha = date.fromisoformat(fecha_raw)
    except ValueError:
        fecha = _fecha_hoy_operativa()
    localidad = (request.GET.get('localidad') or '').strip()

    qs_base = (
        Pedido.objects
        .filter(venta__fecha_entrega=fecha)
        .select_related('venta__cliente', 'venta__vendedor', 'direccion_entrega')
        .select_related('repartidor__usuario')
        .prefetch_related('venta__ventas__articulo')
        .order_by('localidad_entrega', 'venta__cliente__nombre')
    )
    if repartidor:
        qs_base = qs_base.filter(repartidor=repartidor)

    qs = qs_base
    if localidad:
        qs = qs.filter(localidad_entrega=localidad)
    pedidos = list(qs)
    for pedido in pedidos:
        pedido.total_reparto = total_venta(pedido.venta)
        for item in pedido.venta.ventas.all():
            item.subtotal_reparto = subtotal_linea(item)

    localidades = list(
        qs_base
        .exclude(localidad_entrega='')
        .order_by('localidad_entrega')
        .values_list('localidad_entrega', flat=True)
        .distinct()
    )
    mapa = [{
        'id': pedido.pk,
        'cliente': pedido.venta.cliente.nombre_completo(),
        'direccion': pedido.direccion_entrega_texto,
        'localidad': pedido.localidad_entrega,
        'latitud': float(pedido.latitud_entrega) if pedido.latitud_entrega is not None else None,
        'longitud': float(pedido.longitud_entrega) if pedido.longitud_entrega is not None else None,
        'estado': pedido.estado,
        'total': str(pedido.total_reparto.quantize(Decimal('0.01'))),
        'efectivo': str(pedido.monto_efectivo_entrega),
        'transferencia': str(pedido.monto_transferencia_entrega),
        'cuenta_corriente': str(pedido.monto_cuenta_corriente_entrega),
        'cobro_registrado': bool(pedido.cobro_entrega_registrado_en),
        'repartidor': str(pedido.repartidor) if pedido.repartidor else 'Sin asignar',
        'fecha_entrega': pedido.venta.fecha_entrega.isoformat(),
        'horario_desde': pedido.horario_entrega_desde.strftime('%H:%M') if pedido.horario_entrega_desde else '',
        'horario_hasta': pedido.horario_entrega_hasta.strftime('%H:%M') if pedido.horario_entrega_hasta else '',
        'horario_2_desde': pedido.horario_entrega_2_desde.strftime('%H:%M') if pedido.horario_entrega_2_desde else '',
        'horario_2_hasta': pedido.horario_entrega_2_hasta.strftime('%H:%M') if pedido.horario_entrega_2_hasta else '',
    } for pedido in pedidos]

    return render(request, 'venta/reparto_panel.html', {
        'repartidor': repartidor,
        'repartidores': (
            Repartidor.objects.filter(activo=True).select_related('usuario')
            if es_admin else []
        ),
        'pedidos': pedidos,
        'fecha': fecha,
        'localidad_actual': localidad,
        'localidades': localidades,
        'mapa': mapa,
        'motivos_no_entrega': Pedido.MOTIVO_NO_ENTREGA_CHOICES,
        'es_vista_admin': es_admin,
        'es_vista_general': es_admin and repartidor is None,
    })


@login_required(login_url='login')
@require_POST
def reparto_actualizar_estado(request, pedido_id):
    try:
        payload = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    pedido = get_object_or_404(Pedido.objects.select_related('repartidor__usuario'), pk=pedido_id)
    es_admin = request.user.is_superuser
    es_duenio = pedido.repartidor_id and pedido.repartidor.usuario_id == request.user.id
    if not es_admin and not es_duenio:
        return JsonResponse({'ok': False, 'error': 'Pedido no asignado a este repartidor.'}, status=403)
    nuevo_estado = payload.get('estado')
    permitidos = {
        Pedido.EN_REPARTO,
        Pedido.ENTREGADO,
        Pedido.NO_ENTREGADO,
        Pedido.REPROGRAMADO,
    }
    if nuevo_estado not in permitidos:
        return JsonResponse({'ok': False, 'error': 'Estado no permitido.'}, status=400)

    cobro_entrega = None
    if nuevo_estado == Pedido.ENTREGADO:
        pago = payload.get('pago')
        if not isinstance(pago, dict):
            return JsonResponse({
                'ok': False,
                'error': 'Indicá cómo se pagó antes de marcar el pedido como entregado.',
            }, status=400)
        errores_cobro = []
        efectivo = _decimal_cobro(pago.get('efectivo'), 'Efectivo', errores_cobro)
        transferencia = _decimal_cobro(
            pago.get('transferencia'), 'Transferencia', errores_cobro,
        )
        cuenta_corriente = _decimal_cobro(
            pago.get('cuenta_corriente'), 'Cuenta corriente', errores_cobro,
        )
        total_pedido = Decimal(total_venta(pedido.venta) or 0).quantize(Decimal('0.01'))
        total_declarado = efectivo + transferencia + cuenta_corriente
        if total_declarado != total_pedido:
            diferencia = total_pedido - total_declarado
            errores_cobro.append(
                f'Los importes deben sumar ${total_pedido:,.2f}. '
                f'La diferencia es ${diferencia:,.2f}.'
            )
        if errores_cobro:
            return JsonResponse({
                'ok': False,
                'error': ' '.join(errores_cobro),
            }, status=400)
        cobro_entrega = {
            'efectivo': efectivo,
            'transferencia': transferencia,
            'cuenta_corriente': cuenta_corriente,
        }

    try:
        with transaction.atomic():
            pedido = (
                Pedido.objects
                .select_for_update()
                .select_related('venta__cliente')
                .get(pk=pedido.pk)
            )
            if cobro_entrega is not None:
                CuentaCliente.objects.get_or_create(cliente=pedido.venta.cliente)
                total_cobrado = (
                    cobro_entrega['efectivo'] + cobro_entrega['transferencia']
                )
                pedido.set_monto_pagado(total_cobrado, user=request.user)
                pedido.monto_efectivo_entrega = cobro_entrega['efectivo']
                pedido.monto_transferencia_entrega = cobro_entrega['transferencia']
                pedido.monto_cuenta_corriente_entrega = cobro_entrega['cuenta_corriente']
                pedido.cobro_entrega_registrado_en = timezone.now()
                pedido.pagado = cobro_entrega['cuenta_corriente'] == 0
                pedido.save(update_fields=[
                    'monto_efectivo_entrega', 'monto_transferencia_entrega',
                    'monto_cuenta_corriente_entrega',
                    'cobro_entrega_registrado_en', 'pagado',
                ])
                partes = []
                if cobro_entrega['efectivo']:
                    partes.append(f'efectivo ${cobro_entrega["efectivo"]:,.2f}')
                if cobro_entrega['transferencia']:
                    partes.append(
                        f'transferencia ${cobro_entrega["transferencia"]:,.2f}'
                    )
                MovimientoCuenta.objects.filter(pedido_origen=pedido).update(
                    descripcion=(
                        f'Cobro al entregar pedido #{pedido.id}: '
                        + (' + '.join(partes) or 'sin cobro')
                    ),
                )
            pedido.cambiar_estado_entrega(
                nuevo_estado,
                usuario=request.user,
                motivo=(payload.get('motivo') or '').strip(),
                observacion=(payload.get('observacion') or '').strip(),
            )
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    return JsonResponse({
        'ok': True,
        'pedido_id': pedido.pk,
        'estado': pedido.estado,
        'estado_display': pedido.get_estado_display(),
        'forma_pago': pedido.forma_pago_entrega_texto,
        'entregado_en': pedido.entregado_en.isoformat() if pedido.entregado_en else None,
    })
