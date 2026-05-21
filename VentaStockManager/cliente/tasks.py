"""
Tareas async / wrappers para django-q2 + panel de tareas manuales.

Mantenemos UNA fuente de verdad: la lógica vive en `management/commands/*`
(ejecutable desde la CLI) y estos wrappers solo la disparan vía
`call_command()` para que el panel y los schedules de django-q2 usen
exactamente el mismo path.
"""

from __future__ import annotations

import logging

from django.core.management import call_command


log = logging.getLogger(__name__)


def backfill_whatsapp_number_scheduled() -> str:
    """
    Wrapper para el panel de tareas / django-q2 Schedule.

    Re-corre el backfill de `telefono` → `whatsapp_number` sobre los
    clientes que aún tienen `whatsapp_number=''`. Idempotente: solo
    completa los vacíos, nunca pisa lo cargado a mano.

    Útil cuando importás un dump nuevo o cuando notás que la pantalla
    de Difundir no muestra clientes que sí tienen teléfono.
    """
    log.info('Schedule: backfill_whatsapp_number arranca')
    try:
        call_command('backfill_whatsapp_number')
    except Exception as exc:
        log.exception('backfill_whatsapp_number falló: %s', exc)
        raise
    return 'backfill_whatsapp_number OK'
