"""
Detección de clientes inactivos — alerta INTERNA (no WhatsApp).

Schedule django-q2 que corre una vez por día y detecta clientes que
SOLÍAN comprar pero dejaron de hacerlo por más de N días (configurable,
default 30). Por cada uno genera una `AlertaClienteInactivo` visible en
el admin, para que Osvaldo pueda recontactarlos a mano.

Reglas de elegibilidad (TODAS tienen que cumplirse):
  - El cliente tiene al menos una venta registrada (`ultima_compra`
    no es null). Un cliente que nunca compró NO está "inactivo" —
    simplemente nunca fue cliente activo. Este es el alcance que el
    operador confirmó: "solo los que compraron antes".
  - Su última compra fue hace MÁS de `alerta_inactividad_dias` días.
  - El master flag `alerta_inactividad_habilitada` está prendido.

Anti-spam / idempotencia: solo hay UNA alerta pendiente
(revisada=False) por cliente a la vez. La task se puede correr N veces
por día y no duplica alertas: si ya existe una sin revisar para el
cliente, la saltea.

Auto-resolución: cuando el cliente vuelve a comprar, la venta marca
como revisadas todas sus alertas pendientes (ver `Venta.save`). Por eso
la task no necesita "cerrar" alertas viejas — eso lo dispara la compra.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone


log = logging.getLogger(__name__)


def procesar_clientes_inactivos() -> dict:
    """
    Worker entry-point.

    Devuelve un dict con métricas para auditoría:
      {ok, candidatos, alertas_creadas, ya_alertados, motivo_skip?}
    """
    from configuracion.models import get_config
    from cliente.models import Cliente, AlertaClienteInactivo

    config = get_config()

    if not config.alerta_inactividad_habilitada:
        msg = 'alerta_inactividad_habilitada=False — NO-OP.'
        log.info(msg)
        return {'ok': True, 'candidatos': 0, 'alertas_creadas': 0, 'motivo_skip': msg}

    dias_umbral = int(config.alerta_inactividad_dias or 30)
    # USE_TZ=False → timezone.now() es naive; .date() es la fecha local.
    hoy = timezone.now().date()
    desde = hoy - timedelta(days=dias_umbral)

    qs = (
        Cliente.objects
        .annotate(ultima_compra=Max('ventas__fecha_compra'))
        # "Compraron antes": ultima_compra no es null.
        .filter(ultima_compra__isnull=False)
        # Inactivos: última compra anterior al umbral.
        .filter(ultima_compra__lt=desde)
    )

    candidatos = 0
    creadas = 0
    ya_alertados = 0

    for cliente in qs.iterator():
        candidatos += 1

        # Anti-spam: si ya hay una alerta pendiente para este cliente,
        # no creamos otra.
        ya_pendiente = AlertaClienteInactivo.objects.filter(
            cliente=cliente,
            revisada=False,
        ).exists()
        if ya_pendiente:
            ya_alertados += 1
            continue

        dias_inactivo = (hoy - cliente.ultima_compra).days
        AlertaClienteInactivo.objects.create(
            cliente=cliente,
            ultima_compra=cliente.ultima_compra,
            dias_inactivo=dias_inactivo,
        )
        creadas += 1

    config.alerta_inactividad_ultima_corrida_at = timezone.now()
    config.save(update_fields=['alerta_inactividad_ultima_corrida_at'])

    return {
        'ok': True,
        'candidatos': candidatos,
        'alertas_creadas': creadas,
        'ya_alertados': ya_alertados,
    }


# ---------------------------------------------------------------------------
# Wrapper para django-q2 Schedule / panel de tareas
# ---------------------------------------------------------------------------
def clientes_inactivos_scheduled() -> str:
    """
    Entry-point para django-q2 Schedule + panel de tareas manuales.
    Devuelve un string-summary para que se vea cómodo en
    `/admin/django_q/success/`.
    """
    log.info('Schedule clientes_inactivos arranca')
    res = procesar_clientes_inactivos()
    if not res.get('ok'):
        return f'FAIL: {res.get("error") or res.get("motivo_skip")}'
    if res.get('motivo_skip'):
        return f'NO-OP: {res["motivo_skip"]}'
    return (
        f'OK. candidatos={res.get("candidatos", 0)} '
        f'alertas_creadas={res.get("alertas_creadas", 0)} '
        f'ya_alertados={res.get("ya_alertados", 0)}'
    )
