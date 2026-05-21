"""
Signals de la app articulo.

Por ahora solo: cuando se borra un Articulo en Django, encolamos un
job que vacía la fila correspondiente en el Google Sheet. Esto cierra
el ciclo del sync que hasta ahora era unidireccional (Sheets → DB).

Seguridad:
  - El sync de delete está OFF por default. Hay que prender los flags
    `sheets_sync_habilitado` y `sheets_delete_sync_habilitado` en
    `ConfiguracionGeneral` (admin: /admin/configuracion/) para activarlo.
    Por qué: en desarrollo local apuntamos al MISMO Sheet de
    producción, y un delete accidental ahí sería destructivo. En
    staging/producción se prende cuando esté validado.
  - Aún prendido, el job se encola en django-q2: el delete del modelo
    en Django nunca espera la respuesta de Sheets. Si Sheets falla,
    queda en logs/errors de django-q2 pero el delete en DB se
    consumó igual.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from articulo.models import Articulo

log = logging.getLogger(__name__)


@receiver(post_delete, sender=Articulo)
def articulo_borrado_a_sheets(sender, instance, **kwargs):
    """
    Encola el vaciado de la fila correspondiente en el Sheet.

    Solo dispara si:
      - El master `sheets_sync_habilitado` está en True (singleton).
      - El específico `sheets_delete_sync_habilitado` también en True.
      - El artículo tiene `codigo_interno` (es la clave de búsqueda
        en el Sheet — si está vacío no podemos encontrar la fila).
    """
    # Doble gate desde el singleton ConfiguracionGeneral. Antes esto
    # vivía en env vars (SHEETS_SYNC_ENABLED, SHEETS_DELETE_SYNC_ENABLED)
    # pero migramos al singleton para que se prenda/apague desde
    # /admin/configuracion/ sin redeploy.
    #
    # Import local: configuracion importa cosas que no queremos cargar
    # al boot temprano si articulo.signals se conecta antes de que
    # configuracion esté listo en apps.py.
    from configuracion.models import get_config
    cfg = get_config()
    if not cfg.sheets_sync_habilitado:
        return
    if not cfg.sheets_delete_sync_habilitado:
        return
    if not instance.codigo_interno:
        log.info(
            'articulo %s borrado sin codigo_interno, no sync a Sheets',
            instance.pk,
        )
        return

    # Import local: en algunos entornos django-q2 puede no estar
    # configurado todavía (tests, scripts ad-hoc) y no queremos que
    # el import explote al cargar `signals.py`.
    try:
        from django_q.tasks import async_task
    except ImportError:
        log.warning('django_q no disponible — no se encola delete a Sheets')
        return

    async_task(
        'articulo.tasks.sync_borrar_articulo_de_sheets',
        instance.codigo_interno,
        instance.nombre or '',
    )
