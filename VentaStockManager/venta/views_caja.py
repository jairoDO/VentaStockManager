"""
Pantalla "Caja del día" — resumen rápido para que el admin sepa al
final del día cuánto vendió, cuánto cobró y cuánto efectivo debería
haber en la caja.

Lógica de cálculo:
  - Ventas del día: todas las Ventas con fecha_compra = fecha elegida.
  - Ventas pagadas al momento: las que tienen Pedido.pagado = True.
    Eso significa "el cliente pagó al cargar la venta" → entró cash hoy.
  - Ventas a cuenta corriente: Pedido.pagado = False. NO entró cash
    hoy (queda como deuda en la cuenta del cliente).
  - Pagos recibidos: MovimientoCuenta tipo='pago' creados en la fecha.
    Son clientes que vinieron a pagar deuda pendiente → entró cash hoy.

  Efectivo esperado en caja = (ventas pagadas al momento) + (pagos recibidos)

Permisos:
  - Superuser: ve totales del negocio (todas las ventas/pagos).
  - Vendedor: ve solo SUS ventas (filtrado por venta.vendedor.usuario).
    Si no tiene Vendedor asociado, ve panel vacío.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.decorators import user_passes_test
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from venta.models import Venta
from cliente.models import MovimientoCuenta


def _solo_superuser(u) -> bool:
    """Solo el admin (superuser) ve la caja del día — el vendedor no."""
    return bool(u.is_authenticated and u.is_superuser)


@user_passes_test(_solo_superuser, login_url='/admin/login/')
def caja_del_dia(request: HttpRequest) -> HttpResponse:
    # Parseo de fecha: ?fecha=YYYY-MM-DD. Default a hoy.
    # Usamos `date.today()` (no `timezone.localdate()`) porque el proyecto
    # tiene USE_TZ=False — los datetimes son naive y `localdate()` revienta
    # con "cannot be applied to a naive datetime". `date.today()` toma el
    # date del servidor sin tocar timezone, que es lo que queremos acá.
    fecha_str = request.GET.get('fecha', '').strip()
    hoy = date.today()
    if fecha_str:
        try:
            fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            fecha = hoy
    else:
        fecha = hoy

    # Acceso restringido a superuser por decorator. Mantenemos el flag
    # `es_superuser` para mínima refactor — siempre será True por el gate.
    es_superuser = True

    # ---- Ventas del día ----
    ventas_qs = (
        Venta.objects
        .filter(fecha_compra=fecha)
        .select_related('cliente', 'vendedor', 'pedido')
        .prefetch_related('ventas__articulo')
        .order_by('id')
    )

    ventas_data: list[dict] = []
    total_vendido = Decimal('0')
    total_a_cuenta = Decimal('0')
    # "Cobrado al contado sin trackeo": ventas con pedido.pagado=True
    # pero cuyo cliente NO tiene CuentaCliente, así que NO hay
    # MovimientoCuenta(PAGO) que cuente la plata. Cada peso se cuenta
    # una sola vez sumando estos totales aparte de los movimientos.
    total_cobrado_sin_tracking = Decimal('0')

    for v in ventas_qs:
        try:
            tot = Decimal(str(v.precio_total or 0))
        except Exception:
            tot = Decimal('0')
        try:
            pagado = bool(v.pedido and v.pedido.pagado)
        except Exception:
            pagado = False

        # Si la venta tiene PAGO movement(s), ya lo cobramos via la
        # tabla de movimientos. Si no tiene NINGÚN movement de tipo
        # PAGO, la venta se cobró "al contado" sin trackear → sumamos
        # el total acá para no perder esa plata del cálculo.
        tiene_pago_movement = MovimientoCuenta.objects.filter(
            venta=v, tipo=MovimientoCuenta.TIPO_PAGO,
        ).exists()
        cobrado_sin_track = pagado and not tiene_pago_movement
        if cobrado_sin_track:
            total_cobrado_sin_tracking += tot

        ventas_data.append({
            'id': v.id,
            'cliente': str(v.cliente) if v.cliente else '—',
            'vendedor': str(v.vendedor) if v.vendedor else '—',
            'total': tot,
            'pagado': pagado,
            'cobrado_sin_track': cobrado_sin_track,
            'estado': v.pedido.estado if (hasattr(v, 'pedido') and v.pedido) else '',
        })
        total_vendido += tot
        if not pagado:
            total_a_cuenta += tot

    # ---- Pagos registrados del día (MovimientoCuenta tipo='pago') ----
    # Esto incluye:
    #   - Pagos al cargar la venta (api_venta_guardar crea PAGO).
    #   - Pagos via "Registrar pago" o "Marcar como saldada" (cliente
    #     con cuenta).
    #   - Pagos cargados a mano desde la pantalla del cliente.
    # Cada peso se cuenta UNA sola vez acá (no se duplica con la
    # tabla de ventas).
    pagos_data: list[dict] = []
    total_pagos = Decimal('0')
    pagos_qs = (
        MovimientoCuenta.objects
        .filter(created_at__date=fecha, tipo=MovimientoCuenta.TIPO_PAGO)
        .select_related('cuenta__cliente', 'creado_por', 'venta')
        .order_by('created_at')
    )
    for p in pagos_qs:
        try:
            m = Decimal(str(p.monto or 0))
        except Exception:
            m = Decimal('0')
        pagos_data.append({
            'id': p.id,
            'cliente': str(p.cuenta.cliente) if (p.cuenta and p.cuenta.cliente) else '—',
            'monto': m,
            'nota': p.descripcion or '',
            'venta_id': p.venta_id,
            'hora': p.created_at.strftime('%H:%M') if p.created_at else '',
            'creado_por': str(p.creado_por) if p.creado_por else '',
        })
        total_pagos += m

    # Efectivo total esperado: cobrado de ventas sin tracking
    # (contado puro) + todos los PAGO movements del día.
    efectivo_esperado = total_cobrado_sin_tracking + total_pagos

    # Navegación día anterior / siguiente para los flechitas del template.
    from datetime import timedelta
    fecha_anterior = fecha - timedelta(days=1)
    fecha_siguiente = fecha + timedelta(days=1)

    return render(request, 'venta/caja_del_dia.html', {
        'fecha': fecha,
        'fecha_str': fecha.strftime('%Y-%m-%d'),
        'fecha_anterior': fecha_anterior.strftime('%Y-%m-%d'),
        'fecha_siguiente': fecha_siguiente.strftime('%Y-%m-%d'),
        'es_hoy': fecha == hoy,
        'hoy_str': hoy.strftime('%Y-%m-%d'),
        'es_superuser': es_superuser,
        'ventas': ventas_data,
        'pagos': pagos_data,
        'count_ventas': len(ventas_data),
        'count_pagos': len(pagos_data),
        'total_vendido': total_vendido,
        'total_cobrado_sin_tracking': total_cobrado_sin_tracking,
        # `total_pagado_al_momento` se mantiene como alias por
        # compatibilidad con el template (que lo muestra como
        # "Pagado al momento") — ahora apunta al mismo número que
        # `total_cobrado_sin_tracking`, semánticamente más correcto.
        'total_pagado_al_momento': total_cobrado_sin_tracking,
        'total_a_cuenta': total_a_cuenta,
        'total_pagos': total_pagos,
        'efectivo_esperado': efectivo_esperado,
    })
