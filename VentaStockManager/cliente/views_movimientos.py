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
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

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
    if modo not in ('pago', 'deuda', 'dejar_en'):
        modo = 'pago'

    # Configuración visual del form según el modo. Toda en un dict para
    # que el template lea de un solo lugar (vs branching dentro del HTML).
    #
    # `dejar_en` es el modo "no quiero pensar en sumas/restas": el operador
    # tipea el saldo OBJETIVO al que quiere dejar al cliente (ej. 0 para
    # saldar, o un monto específico), y el sistema calcula el delta y
    # crea un AJUSTE con el signo correcto. Útil para la administradora
    # que cobra varios pedidos al mismo tiempo sin querer hacer cuentas.
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
        'dejar_en': {
            'titulo': '🎯 Dejar saldo en…',
            'subtitulo': 'Indicá el saldo final al que querés dejar al cliente. El sistema calcula y registra el ajuste.',
            'label_monto': 'Dejar saldo en',
            'help_monto': 'Saldo final del cliente después del ajuste. Negativo (ej. -1500) = el cliente debe; positivo (ej. 1500) = a favor; 0 = saldado.',
            'placeholder_monto': '0 para saldar',
            'label_nota': 'Nota (opcional)',
            'help_nota': 'Ej. "Cobró todo lo pendiente en mano" o "Cierre de cuenta".',
            'placeholder_nota': 'Ej. Cobrado en efectivo',
            'color_boton': 'indigo',
            'texto_boton': 'Aplicar ajuste',
        },
    }[modo]

    # ---- POST: guardar y redirigir ----
    if request.method == 'POST':
        monto_raw = (request.POST.get('monto') or '').strip().replace(',', '.')
        descripcion = (request.POST.get('descripcion') or '').strip()
        venta_id_raw = (request.POST.get('venta_id') or '').strip()

        # Parseo del monto. En modo `dejar_en` puede ser 0 o negativo
        # (saldo objetivo "debe X" = -X). Los otros modos exigen > 0.
        try:
            monto = Decimal(monto_raw)
        except (InvalidOperation, ValueError):
            messages.error(request, 'Monto inválido. Ingresá un número (ej. 5000).')
            return _render_form(request, cliente, cuenta, modo, config,
                                monto_raw, descripcion, venta_id_raw)
        if modo != 'dejar_en' and monto <= 0:
            messages.error(request, 'El monto tiene que ser mayor a 0.')
            return _render_form(request, cliente, cuenta, modo, config,
                                monto_raw, descripcion, venta_id_raw)

        # Determinar monto firmado + tipo + descripción según modo.
        venta = None
        if modo == 'dejar_en':
            # `monto` es el SALDO OBJETIVO. Delta = objetivo − saldo_actual.
            saldo_actual = cuenta.saldo
            delta = monto - saldo_actual
            if delta == 0:
                messages.warning(
                    request,
                    f'El cliente ya tiene saldo ${saldo_actual:,.2f}. No hay ajuste para hacer.',
                )
                return _render_form(request, cliente, cuenta, modo, config,
                                    monto_raw, descripcion, venta_id_raw)
            monto_signed = delta
            tipo = MovimientoCuenta.TIPO_AJUSTE
            # Descripcion auto-armada con traza completa, + nota libre del operador.
            traza = (
                f'Ajuste a saldo objetivo ${monto:,.2f} '
                f'(saldo previo ${saldo_actual:,.2f}, delta {"+" if delta > 0 else ""}{delta:,.2f})'
            )
            descripcion = f'{traza}. {descripcion}'.strip().rstrip('.')
        elif modo == 'deuda':
            monto_signed = -monto
            tipo = MovimientoCuenta.TIPO_AJUSTE
        else:  # 'pago'
            monto_signed = monto
            tipo = MovimientoCuenta.TIPO_PAGO

        # Resolver venta opcional (solo aplica a modo 'pago'). Para
        # `deuda` y `dejar_en` no asociamos a venta — son ajustes globales
        # de saldo, no de una venta puntual.
        if modo == 'pago' and venta_id_raw:
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

        # Mensaje de éxito específico al modo.
        if modo == 'dejar_en':
            msg = (
                f'✓ Saldo de {cliente.nombre_completo()} ajustado a '
                f'${monto:,.2f} (delta {"+" if monto_signed > 0 else ""}'
                f'{monto_signed:,.2f}).'
            )
        else:
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

    # Para Alpine x-data necesitamos un literal JS válido — Decimal con
    # USE_L10N puede renderizarse con coma y romper el parser. Lo serializamos
    # explícitamente con punto siempre, vía Python str(Decimal).
    saldo_js = f'{cuenta.saldo:.2f}'  # Decimal soporta f-string, siempre con '.'
    return render(request, 'cliente/registrar_movimiento.html', {
        'cliente': cliente,
        'cuenta': cuenta,
        'modo': modo,
        'cfg': config,
        'monto_inicial': monto_inicial,
        'descripcion_inicial': descripcion_inicial,
        'saldo_actual': cuenta.saldo,
        'saldo_actual_js': saldo_js,
        'ventas_impagas': ventas_impagas,
        'venta_id_sugerida': venta_id_sugerida,
    })


