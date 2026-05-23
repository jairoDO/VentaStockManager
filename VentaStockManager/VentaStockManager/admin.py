from material.admin.sites import MaterialAdminSite

# -----------------------------------------------------------------------
# Monkey-patch: material-admin pide actions.min.js / prepopulate.min.js
# cuando DEBUG=False, pero esos archivos YA NO EXISTEN en Django 4.2+
# (el equipo de Django dejó de minificarlos hace varias versiones,
# solo quedan actions.js, prepopulate.js sin sufijo). Material nunca
# se actualizó.
#
# Síntoma sin este patch: TODOS los changelists del admin tiran 500
# en producción con `ValueError: The file 'admin/js/actions.min.js'
# could not be found`. Anda fino con DEBUG=True porque ahí material
# usa `extra=''` (sin .min) y los archivos existen.
#
# Fix: forzamos `media` a usar siempre las versiones sin .min. Mantenemos
# las de vendor (jquery, xregexp) con .min porque ESAS sí existen.
from django import forms
from material.admin.options import MaterialModelAdminMixin
from django.contrib.admin.options import ModelAdmin as _DjangoModelAdmin


@property
def _patched_material_media(self):
    js = [
        'admin/js/vendor/jquery/jquery.min.js',  # sí existe
        'admin/js/jquery.init.js',
        'admin/js/core.js',
        'admin/js/actions.js',                   # NO usar .min (no existe en 4.2+)
        'admin/js/urlify.js',
        'admin/js/prepopulate.js',               # NO usar .min (no existe en 4.2+)
        'admin/js/vendor/xregexp/xregexp.min.js',  # sí existe
        'material/admin/js/RelatedObjectLookups.min.js',
    ]
    return _DjangoModelAdmin.media.fget(self) + forms.Media(js=js)


