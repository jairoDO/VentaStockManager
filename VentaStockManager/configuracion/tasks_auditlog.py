"""
Task wrapper para que django-q2 invoque la purga de auditlog
periódicamente. Es delegate-thin: solo llama al management command,
que tiene toda la lógica.

Por qué split en management command + task wrapper:
  - El command es ejecutable a mano (debug, testing, panel de tareas).
  - El wrapper es lo que django-q registra en su Schedule.
  - Si en el futuro la lógica cambia, un solo lugar para tocar.

NO hace nada si `auditlog_purge_habilitado=False` en el singleton —
ese check vive en el management command. El wrapper siempre llama.
"""
from __future__ import annotations

import logging

from django.core.management import call_command


log = logging.getLogger(__name__)


def purgar_auditlog_scheduled() -> str:
    """
    Entry-point para django-q2 Schedule + panel de tareas manuales.

    Devuelve un string descriptivo del resultado (django-q2 lo guarda
    en Task.result, visible desde /admin/django_q/success/).
    """
    log.info('Schedule purgar_auditlog arranca')
    try:
        # call_command captura el output del command. Si todo va bien,
        # devuelve None y el command imprime su propio mensaje.
        call_command('purgar_auditlog_antiguos')
        return 'purga ejecutada (ver ConfiguracionGeneral para detalle)'
    except Exception as e:
        log.exception('Error en purgar_auditlog_scheduled')
        return f'ERROR: {e}'
