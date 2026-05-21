from django.apps import AppConfig


class ConfiguracionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'configuracion'
    verbose_name = 'Configuración'
    icon_name = 'tune'

    def ready(self):
        # Auditar cambios a la config — útil para saber quién bajó
        # la retención a 3 meses cuando aparezca alguien diciendo
        # "¿dónde están las ventas viejas?".
        from auditlog.registry import auditlog
        from configuracion.models import ConfiguracionGeneral
        auditlog.register(ConfiguracionGeneral)

        # Cargar el system check que detecta comentarios {# ... #}
        # multi-línea (bug recurrente — ya pasó 5 veces). Importar el
        # módulo es suficiente: el decorador `@checks.register` adentro
        # del archivo lo deja registrado en Django global.
        # Ver `configuracion/checks.py` para detalles.
        from configuracion import checks as _checks  # noqa: F401
