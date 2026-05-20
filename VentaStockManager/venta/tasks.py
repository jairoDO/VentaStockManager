"""
Tasks de la app venta para django-q2.

django-q2 invoca funciones Python referenciadas por su path
importable. Las funciones de este módulo son thin wrappers sobre
management commands o lógica de negocio que necesita correr
asíncronamente / en cron.
"""

from __future__ import annotations

import logging

from django.core.management import call_command

log = logging.getLogger(__name__)


def archivar_ventas_antiguas_scheduled() -> str:
    """
    Wrapper para el Schedule de django-q2.

    Invoca el management command `archivar_ventas_antiguas` con sus
    defaults. El command, a su vez, lee la cantidad de meses desde
    `ConfiguracionGeneral` (editable desde el admin), así que basta
    con que este Schedule corra periódicamente — la frecuencia no
    depende de cuántos meses configuró el operador.

    Devuelve un string con resultado para que django-q2 lo guarde
    como `result` del task (visible en /admin/django_q/success/).
    """
    log.info('Schedule: archivar_ventas_antiguas arranca')
    try:
        call_command('archivar_ventas_antiguas')
    except Exception as exc:
        log.exception('Schedule archivar_ventas_antiguas falló: %s', exc)
        # Re-raise para que django-q2 marque la task como fallida.
        raise
    return 'archivar_ventas_antiguas OK'
