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
       → muestra el form con labels apropiados al modo.
  POST /clientes/<id>/movimiento/?modo=pago|deuda
       → guarda el MovimientoCuenta con tipo+signo correcto,
         redirige a la pantalla de cuenta corriente.

Auth: requiere staff (mismo que el admin). No hay riesgo de acceso
público porque MovimientoCuenta es interno.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from cliente.models import Cliente, CuentaCliente, MovimientoCuenta


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

        # Validar monto > 0.
        try:
            monto = Decimal(monto_raw)
        except (InvalidOperation, ValueError):
            messages.error(request, 'Monto inválido. Ingresá un número (ej. 5000).')
            return _render_form(request, cliente, cuenta, modo, config, monto_raw, descripcion)
        if monto <= 0:
            messages.error(request, 'El monto tiene que ser mayor a 0.')
            return _render_form(request, cliente, cuenta, modo, config, monto_raw, descripcion)

        # Aplicar signo según modo + setear tipo correcto.
        if modo == 'deuda':
            monto_signed = -monto
            tipo = MovimientoCuenta.TIPO_AJUSTE
        else:
            monto_signed = monto
            tipo = MovimientoCuenta.TIPO_PAGO

        # Transaction defensiva — pago + cuenta.save no deberían
        # tener efectos secundarios pero por consistencia.
        with transaction.atomic():
            MovimientoCuenta.objects.create(
                cuenta=cuenta,
                tipo=tipo,
                monto=monto_signed,
                descripcion=descripcion,
                creado_por=request.user,
            )

        accion = 'pago' if modo == 'pago' else 'deuda'
        messages.success(
            request,
            f'✓ {accion.capitalize()} de ${abs(monto_signed):,.2f} registrado para '
            f'{cliente.nombre_completo()}.',
        )
        # Volver a la pantalla de cuenta del cliente.
        return HttpResponseRedirect(
            f'/admin/cliente/cuentacliente/{cuenta.pk}/change/'
        )

    # ---- GET: mostrar el form ----
    return _render_form(request, cliente, cuenta, modo, config, '', '')


def _render_form(request, cliente, cuenta, modo, config, monto_inicial, descripcion_inicial):
    """Render del template — extraído para no duplicar entre GET y POST con error."""
    return render(request, 'cliente/registrar_movimiento.html', {
        'cliente': cliente,
        'cuenta': cuenta,
        'modo': modo,
        'cfg': config,
        'monto_inicial': monto_inicial,
        'descripcion_inicial': descripcion_inicial,
        'saldo_actual': cuenta.saldo,
    })
