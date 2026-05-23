"""
Pantalla custom para registrar pagos / deudas en la cuenta corriente.

Por qué fuera del admin:
  - El admin material-admin tiene un bug con readonly fields y inputs
    invisibles que pasamos 6 commits intentando arreglar sin éxito.
  - Una pantalla Alpine + Tailwind nos da CONTROL TOTAL del rendering:
    inputs visibles, validación en cliente, mensajes claros.
  - El operador piensa en "pago" o "deuda", no en signos contables.
    Esta UI lo refleja explícitamente.

Flujo:
  GET  /clientes/<id>/movimiento/?modo=pago|deuda
       → muestra el form con labels apropiados al modo + lista de
         ventas impagas del cliente (con preselección de la más reciente).
  POST /clientes/<id>/movimiento/?modo=pago|deuda
       → guarda el MovimientoCuenta con tipo+signo correcto, lo asocia
         a la venta indicada (si la hay), y si el pago cubre el total
         de la venta marca su Pedido como `pagado=True`. Redirige a la
         pantalla de cuenta del cliente.

Auth: requiere staff (mismo que el admin). No hay riesgo de acceso
público porque MovimientoCuenta es interno.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from cliente.models import Cliente, CuentaCliente, MovimientoCuenta
from venta.models import Venta


@staff_member_required
@require_http_methods(["GET", "POST"])
def registrar_movimiento(request: HttpRequest, cliente_id: int) -> HttpResponse:
    """
    Pantalla custom de "Registrar pago / deuda". Diseñada para evitar
    el bug visual del admin material-admin con inputs invisibles.

    El `?modo=` determina los labels y el signo del monto:
      - modo=pago (default)  → tipo=PAGO,   signo POSITIVO (suma al saldo)
      - modo=deuda            → tipo=AJUSTE, signo NEGATIVO (resta saldo)

    El operador siempre ingresa positivo — el signo lo aplica la view.
    """
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    # Auto-crear cuenta si no existe (defensivo, igual que en venta nueva).
    cuenta, _ = CuentaCliente.objects.get_or_create(cliente=cliente)

    modo = (request.GET.get('modo') or request.POST.get('modo') or 'pago').lower()
    if modo not in ('pago', 'deuda'):
        modo = 'pago'

    # Configuración visual del form según el modo. Toda en un dict para
    # que el template lea de un solo lugar (vs branching dentro del HTML).
    config = {
        'pago': {
            'titulo': '💰 Registrar pago',
            'subtitulo': 'El cliente trajo plata — se suma a su saldo (o cancela deuda).',
            'label_monto': 'Monto pagado',
            'help_monto': 'Cuánto pagó el cliente. Ingresá positivo (ej. 5000).',
            'placeholder_monto': 'Ej. 5000',
            'label_nota': 'Nota (opcional)',
            'help_nota': 'Ej. "Pago en efectivo del 22/05" o "Transferencia BBVA".',
            'placeholder_nota': 'Ej. Pago en efectivo del 22/05',
            'color_boton': 'emerald',  # verde
            'texto_boton': 'Guardar pago',
        },
        'deuda': {
            'titulo': '🧾 Registrar deuda',
            'subtitulo': 'El cliente debe más — se resta de su saldo.',
            'label_monto': 'Monto adeudado',
            'help_monto': 'Cuánto debe el cliente. Ingresá positivo — el sistema lo guarda como deuda.',
            'placeholder_monto': 'Ej. 5000',
            'label_nota': 'Motivo (opcional)',
            'help_nota': 'Ej. "Consumo a fiar" o "Anulación de pago erróneo del 21/05".',
            'placeholder_nota': 'Ej. Consumo a fiar',
            'color_boton': 'red',
            'texto_boton': 'Guardar deuda',
        },
    }[modo]

    # ---- POST: guardar y redirigir ----
    if request.method == 'POST':
        monto_raw = (request.POST.get('monto') or '').strip().replace(',', '.')
        descripcion = (request.POST.get('descripcion') or '').strip()
        venta_id_raw = (request.POST.get('venta_id') or '').strip()

        # Validar monto > 0.
        try:
            monto = Decimal(monto_raw)
        except (InvalidOperation, ValueError):
            messages.error(request, 'Monto inválido. Ingresá un número (ej. 5000).')
            return _render_form(request, cliente, cuenta, modo, config,
                                monto_raw, descripcion, venta_id_raw)
        if monto <= 0:
            messages.error(request, 'El monto tiene que ser mayor a 0.')
            return _render_form(request, cliente, cuenta, modo, config,
                                monto_raw, descripcion, venta_id_raw)

        # Aplicar signo según modo + setear tipo correcto.
        if modo == 'deuda':
            monto_signed = -monto
            tipo = MovimientoCuenta.TIPO_AJUSTE
        else:
            monto_signed = monto
            tipo = MovimientoCuenta.TIPO_PAGO

        # Resolver venta opcional. El FK MovimientoCuenta.venta es null=True
        # — si el operador no asoció a ninguna venta, lo guardamos como
        # movimiento genérico (afecta el saldo pero no marca venta pagada).
        venta = None
        if venta_id_raw:
            try:
                venta = Venta.objects.filter(
                    pk=int(venta_id_raw), cliente=cliente,
                ).first()
            except (ValueError, TypeError):
                venta = None

        with transaction.atomic():
            MovimientoCuenta.objects.create(
                cuenta=cuenta,
                tipo=tipo,
                monto=monto_signed,
                venta=venta,
                descripcion=descripcion,
                creado_por=request.user,
            )

            # Si es un PAGO con venta asociada, chequear si quedó cubierto
            # el total y marcar Pedido.pagado=True automáticamente.
            if venta and modo == 'pago':
                _marcar_pagado_si_cubre(venta)

        accion = 'pago' if modo == 'pago' else 'deuda'
        msg = (
            f'✓ {accion.capitalize()} de ${abs(monto_signed):,.2f} registrado '
            f'para {cliente.nombre_completo()}.'
        )
        if venta:
            msg += f' Asociado a la venta #{venta.id}.'
        messages.success(request, msg)

        # Volver a la pantalla de cuenta del cliente.
        return HttpResponseRedirect(
            f'/admin/cliente/cuentacliente/{cuenta.pk}/change/'
        )

    # ---- GET: mostrar el form ----
    return _render_form(request, cliente, cuenta, modo, config, '', '', '')


def _marcar_pagado_si_cubre(venta: Venta) -> None:
    """
    Después de crear un MovimientoCuenta asociado a una venta, sumar
    TODOS los pagos/aplicaciones de saldo aplicados a esa venta. Si el
    total cubre el precio_total de la venta → marcar Pedido.pagado=True.

    No tocamos el flag a False si quedan diferencias (por ej. el operador
    anula un pago con una deuda): eso requiere intervención manual
    porque la regla "qué cuenta vs no cuenta" es del negocio.
    """
    if not hasattr(venta, 'pedido') or venta.pedido.pagado:
        # Ya está marcada o no tiene pedido — nada que hacer.
        return

    total_venta = Decimal(venta.precio_total or 0)
    # Aplicar descuento global si tiene.
    if venta.descuento_porcentaje:
        descuento = total_venta * (Decimal(venta.descuento_porcentaje) / Decimal(100))
        total_a_cobrar = total_venta - descuento
    else:
        total_a_cobrar = total_venta

    # Sumar pagos aplicados a esta venta. monto > 0 = a favor del cliente
    # (le bajamos deuda). Solo cuenta tipos "que pagan": PAGO, APLICACION_SALDO.
    pagos = (
        MovimientoCuenta.objects
        .filter(
            venta=venta,
            tipo__in=[
                MovimientoCuenta.TIPO_PAGO,
                MovimientoCuenta.TIPO_APLICACION_SALDO,
            ],
        )
        .aggregate(s=Sum('monto'))
    )
    total_pagado = abs(Decimal(pagos['s'] or 0))

    if total_pagado >= total_a_cobrar > 0:
        venta.pedido.pagado = True
        venta.pedido.save(update_fields=['pagado'])


def _render_form(request, cliente, cuenta, modo, config,
                 monto_inicial, descripcion_inicial, venta_id_inicial):
    """
    Render del template con la lista de ventas impagas del cliente.

    Las "ventas impagas" son las que tienen pedido.pagado=False y NO
    están archivadas. Ordenadas por fecha desc para que la última quede
    arriba (el caso 95%: el cliente paga la venta más reciente).

    `venta_id_sugerida` es la más reciente impaga — el template la
    pre-selecciona en el dropdown.
    """
    ventas_impagas_qs = (
        Venta.objects
        .filter(
            cliente=cliente,
            pedido__pagado=False,
            archivada_en__isnull=True,
        )
        .select_related('pedido')
        .order_by('-fecha_compra', '-id')[:20]  # max 20 para no inflar dropdown
    )
    ventas_impagas = []
    for v in ventas_impagas_qs:
        total = Decimal(v.precio_total or 0)
        if v.descuento_porcentaje:
            total = total - (total * Decimal(v.descuento_porcentaje) / Decimal(100))
        ventas_impagas.append({
            'id': v.id,
            'fecha': v.fecha_compra,
            'total': total,
            'label': f'Venta #{v.id} · {v.fecha_compra.strftime("%d/%m/%y")} · ${total:,.2f}',
        })

    # Pre-selección: si el operador no eligió, usar la primera (más reciente).
    if venta_id_inicial:
        venta_id_sugerida = venta_id_inicial
    elif ventas_impagas:
        venta_id_sugerida = str(ventas_impagas[0]['id'])
    else:
        venta_id_sugerida = ''

    return render(request, 'cliente/registrar_movimiento.html', {
        'cliente': cliente,
        'cuenta': cuenta,
        'modo': modo,
        'cfg': config,
        'monto_inicial': monto_inicial,
        'descripcion_inicial': descripcion_inicial,
        'saldo_actual': cuenta.saldo,
        'ventas_impagas': ventas_impagas,
        'venta_id_sugerida': venta_id_sugerida,
    })
