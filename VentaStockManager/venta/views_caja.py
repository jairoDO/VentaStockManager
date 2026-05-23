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

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from venta.models import Venta
from cliente.models import MovimientoCuenta


@staff_member_required
def caja_del_dia(request: HttpRequest) -> HttpResponse:
    # Parseo de fecha: ?fecha=YYYY-MM-DD. Default a hoy.
    fecha_str = request.GET.get('fecha', '').strip()
    hoy = timezone.localdate()
    if fecha_str:
        try:
            fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            fecha = hoy
    else:
        fecha = hoy

    es_superuser = request.user.is_superuser

    # Si es vendedor no-superuser, restringimos a SUS ventas.
    # Buscamos su Vendedor por la relación inversa del OneToOne.
    vendedor_filter = None
    if not es_superuser:
        try:
            vendedor_filter = request.user.vendedor
        except Exception:
            vendedor_filter = None  # Sin Vendedor asociado → panel vacío.

    # ---- Ventas del día ----
    ventas_qs = (
        Venta.objects
        .filter(fecha_compra=fecha)
        .select_related('cliente', 'vendedor', 'pedido')
        .prefetch_related('ventas__articulo')  # `ventas` es el reverse de ArticuloVenta.venta
        .order_by('id')
    )
    if not es_superuser:
        if vendedor_filter:
            ventas_qs = ventas_qs.filter(vendedor=vendedor_filter)
        else:
            ventas_qs = ventas_qs.none()

    ventas_data: list[dict] = []
    total_vendido = Decimal('0')
    total_pagado_al_momento = Decimal('0')
    total_a_cuenta = Decimal('0')

    for v in ventas_qs:
        # precio_total ya considera descuentos por línea. Es property
        # que recorre las líneas, OK para 50-100 ventas/día.
        try:
            tot = Decimal(str(v.precio_total or 0))
        except Exception:
            tot = Decimal('0')
        # Pedido siempre existe (se crea con la Venta) — pero defensivo
        # por si alguna venta vieja quedó sin Pedido por bug histórico.
        try:
            pagado = bool(v.pedido and v.pedido.pagado)
        except Exception:
            pagado = False

        ventas_data.append({
            'id': v.id,
            'cliente': str(v.cliente) if v.cliente else '—',
            'vendedor': str(v.vendedor) if v.vendedor else '—',
            'total': tot,
            'pagado': pagado,
            'estado': v.pedido.estado if (hasattr(v, 'pedido') and v.pedido) else '',
        })
        total_vendido += tot
        if pagado:
            total_pagado_al_momento += tot
        else:
            total_a_cuenta += tot

    # ---- Pagos recibidos del día (MovimientoCuenta tipo='pago') ----
    # NO filtramos por vendedor — los pagos no tienen vendedor asociado.
    # Vendedor ve solo si es superuser (sino no aplica).
    pagos_data: list[dict] = []
    total_pagos = Decimal('0')
    if es_superuser:
        pagos_qs = (
            MovimientoCuenta.objects
            .filter(created_at__date=fecha, tipo=MovimientoCuenta.TIPO_PAGO)
            .select_related('cuenta__cliente', 'creado_por')
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
                'nota': p.nota if hasattr(p, 'nota') else '',
                'hora': timezone.localtime(p.created_at).strftime('%H:%M'),
                'creado_por': str(p.creado_por) if p.creado_por else '',
            })
            total_pagos += m

    efectivo_esperado = total_pagado_al_momento + total_pagos

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
        'total_pagado_al_momento': total_pagado_al_momento,
        'total_a_cuenta': total_a_cuenta,
        'total_pagos': total_pagos,
        'efectivo_esperado': efectivo_esperado,
    })