MaterialModelAdminMixin.media = _patched_material_media

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
            try:
                from articulo.models import SolicitudListaCliente
                ctx['solicitudes_lista_pendientes'] = (
                    SolicitudListaCliente.objects.filter(resuelta=False).count()
                )
            except Exception:
                ctx['solicitudes_lista_pendientes'] = 0
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

        # Orden CUSTOM del dashboard pensado para el flow del operador.
        # No alfabético — el alfabético deja "Auditoría" arriba y "Venta"
        # abajo, que es lo contrario a lo que el operador necesita.
        #
        # Especialmente importante en mobile: las primeras 2-3 apps son
        # las que ve al abrir el admin sin scrollear. Tienen que ser las
        # que más usa (Venta, Cliente, Articulo).
        APP_ORDER = {
            'venta': 1,        # más usado: cargar ventas todos los días
            'cliente': 2,       # asociado a ventas
            'articulo': 3,      # consulta de precios/stock
            'compra': 4,        # menos frecuente: cargar facturas de proveedores
            'vendedor': 5,
            'factura_config': 6,
            'wa_campania': 7,   # solo superuser
            'configuracion': 8, # solo superuser
            'auth': 9,          # User admin — superuser only en la práctica
            'auditlog': 10,
            'django_q': 11,     # tareas internas, último
        }
        app_list = sorted(
            valid_apps,
            # apps no listadas (futuras) van al final, ordenadas alfa.
            key=lambda x: (APP_ORDER.get(x['app_label'], 99), x['name'].lower()),
        )

        # Add icons to the app list
        for app in app_list:
            app_config = apps.get_app_config(app['app_label'])
            app['icon'] = getattr(app_config, 'icon_name', 'default_icon')

        # Orden custom de modelos DENTRO de cada app. Django default los
        # ordena alfa también, que pone "Alerta de stock" antes de "Venta"
        # (lo principal). Forzamos un orden razonable.
        MODEL_ORDER = {
            'venta': {
                'venta': 1, 'pedido': 2, 'alertastock': 3,
            },
            'cliente': {
                'cliente': 1, 'cuentacliente': 2,
                'movimientocuenta': 3, 'preciocliente': 4,
            },
            'articulo': {
                'articulo': 1, 'categoria': 2, 'reglacategoria': 3,
                'listaprecios': 4, 'difusionlistapreciosenvio': 5,
                'solicitudlistacliente': 6,
            },
            'wa_campania': {
                'panelconexionwa': 0,  # el "virtual" que insertamos abajo
                'campania': 1, 'enviowhatsapp': 2,
            },
        }
        for app in app_list:
            label = app.get('app_label')
            if label in MODEL_ORDER:
                order = MODEL_ORDER[label]
                app['models'].sort(
                    key=lambda m: (
                        order.get((m.get('object_name') or '').lower(), 99),
                        (m.get('name') or '').lower(),
                    ),
                )

        # Inyectar un atajo "Conexión WhatsApp" dentro del app
        # wa_campania para que aparezca como card en /admin/. El panel
        # vive fuera del admin (es una pantalla custom en
        # /wa-campania/conexion/) pero conceptualmente es admin operativo:
        # el operador necesita llegar rápido para vincular el bot.
        #
        # Aprovechamos la estructura de `models` del app_list para meter
        # un "modelo virtual" cuyo admin_url apunta al panel. Material
        # admin lo renderiza como una card más, indistinguible de un
        # modelo registrado real. Solo se muestra a superusers (la
        # vista misma exige is_superuser).
        if request.user.is_authenticated and request.user.is_superuser:
            for app in app_list:
                if app.get('app_label') == 'wa_campania':
                    app['models'].insert(0, {
                        'name': 'Conexión WhatsApp',
                        'object_name': 'PanelConexionWA',
                        'admin_url': '/wa-campania/conexion/',
                        'add_url': None,
                        'perms': {
                            'add': False, 'change': False,
                            'delete': False, 'view': True,
                        },
                        # Material admin lee el icono del campo `model`,
                        # NO disponible para virtuales. Lo dejamos como
                        # un dict con `_meta.app_label` para que el
                        # template no rompa. Si la card sale sin icono
                        # nice, no es bloqueante.
                        'view_only': True,
                    })
                    break

            # Atajo "Gestión de Usuarios" dentro del app `auth`. Apunta
            # a la pantalla custom Alpine en /usuarios/ que reemplaza
            # el admin clásico de auth.User (demasiado complejo: tiene
            # 20+ campos, permisos granulares, grupos). La pantalla
            # custom expone solo lo necesario: crear vendedor/superuser
            # con flags correctos, resetear password, activar/desactivar.
            #
            # Lo metemos como primer item de `auth` para que sea lo
            # primero que ve el operador (antes que "Users" clásico).
            for app in app_list:
                if app.get('app_label') == 'auth':
                    app['models'].insert(0, {
                        'name': 'Gestión de Usuarios',
                        'object_name': 'GestionUsuarios',
                        'admin_url': '/usuarios/',
                        'add_url': '/usuarios/#crear',
                        'perms': {
                            'add': True, 'change': True,
                            'delete': False, 'view': True,
                        },
                        'view_only': True,
                    })
                    break

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
#
# IMPORTANTE: NO registramos ReglaCategoria como entrada propia del
# admin. Las reglas se editan SOLO desde el inline de Categoría para
# que el operador tenga UNA sola entrada mental ("categorías, y dentro
# están sus reglas"). El admin class sigue existiendo en articulo/admin.py
# por si alguna vez queremos exponerlo de nuevo o para acceso directo
# por URL (/admin/articulo/reglacategoria/) en debug.
from articulo.models import (
    Categoria, ListaPrecios, DifusionListaPreciosEnvio,
    SolicitudListaCliente,
)
from articulo.admin import (
    CategoriaAdmin, ListaPreciosAdmin, DifusionListaPreciosEnvioAdmin,
    SolicitudListaClienteAdmin,
)
admin_site.register(Categoria, CategoriaAdmin)
# Historial de envíos de difusión (read-only desde admin, con bulk
# action "Reintentar fallidos"). El operador entra acá cuando quiere
# investigar por qué un cliente no recibió la lista, o reintentar los
# que fallaron por algún motivo (bot caído, número mal, etc.).
admin_site.register(DifusionListaPreciosEnvio, DifusionListaPreciosEnvioAdmin)
# Bandeja de "clientes que pidieron lista pero no tienen una asignada".
# Las crea el auto-responder del bot. El operador las ve en el badge
# del header + entra acá para armarles una lista (un click va al editor).
admin_site.register(SolicitudListaCliente, SolicitudListaClienteAdmin)
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
#
# Restringido a superuser via subclasses con SuperuserOnlyAdminMixin:
# los vendedores no necesitan ver schedules ni tasks internos.
from cliente.admin_permissions import SuperuserOnlyAdminMixin
from django_q.models import Schedule, Success, Failure
from django_q.admin import ScheduleAdmin, TaskAdmin, FailAdmin


class _ScheduleAdminSuperuser(SuperuserOnlyAdminMixin, ScheduleAdmin):
    pass


class _TaskAdminSuperuser(SuperuserOnlyAdminMixin, TaskAdmin):
    pass


class _FailAdminSuperuser(SuperuserOnlyAdminMixin, FailAdmin):
    pass


admin_site.register(Schedule, _ScheduleAdminSuperuser)
admin_site.register(Success, _TaskAdminSuperuser)
admin_site.register(Failure, _FailAdminSuperuser)

# django-auditlog registra LogEntry en el admin default. Como
# nosotros usamos `admin_site` custom (MaterialAdminSite), hay
# que re-registrarlo acá para que aparezca en /admin/.
#
# Restringido a superuser — los registros de cambios son sensibles
# y los vendedores no necesitan verlos.
from auditlog.models import LogEntry
from auditlog.admin import LogEntryAdmin


class _LogEntryAdminSuperuser(SuperuserOnlyAdminMixin, LogEntryAdmin):
    pass


admin_site.register(LogEntry, _LogEntryAdminSuperuser)