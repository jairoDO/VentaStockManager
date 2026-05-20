"""
Signals de la app venta.

Lo importante: `articulo_venta_devolver_stock` se dispara cuando se
borra un `ArticuloVenta` (manualmente desde admin, o por cascade al
borrar la Venta entera). Devuelve la cantidad al stock del artículo
para mantener la invariante "stock = lo que hay físicamente".

Diseño:
  - Usamos `pre_delete` en lugar de `post_delete` porque post-delete
    el FK al artículo puede haber quedado roto (si el artículo se
    borró antes — caso raro pero posible con PROTECT en medio).
  - Guard `bypass_stock_restore`: la pantalla nueva ya maneja el stock
    a mano cuando borra un item (vía la API). Setea un flag en la
    instancia para que el signal NO duplique la operación. Si en algún
    momento alguien borra directamente desde shell o admin sin esa
    flag, el signal hace el trabajo correctamente.
"""

from __future__ import annotations

import logging

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from venta.models import ArticuloVenta

log = logging.getLogger(__name__)


@receiver(pre_delete, sender=ArticuloVenta)
def articulo_venta_devolver_stock(sender, instance, **kwargs):
    """
    Devuelve la cantidad al stock del artículo antes del delete.

    Casos cubiertos:
      - Operador borra una Venta entera desde el admin → cascade
        borra todos sus ArticuloVenta → este signal devuelve stock
        por cada uno.
      - Operador borra un ArticuloVenta puntual desde el shell.
      - Cualquier otro delete que pase por el ORM.

    Caso NO cubierto (y aceptado): bulk DELETE con
    `ArticuloVenta.objects.filter(...).delete()` SÍ dispara el signal
    en Django 4.2 (signals on bulk delete son emitidos en cada fila),
    pero un raw SQL DELETE no. No deberíamos usar raw SQL para esto.
    """
    # La pantalla nueva (api_venta_guardar) ya devuelve el stock a
    # mano cuando borra una línea desde la UI. Marca la instancia con
    # `_skip_stock_restore=True` ANTES de llamar a `.delete()` para
    # decirle a este signal que no haga nada (sino devolveríamos
    # stock dos veces).
    if getattr(instance, '_skip_stock_restore', False):
        return

    if not instance.articulo_id or not instance.cantidad:
        return

    # Cargamos el artículo fresco. `instance.articulo` podría estar
    # cacheado o haberse borrado en el mismo request (raro, pero por
    # las dudas).
    from articulo.models import Articulo
    try:
        articulo = Articulo.objects.select_for_update().get(pk=instance.articulo_id)
    except Articulo.DoesNotExist:
        log.warning(
            'pre_delete ArticuloVenta %s: articulo %s ya no existe, '
            'no se puede devolver stock',
            instance.pk, instance.articulo_id,
        )
        return

    articulo.stock = (articulo.stock or 0) + instance.cantidad
    articulo.save(update_fields=['stock'])
    log.info(
        'Stock devuelto: articulo %s +%s (de ArticuloVenta %s)',
        articulo.id, instance.cantidad, instance.pk,
    )
