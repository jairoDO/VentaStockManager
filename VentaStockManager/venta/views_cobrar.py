"""
Pantalla intermedia para "Generar PDFs y registrar pago" — disparada
desde la acción del PedidoAdmin.

Flujo:
  - El operador selecciona N pedidos en el admin y elige la acción
    "💰 Generar PDFs y registrar pago".
  - La acción redirige acá (GET con ?pedidos_ids=...) y se muestra una
    tabla con una fila por pedido: cliente, total venta, saldo actual,
    e input "Dejar saldo en" + nota opcional.
  - El operador llena los valores que quiera para cada cliente.
  - POST: por cada fila se crea un MovimientoCuenta de tipo AJUSTE para
    dejar el saldo del cliente en el valor objetivo, se marca el pedido
    como pagado, y al final redirige a la generación de PDFs (que ya
    existe).

Por qué pantalla intermedia y no acción automática:
  - Pocas veces el operador que cobra es el mismo que cargó la venta.
  - La administradora quiere DECIDIR cuánto deja al cliente (puede dejar
    saldo a favor, puede dejar deuda parcial, puede saldar). El cobro
    automático (todo o nada) no le sirve.

Idempotencia: si el operador cierra el form sin enviar, no se aplica
nada. Si lo envía dos veces, cada submit aplica un AJUSTE — el segundo
casi siempre va a ser 0 (porque el saldo ya está donde lo dejó), pero
en cualquier caso queda registrado en el historial.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from cliente.models import CuentaCliente, MovimientoCuenta
from venta.models import Pedido
from venta.utils import total_venta as calcular_total_venta


def _parse_ids(raw: str) -> list[int]:
    """Convierte '1,2,3' → [1, 2, 3], saltando lo que no sea entero."""
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
def cobrar_y_generar_pdf(request: HttpRequest) -> HttpResponse:
    pedidos_ids_raw = (
        request.POST.get('pedidos_ids') or request.GET.get('pedidos_ids') or ''
    )
    pedido_ids = _parse_ids(pedidos_ids_raw)
    if not pedido_ids:
        messages.error(request, 'No hay pedidos seleccionados.')
        return HttpResponseRedirect('/admin/venta/pedido/')

    # Cargar pedidos en el ORDEN del query string (orden estable para
    # que el operador vea las filas igual que las seleccionó en el admin).
    pedidos_map = {
        p.id: p for p in Pedido.objects
        .select_related('venta__cliente__cuenta')
        .filter(id__in=pedido_ids)
    }
    pedidos = [pedidos_map[i] for i in pedido_ids if i in pedidos_map]
    if not pedidos:
        messages.error(request, 'Los pedidos seleccionados ya no existen.')
        return HttpResponseRedirect('/admin/venta/pedido/')

    # ---- POST: aplicar ajustes + redirigir al PDF ----
    if request.method == 'POST':
        aplicados = 0
        sin_cambio = 0
        errores: list[str] = []
        total_movido = Decimal('0')

        with transaction.atomic():
            for pedido in pedidos:
                cliente = pedido.venta.cliente if pedido.venta else None
                if not cliente:
                    errores.append(f'Pedido #{pedido.id}: sin cliente, no se procesó.')
                    continue

                dejar_raw = (
                    request.POST.get(f'dejar_en_{pedido.id}') or ''
                ).strip().replace(',', '.')
                nota = (request.POST.get(f'nota_{pedido.id}') or '').strip()

                if dejar_raw == '':
                    # El operador dejó la fila vacía → no aplicar ajuste, pero
                    # SÍ marcar pedido como pagado igual (asume que el cobro fue
                    # gestionado por otra vía). Si no quería ni eso, debería
                    # deseleccionarlo en el admin.
                    pedido.pagado = True
                    pedido.save(update_fields=['pagado'])
                    sin_cambio += 1
                    continue

                try:
                    objetivo = Decimal(dejar_raw)
                except (InvalidOperation, ValueError):
                    errores.append(
                        f'Pedido #{pedido.id} ({cliente.nombre_completo()}): '
                        f'"{dejar_raw}" no es un número válido.'
                    )
                    continue

                cuenta, _ = CuentaCliente.objects.select_for_update().get_or_create(
                    cliente=cliente,
                )
                saldo_actual = cuenta.saldo
                delta = objetivo - saldo_actual

                if delta != 0:
                    traza = (
                        f'Cobro al generar comanda — saldo objetivo ${objetivo:,.2f} '
                        f'(previo ${saldo_actual:,.2f}, delta {"+" if delta > 0 else ""}{delta:,.2f}) '
                        f'· venta #{pedido.venta_id}'
                    )
                    desc = f'{traza}. {nota}'.strip().rstrip('.')
                    MovimientoCuenta.objects.create(
                        cuenta=cuenta,
                        tipo=MovimientoCuenta.TIPO_AJUSTE,
                        monto=delta,
                        venta=pedido.venta,
                        descripcion=desc,
                        creado_por=request.user if request.user.is_authenticated else None,
                    )
                    total_movido += abs(delta)
                    aplicados += 1
                else:
                    sin_cambio += 1

                pedido.pagado = True
                pedido.save(update_fields=['pagado'])

        # Resumen al operador.
        partes = []
        if aplicados:
            partes.append(f'{aplicados} ajustes aplicados (${total_movido:,.2f} movidos)')
        if sin_cambio:
            partes.append(f'{sin_cambio} marcados pagados sin cambio de saldo')
        if errores:
            partes.append(f'{len(errores)} con error')
        resumen = ' · '.join(partes) if partes else 'Sin cambios.'
        messages.success(request, f'✓ {resumen}')
        for err in errores:
            messages.warning(request, err)

        # Disparar PDFs (reusa la URL existente).
        ids_csv = ','.join(str(p.id) for p in pedidos)
        return HttpResponseRedirect(
            reverse('generar_pdf_pedidos') + f'?pedidos_ids={ids_csv}'
        )

    # ---- GET: armar la tabla con saldo actual + total venta ----
    filas = []
    for pedido in pedidos:
        venta = pedido.venta
        cliente = venta.cliente if venta else None
        if not cliente:
            continue
        total = Decimal(calcular_total_venta(venta) or 0) if venta else Decimal('0')
        # get_or_create defensivo — algunos clientes legacy podían no
        # tener CuentaCliente. Igual lo creamos para que .saldo no rompa.
        cuenta, _ = CuentaCliente.objects.get_or_create(cliente=cliente)
        saldo = cuenta.saldo
        # Default sugerido: si el cliente paga TODO ahora (incluyendo lo
        # que ya debía), querría dejar el saldo en lo que tenga PRE-venta.
        # Pero como esa lógica es ambigua, el default más natural es 0
        # (cliente queda al día). El operador la cambia si quiere.
        filas.append({
            'pedido': pedido,
            'venta': venta,
            'cliente': cliente,
            'total_venta': f'{total:.2f}',
            'saldo_actual': f'{saldo:.2f}',
            'saldo_actual_display': saldo,
            'fecha_compra': venta.fecha_compra if venta else None,
            # Sugerimos `0` (saldar) como default para "cobré todo".
            'dejar_en_default': '0',
        })

    return render(request, 'venta/cobrar_y_generar_pdf.html', {
        'filas': filas,
        'pedidos_ids_csv': ','.join(str(p.id) for p in pedidos),
        'total_filas': len(filas),
    })
