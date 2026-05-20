from django.apps import AppConfig


class WaCampaniaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'wa_campania'
    verbose_name = 'Campañas de WhatsApp'
    # Icono del admin material para que aparezca al lado del nombre.
    icon_name = 'campaign'

    def ready(self):
        # Auditoría: registrar quién crea/edita/borra campañas y
        # quién dispara envíos. Los EnvioWhatsapp se actualizan por
        # el worker (con actor=None) — eso está bien, lo importante
        # es trazar la decisión humana en la Campania.
        from auditlog.registry import auditlog
        from wa_campania.models import Campania, EnvioWhatsapp

        auditlog.register(Campania)
        auditlog.register(EnvioWhatsapp)
