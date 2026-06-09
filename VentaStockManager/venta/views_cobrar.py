"""
Pantalla intermedia "Registrar pago" — disparada desde la acción del
PedidoAdmin "💰 Registrar pago".

Flujo:
  - El operador selecciona N pedidos en el admin y elige la acción.
  - La acción redirige acá (GET con ?pedidos_ids=...) y se muestra una
    tabla con una fila por pedido: cliente, total venta, saldo actual,
    input "¿Cuánto pagó?" + nota.
  - **Idempotencia**: si el pedido ya tiene `monto_pagado != null`, la
    fila muestra "Ya registrado: $X" en lugar del input, y la acción
    NO duplica el movimiento aunque se vuelva a disparar por error.
  - POST: por cada fila con monto y SIN registro previo, crea un
    MovimientoCuenta(PAGO) y guarda `pedido.monto_pagado = monto`.

NO genera PDFs — eso es trabajo de la acción "Generar PDFs" separada.

Filosofía:
  - "Cobrar" es una decisión sensible (toca la cuenta corriente).
    Separar de "imprimir" minimiza el riesgo de cobros accidentales.
  - El campo `monto_pagado` del Pedido es la fuente de verdad de "este
    pedido ya se cobró por esta vía". Si está seteado, no se vuelve a
    procesar — para "des-cobrar" el operador debe limpiarlo a mano
    desde el admin de Pedido.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from cliente.models import CuentaCliente, MovimientoCuenta
from venta.models import Pedido
from venta.utils import total_venta as calcular_total_venta


def _parse_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for tok in (raw or '').split(','):
        tok = tok.strip()
        if not tok:
            continue
        try:
            ids.append(int(tok))
        except ValueError:
            continue
    return ids


@staff_member_required
@require_http_methods(['GET', 'POST'])
def registrar_pago_pedidos(request: HttpRequest) -> HttpResponse:
    pedidos_ids_raw = (
        request.POST.get('pedidos_ids') or request.GET.get('pedidos_ids') or ''
    )
    pedido_ids = _parse_ids(pedidos_ids_raw)
    if not pedido_ids:
        messages.error(request, 'No hay pedidos seleccionados.')
        return HttpResponseRedirect('/admin/venta/pedido/')

    pedidos_map = {
        p.id: p for p in Pedido.objects
        .select_related('venta__cliente__cuenta')
        .filter(id__in=pedido_ids)
    }
    pedidos = [pedidos_map[i] for i in pedido_ids if i in pedidos_map]
    if not pedidos:
        messages.error(request, 'Los pedidos seleccionados ya no existen.')
        return HttpResponseRedirect('/admin/venta/pedido/')

    # ---- POST: registrar/actualizar pagos ----
    # El helper set_monto_pagado es idempotente: si el monto no cambió,
    # no hace nada; si cambió, ACTUALIZA el PAGO existente (no duplica).
    # Esto permite que el operador edite un pago ya registrado desde
    # el mismo form sin tener que ir a otra pantalla.
    if request.method == 'POST':
        registrados = 0
        actualizados = 0
        sin_cambio = 0
        sin_monto = 0
        errores: list[str] = []
        total_cobrado = Decimal('0')

        with transaction.atomic():
            for pedido in pedidos:
                monto_raw = (
                    request.POST.get(f'monto_pagado_{pedido.id}') or ''
                ).strip().replace(',', '.')

                if monto_raw == '':
                    # Input vacío: si ya tenía pago, lo dejamos como está;
                    # si no tenía, lo saltamos.
                    if pedido.monto_pagado is None:
                        sin_monto += 1
                    else:
                        sin_cambio += 1
                    continue

                try:
                    monto = Decimal(monto_raw)
                except (InvalidOperation, ValueError):
                    errores.append(
                        f'Pedido #{pedido.id}: "{monto_raw}" no es un número válido.'
                    )
                    continue
                if monto < 0:
                    errores.append(
                        f'Pedido #{pedido.id}: el monto no puede ser negativo.'
                    )
                    continue

                # Delego en el helper: crea/actualiza/no-op según el caso.
                try:
                    user = request.user if request.user.is_authenticated else None
                    resultado = pedido.set_monto_pagado(monto, user=user)
                    if resultado.get('creado'):
                        registrados += 1
                        total_cobrado += monto
                    elif resultado.get('actualizado'):
                        actualizados += 1
                    else:
                        sin_cambio += 1
                except ValueError as e:
                    errores.append(f'Pedido #{pedido.id}: {e}')

        partes = []
        if registrados:
            partes.append(f'{registrados} pagos nuevos (${total_cobrado:,.2f})')
        if actualizados:
            partes.append(f'{actualizados} actualizados')
        if sin_cambio:
            partes.append(f'{sin_cambio} sin cambio')
        if sin_monto:
            partes.append(f'{sin_monto} sin monto cargado')
        if errores:
            partes.append(f'{len(errores)} con error')
        resumen = ' · '.join(partes) if partes else 'Sin cambios.'
        messages.success(request, f'✓ {resumen}')
        for err in errores:
            messages.warning(request, err)

        return HttpResponseRedirect('/admin/venta/pedido/')

    # ---- GET: armar la tabla ----
    filas = []
    for pedido in pedidos:
        venta = pedido.venta
        cliente = venta.cliente if venta else None
        if not cliente:
            continue
        total = Decimal(calcular_total_venta(venta) or 0) if venta else Decimal('0')
        cuenta = CuentaCliente.objects.filter(cliente=cliente).first()
        tiene_cuenta = cuenta is not None
        saldo = cuenta.saldo if cuenta else Decimal('0')

        # Si la venta ya tiene una VENTA_A_CUENTA, asumimos que está
        # reflejada en el saldo (deuda_pendiente=0). Si no, asumimos
        # que es deuda nueva no contabilizada (deuda_pendiente=total).
        venta_ya_en_saldo = bool(
            venta and MovimientoCuenta.objects.filter(
                venta=venta, tipo=MovimientoCuenta.TIPO_VENTA_A_CUENTA,
            ).exists()
        )
        deuda_pendiente = Decimal('0') if venta_ya_en_saldo else total

        # Pedido con pago previo: pre-cargamos el input con ese monto
        # para que el operador pueda editarlo en lugar de re-tipearlo.
        # `set_monto_pagado` es idempotente — si no cambia, no-op; si
        # cambia, actualiza el PAGO existente sin duplicar.
        ya_registrado = pedido.monto_pagado is not None
        monto_default = (
            f'{pedido.monto_pagado:.2f}' if ya_registrado else f'{total:.2f}'
        )

        filas.append({
            'pedido': pedido,
            'venta': venta,
            'cliente': cliente,
            'total_venta': f'{total:.2f}',
            'saldo_actual': f'{saldo:.2f}',
            'saldo_actual_display': saldo,
            'deuda_pendiente_venta': f'{deuda_pendiente:.2f}',
            'deuda_total_str': f'{abs(saldo):.2f}' if saldo < 0 else '0.00',
            'fecha_compra': venta.fecha_compra if venta else None,
            'tiene_cuenta': tiene_cuenta,
            'ya_registrado': ya_registrado,
            'monto_default': monto_default,
        })

    return render(request, 'venta/registrar_pago_pedidos.html', {
        'filas': filas,
        'pedidos_ids_csv': ','.join(str(p.id) for p in pedidos),
        'total_filas': len(filas),
    })
