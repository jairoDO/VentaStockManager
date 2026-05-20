from django.apps import AppConfig


class ClienteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cliente"
    icon_name = "account_circle"

    def ready(self):
        from auditlog.registry import auditlog
        from cliente.models import Cliente, CuentaCliente, MovimientoCuenta, PrecioCliente

        auditlog.register(Cliente)
        # CuentaCliente cambia poco (solo creación). Los movimientos
        # son la pieza interesante: cada uno deja huella en quién hizo
        # qué con la plata del cliente.
        auditlog.register(CuentaCliente)
        auditlog.register(MovimientoCuenta)
        # Cada cambio de precio pactado queda registrado con actor.
        # Útil para "¿quién le bajó el precio del alfajor a fulano?"
        auditlog.register(PrecioCliente)
