from material.admin.sites import MaterialAdminSite
from venta.admin import VentaAdmin, PedidoAdmin, AlertaStockAdmin
from articulo.admin import ArticuloAdmin
from cliente.admin import ClienteAdmin, CuentaClienteAdmin, MovimientoCuentaAdmin, PrecioClienteAdmin
from compra.admin import ProvedorAdmin, CompraAdmin
from venta.models import Venta, Pedido, AlertaStock
from articulo.models import Articulo
from cliente.models import Cliente, CuentaCliente, MovimientoCuenta, PrecioCliente
from compra.models import Proveedor, Compra
from django.apps import apps
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
import logging
from factura_config.models import FacturaConfiguration
from factura_config.admin import FacturaConfigurationAdmin


class MyAdminSite(MaterialAdminSite):
    def each_context(self, request):
        """
        Inyecta variables que están disponibles en TODOS los templates
        del admin. La usamos para el badge "⚠ N alertas pendientes"
        que se muestra en el header (override del block branding en
        cliente/templates/admin/base_site.html).

        El count solo se calcula para usuarios staff — no queremos
        pegarle a la DB en cada request de un anónimo. La query es
        liviana porque AlertaStock tiene índice sobre `revisada`.
        """
        ctx = super().each_context(request)
        if request.user.is_authenticated and request.user.is_staff:
            try:
                from venta.models import AlertaStock
                ctx['alertas_stock_pendientes'] = (
                    AlertaStock.objects.filter(revisada=False).count()
                )
            except Exception:
                # Si la tabla todavía no existe (caso muy raro: bootstrap
                # con DB nueva y sin migrar), no rompemos el admin.
                ctx['alertas_stock_pendientes'] = 0
        return ctx

    def get_app_list(self, request, app_label=None):
        # OJO con `_build_app_dict`:
        #   - con `app_label=None` devuelve `{label: app_data, ...}`
        #   - con `app_label='foo'` devuelve directamente `app_data`
        # El código viejo asumía siempre el primer caso e iteraba
        # `.values()`, lo que rompía la vista `/admin/<app>/` (el
        # breadcrumb del medio) con 404 — los `.values()` de un solo
        # `app_data` son listas/strings, no dicts, y el filtro los
        # tiraba a todos. Normalizamos a lista de app_data para
        # ambos casos.
        app_dict = self._build_app_dict(request, app_label)
        if app_label is not None:
            # Si la app no existe (o el user no tiene permiso),
            # `_build_app_dict` devuelve {} — respetamos.
            apps_iter = [app_dict] if app_dict else []
        else:
            apps_iter = list(app_dict.values())

        valid_apps = []
        for app in apps_iter:
            if not isinstance(app, dict) or "name" not in app:
                logging.error(f"Invalid app_dict structure: {app}")
                continue
            valid_apps.append(app)

        app_list = sorted(valid_apps, key=lambda x: x["name"].lower())

        # Add icons to the app list
        for app in app_list:
            app_config = apps.get_app_config(app['app_label'])
            app['icon'] = getattr(app_config, 'icon_name', 'default_icon')

        return app_list
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

UserAdmin.icon_name = "person"

admin_site = MyAdminSite()

admin_site.site_header = format_html(
    'Osvaldo Administrator - <span class="text-primary">Precios<button class="btn btn-primary" onclick="window.location.href=\'https://jairodo.pythonanywhere.com/lista_precios\'"><a class="pl-4 ml-4 material-icons" title="Ir a la lista de precios">arrow_forward</a></button><button class="btn btn-secondary" onclick="navigator.clipboard.writeText(\'https://jairodo.pythonanywhere.com/lista_precios\')"><a class="mb-2 material-icons" title="Copiar link lista de precios">content_copy</a></button></span>'
)
admin_site.index_title = 'Osvaldo Administrador '
admin_site.site_title = 'Osvaldo Programs'

admin_site.register(User, UserAdmin)
admin_site.register(Venta, VentaAdmin)
admin_site.register(Pedido, PedidoAdmin)
# Alertas de stock: bandeja de entrada de "vendí algo y no había en
# stock" para que la administración las investigue y marque revisadas.
admin_site.register(AlertaStock, AlertaStockAdmin)
admin_site.register(Articulo, ArticuloAdmin)
# Categorías de artículos y reglas de auto-asignación. Es metadata
# local — no se sincroniza a Google Sheets.
from articulo.models import Categoria, ListaPrecios, ReglaCategoria
from articulo.admin import CategoriaAdmin, ListaPreciosAdmin, ReglaCategoriaAdmin
admin_site.register(Categoria, CategoriaAdmin)
admin_site.register(ReglaCategoria, ReglaCategoriaAdmin)
# Listas de precios personalizadas por cliente (se exportan a PDF
# y se mandan por WhatsApp). El alta a mano es por acá; la pantalla
# custom con filtros y "agregar por categoría" viene en próxima fase.
admin_site.register(ListaPrecios, ListaPreciosAdmin)
admin_site.register(Cliente, ClienteAdmin)
admin_site.register(Proveedor, ProvedorAdmin)
admin_site.register(Compra, CompraAdmin)
admin_site.register(FacturaConfiguration, FacturaConfigurationAdmin)
# Cuenta corriente: una entrada en el menú para listar cuentas y
# otra para ver movimientos planos. El alta de pagos pasa por
# CuentaCliente → inline de movimientos.
admin_site.register(CuentaCliente, CuentaClienteAdmin)
admin_site.register(MovimientoCuenta, MovimientoCuentaAdmin)
# Precios pactados por cliente: nacen automáticamente cada vez que
# el operador edita el precio de un artículo en una venta. También
# se pueden cargar a mano desde acá.
admin_site.register(PrecioCliente, PrecioClienteAdmin)

# Campañas de WhatsApp. SOLO superusers pueden ver/usar (la mixin
# de los admins se ocupa de filtrar, pero registramos siempre para
# que el modelo sea accesible vía las URLs del admin).
from wa_campania.models import Campania, EnvioWhatsapp
from wa_campania.admin import CampaniaAdmin, EnvioWhatsappAdmin
admin_site.register(Campania, CampaniaAdmin)
admin_site.register(EnvioWhatsapp, EnvioWhatsappAdmin)

# Configuración general (singleton editable desde el admin).
from configuracion.models import ConfiguracionGeneral
from configuracion.admin import ConfiguracionGeneralAdmin
admin_site.register(ConfiguracionGeneral, ConfiguracionGeneralAdmin)

# django-q2: registra Schedule (cron), Success (tasks OK) y Failure
# (tasks fallidas) en el admin_site custom. Sin esto, las URLs
# `/admin/django_q/...` dan 404 porque django-q se auto-registra
# en el admin default de Django, no en nuestro MaterialAdminSite.
from django_q.models import Schedule, Success, Failure
from django_q.admin import ScheduleAdmin, TaskAdmin, FailAdmin
admin_site.register(Schedule, ScheduleAdmin)
admin_site.register(Success, TaskAdmin)
admin_site.register(Failure, FailAdmin)

# django-auditlog registra LogEntry en el admin default. Como
# nosotros usamos `admin_site` custom (MaterialAdminSite), hay
# que re-registrarlo acá para que aparezca en /admin/.
from auditlog.models import LogEntry
from auditlog.admin import LogEntryAdmin
admin_site.register(LogEntry, LogEntryAdmin)