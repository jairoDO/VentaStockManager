from django.apps import AppConfig


class ArticuloConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "articulo"
    icon_name = "local_play"
    verbose_name = "Articulos"

    def ready(self):
        from auditlog.registry import auditlog
        from articulo.models import (
            Articulo, Categoria, ListaPrecios, ListaPreciosItem, ReglaCategoria,
        )

        auditlog.register(Articulo)
        # Auditar cambios en categorías y reglas — útil para entender
        # por qué se reasignaron artículos sin que nadie se acuerde.
        auditlog.register(Categoria)
        auditlog.register(ReglaCategoria)
        # Listas de precios: nos importa saber quién/cuándo cambió
        # el descuento o los items (especialmente si Osvaldo después
        # dice "yo no le di ese descuento al cliente X").
        auditlog.register(ListaPrecios)
        auditlog.register(ListaPreciosItem)

        # Conectar signals (post_delete → sync a Sheets).
        # Import lazy para que Django registre los receivers solo
        # cuando el app está listo.
        from articulo import signals  # noqa: F401
