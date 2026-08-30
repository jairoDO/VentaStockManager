"""Pantallas operativas para asignar y completar repartos."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from vendedor.models import Repartidor
from venta.models import Pedido, PedidoEstadoHistorial


ZONA_HORARIA_OPERATIVA = ZoneInfo('America/Argentina/Cordoba')


def _fecha_hoy_operativa():
    return datetime.now(ZONA_HORARIA_OPERATIVA).date()


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


def _es_admin(user):
    return user.is_authenticated and user.is_superuser


@user_passes_test(_es_admin, login_url='/admin/login/')
def asignar_pedidos_repartidor(request):
    """Pantalla intermedia de la acción masiva del PedidoAdmin."""
    ids = _parse_ids(request.POST.get('pedidos_ids') or request.GET.get('pedidos_ids'))
    pedidos = list(
        Pedido.objects.filter(pk__in=ids)
        .select_related('venta__cliente', 'venta__vendedor', 'repartidor')
        .order_by('venta__fecha_entrega', 'venta__cliente__nombre')
    )
    if not pedidos:
        messages.error(request, 'No se encontraron pedidos para asignar.')
        return redirect('/admin/venta/pedido/')

    if request.method == 'POST':
        repartidor_id = request.POST.get('repartidor_id')
        repartidor = get_object_or_404(Repartidor, pk=repartidor_id, activo=True)
        asignados = 0
        omitidos = 0
        with transaction.atomic():
            bloqueados = {
                pedido.pk: pedido
                for pedido in Pedido.objects.select_for_update().filter(pk__in=ids)
            }
            for pedido_original in pedidos:
                pedido = bloqueados[pedido_original.pk]
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
                    observacion=f'Asignado a {repartidor}',
                )
                asignados += 1

        mensaje = f'{asignados} pedido(s) asignados a {repartidor}.'
        if omitidos:
            mensaje += f' {omitidos} entregado(s) no se modificaron.'
        messages.success(request, mensaje)
        return redirect('/admin/venta/pedido/')

    return render(request, 'venta/reparto_asignar.html', {
        'pedidos': pedidos,
        'pedidos_ids': ','.join(str(p.pk) for p in pedidos),
        'repartidores': Repartidor.objects.filter(activo=True).select_related('usuario'),
        'sin_ubicacion': sum(not p.tiene_coordenadas_entrega for p in pedidos),
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
    repartidor = _repartidor_para_request(request)
    if not repartidor:
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

    qs = (
        Pedido.objects
        .filter(repartidor=repartidor, venta__fecha_entrega=fecha)
        .select_related('venta__cliente', 'venta__vendedor', 'direccion_entrega')
        .prefetch_related('venta__ventas__articulo')
        .order_by('localidad_entrega', 'venta__cliente__nombre')
    )
    if localidad:
        qs = qs.filter(localidad_entrega=localidad)
    pedidos = list(qs)

    localidades = list(
        Pedido.objects.filter(repartidor=repartidor, venta__fecha_entrega=fecha)
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
    } for pedido in pedidos]

    return render(request, 'venta/reparto_panel.html', {
        'repartidor': repartidor,
        'pedidos': pedidos,
        'fecha': fecha,
        'localidad_actual': localidad,
        'localidades': localidades,
        'mapa': mapa,
        'motivos_no_entrega': Pedido.MOTIVO_NO_ENTREGA_CHOICES,
        'es_vista_admin': request.user.is_superuser,
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
    if pedido.estado == Pedido.ENTREGADO and not es_admin:
        return JsonResponse({'ok': False, 'error': 'El pedido ya fue entregado.'}, status=409)

    nuevo_estado = payload.get('estado')
    permitidos = {
        Pedido.EN_REPARTO,
        Pedido.ENTREGADO,
        Pedido.NO_ENTREGADO,
        Pedido.REPROGRAMADO,
    }
    if nuevo_estado not in permitidos:
        return JsonResponse({'ok': False, 'error': 'Estado no permitido.'}, status=400)

    try:
        with transaction.atomic():
            pedido = Pedido.objects.select_for_update().get(pk=pedido.pk)
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
        'entregado_en': pedido.entregado_en.isoformat() if pedido.entregado_en else None,
    })