# ---------------------------------------------------------------------------
# Crear CuentaCliente (endpoint JSON)
# ---------------------------------------------------------------------------
@staff_member_required
@require_POST
def crear_cuenta_cliente(request: HttpRequest, cliente_id: int) -> HttpResponse:
    """
    Crea la CuentaCliente del cliente si no la tiene. Usado desde la
    pantalla "Cobrar y generar PDF" cuando un pedido cae a un cliente
    sin cuenta corriente.

    Al crearla, **arrastra las ventas pendientes pre-existentes** como
    movimientos `VENTA_A_CUENTA` (monto=-total). Eso es lo que pasaría
    si esas ventas hubieran sido creadas por el flujo nuevo
    (api_venta_guardar) — entran como deuda en el saldo.

    Sin este arrastre, las ventas pendientes "no contabilizadas" hacían
    que el cliente terminara con saldo a favor después de pagar (porque
    el PAGO entraba sin un debit que cancelar).

    Idempotente: si la cuenta ya existe, no toca nada y devuelve los
    datos actuales.
    """
    from venta.models import Venta
    from venta.utils import total_venta as calcular_total_venta
    from decimal import Decimal

    cliente = get_object_or_404(Cliente, pk=cliente_id)
    with transaction.atomic():
        cuenta, created = CuentaCliente.objects.get_or_create(cliente=cliente)
        ventas_arrastradas = 0
        deuda_arrastrada = Decimal('0')
        if created:
            # Iterar ventas pendientes (pedido.pagado=False) no archivadas
            # del cliente. Por cada una, crear el VENTA_A_CUENTA con el
            # total. Filtramos por seguridad las que ya tengan algún
            # movimiento asociado (no debería pasar, pero idempotente).
            ventas_pendientes = (
                Venta.objects
                .filter(cliente=cliente, pedido__pagado=False, archivada_en__isnull=True)
                .exclude(movimientos_cuenta__isnull=False)
                .distinct()
            )
            for v in ventas_pendientes:
                total = Decimal(calcular_total_venta(v) or 0)
                if total <= 0:
                    continue
                MovimientoCuenta.objects.create(
                    cuenta=cuenta,
                    tipo=MovimientoCuenta.TIPO_VENTA_A_CUENTA,
                    monto=-total,
                    venta=v,
                    descripcion=(
                        f'Arrastre al crear cuenta — venta #{v.id} '
                        f'pendiente pre-existente'
                    ),
                    creado_por=request.user if request.user.is_authenticated else None,
                )
                ventas_arrastradas += 1
                deuda_arrastrada += total
    return JsonResponse({
        'ok': True,
        'created': created,
        'cuenta_id': cuenta.pk,
        'cliente_id': cliente.pk,
        'saldo': str(cuenta.saldo),
        'ventas_arrastradas': ventas_arrastradas,
        'deuda_arrastrada': str(deuda_arrastrada),
    })
