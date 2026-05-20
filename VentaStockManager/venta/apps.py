from django.apps import AppConfig


class VentaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "venta"
    icon_name = "monetization_on"

    def ready(self):
        # Registramos los modelos en django-auditlog para que cada
        # create/update/delete quede registrado con el usuario que
        # lo hizo (gracias al AuditlogMiddleware).
        # NO trackeamos lecturas ni logins (ruido).
        from auditlog.registry import auditlog
        from venta.models import Venta, ArticuloVenta, Pedido, AlertaStock

        auditlog.register(Venta)
        auditlog.register(ArticuloVenta)
        auditlog.register(Pedido)
        # AlertaStock: las creaciones quedan registradas automáticamente
        # (sirve para entender quién aprobó qué). Las marcas de "revisada"
        # también, así sabemos quién la cerró.
        auditlog.register(AlertaStock)

        # Conectar signals: pre_delete devuelve stock al borrar
        # ArticuloVenta (incluso cuando es cascade por borrar la
        # Venta entera desde el admin).
        from venta import signals  # noqa: F401
