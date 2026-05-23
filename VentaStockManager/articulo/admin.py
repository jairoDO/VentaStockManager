from django.contrib import admin
from datetime  import date
from django.contrib import messages
from django.core.management import call_command
from django.db import models as django_models
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html
# Register your models here.
from articulo.models import (
    Articulo, Categoria, ListaPrecios, ListaPreciosItem,
    ReglaCategoria, SolicitudListaCliente,
)
from cliente.admin_permissions import (
    SuperuserOnlyAdminMixin, ArticulosReadOnlyForNonSuperuser,
    StaffFullAccessAdminMixin,
)
from django_q.tasks import async_task
from .task import actualizar_precios_articulos_desde_drive
from .widgets import ListaPalabrasWidget
import decimal

class ArticuloAdmin(ArticulosReadOnlyForNonSuperuser, admin.ModelAdmin):

    # Redirigimos el changelist clásico (/admin/articulo/articulo/) a la
    # grilla custom — es la pantalla canónica para edición masiva.
    # Mantenemos un escape ?clasico=1 para cuando el operador necesita
    # las acciones bulk de Django admin (mover categoría, asignar proveedor,
    # eliminar). La grilla muestra un botón visible "Admin clásico"
    # que linkea con ese query param para que sea descubrible.
    def changelist_view(self, request, extra_context=None):
        if request.GET.get('clasico') != '1':
            return HttpResponseRedirect(reverse('grilla_precios'))
        return super().changelist_view(request, extra_context=extra_context)

    # NOTA: `codigo_interno` se sigue autogenerando en save() (legacy
    # data lo usa como fallback en algunos lugares), pero se OCULTA
    # de la UI — Osvaldo nunca lo lee/usa. Si en el futuro hay que
    # verlo, agregalo a list_display y search_fields. La búsqueda
    # sigue funcionando por código real y nombre.
    list_display = (
        'marca', 'codigo', 'nombre', 'stock',
        'precio_minorista', 'categoria_badge', 'proveedor_nombre',
        'vence_dentro_de_60_dias', 'total_venta_por_articulo',
    )
    list_filter = ('categoria', 'proveedor', 'marca')
    list_select_related = ('categoria', 'proveedor')  # evita N+1 al renderizar columnas FK
    search_fields = ("nombre", 'codigo')
    # fields = ("__all__",)
    ordering = ("vencimiento",)
    icon_name = "local_play"
    model = Articulo
    autocomplete_fields = ('categoria', 'proveedor')
    actions = [
        'agregar_10_por_ciento_al_precio',
        'agregar_5_por_ciento_al_precio',
        'agregar_1_por_ciento_al_precio',
        'disparar_actualizar_precio_archivo',
        'aplicar_reglas_categoria_action',
        'limpiar_categoria',
        'mover_a_categoria_action',
        'asignar_proveedor_action',
    ]

    @admin.action(description='Asignar proveedor a los seleccionados…')
    def asignar_proveedor_action(self, request, queryset):
        """
        Bulk action de "intermediate page": cuando el usuario aprieta
        sin todavía haber elegido proveedor, mostramos un form
        intermedio con un select. Cuando llega con `proveedor_id` y
        confirmación, hacemos el `update()` bulk.

        El patrón es estándar de Django admin actions; lo único
        non-obvious es que tenemos que mantener los IDs seleccionados
        en un hidden field del form intermedio para que el segundo
        POST sepa qué actualizar.
        """
        from compra.models import Proveedor

        # Si el form intermedio mandó `aplicar`, tenemos los IDs en
        # el body del POST como hidden inputs (action confirma).
        if request.POST.get('aplicar'):
            proveedor_id = request.POST.get('proveedor_id') or None
            if proveedor_id == '__sin_proveedor__':
                # Opción "quitar proveedor" (dejar en NULL).
                n = queryset.update(proveedor=None)
                self.message_user(
                    request,
                    f'{n} artículos quedaron sin proveedor.',
                    level=messages.WARNING,
                )
            else:
                try:
                    proveedor = Proveedor.objects.get(pk=proveedor_id)
                except (Proveedor.DoesNotExist, ValueError, TypeError):
                    self.message_user(
                        request,
                        'Proveedor inválido.',
                        level=messages.ERROR,
                    )
                    return None
                n = queryset.update(proveedor=proveedor)
                self.message_user(
                    request,
                    f'{n} artículos asignados al proveedor "{proveedor.nombre}".',
                    level=messages.SUCCESS,
                )
            # Volvemos al changelist clásico (no a la grilla) para que el
            # operador siga viendo el resultado de la acción + sus seleccionados.
            return HttpResponseRedirect(reverse('admin:articulo_articulo_changelist') + '?clasico=1')

        # Primer paso: mostrar el form intermedio.
        proveedores = Proveedor.objects.order_by('nombre')
        contexto = {
            'titulo': 'Asignar proveedor a artículos',
            'queryset': queryset,
            'cantidad': queryset.count(),
            'proveedores': proveedores,
            'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
        }
        return render(request, 'admin/articulo/asignar_proveedor.html', contexto)

    def proveedor_nombre(self, obj):
        """Nombre del proveedor o guión si está vacío. Usa list_select_related."""
        if not obj.proveedor_id:
            return format_html('<span style="color: #999;">—</span>')
        return obj.proveedor.nombre if obj.proveedor else '—'
    proveedor_nombre.short_description = 'Proveedor'
    proveedor_nombre.admin_order_field = 'proveedor__nombre'

    def categoria_badge(self, obj):
        """Badge coloreado con la categoría del artículo (o '-' si no tiene)."""
        if not obj.categoria_id:
            return format_html('<span style="color: #999;">—</span>')
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 10px; font-size: 11px; font-weight: 500;">{}</span>',
            obj.categoria.color,
            obj.categoria.nombre,
        )
    categoria_badge.short_description = 'Categoría'
    categoria_badge.admin_order_field = 'categoria__nombre'

    @admin.action(description='Aplicar reglas de categoría (a los seleccionados sin categoría)')
    def aplicar_reglas_categoria_action(self, request, queryset):
        """
        Aplica las reglas SOLO sobre los artículos seleccionados,
        respetando los que ya tienen categoría. Útil para Osvaldo si
        quiere clasificar un subset chico sin correr el command global.
        """
        from articulo.models import ReglaCategoria
        reglas = list(
            ReglaCategoria.objects.filter(activa=True)
            .select_related('categoria')
            .order_by('prioridad', 'id')
        )
        reglas_lc = [
            (r, [k.lower() for k in (r.palabras_clave or []) if k])
            for r in reglas
        ]
        sin_categoria = queryset.filter(categoria__isnull=True)
        asignados = 0
        for art in sin_categoria.iterator():
            nombre_lc = (art.nombre or '').lower()
            for regla, keywords in reglas_lc:
                if any(kw in nombre_lc for kw in keywords):
                    Articulo.objects.filter(pk=art.pk).update(categoria=regla.categoria)
                    asignados += 1
                    break
        self.message_user(
            request,
            f'{asignados} artículos asignados a categoría. '
            f'{queryset.count() - sin_categoria.count()} ya tenían categoría (no se tocaron).',
            level=messages.SUCCESS if asignados else messages.INFO,
        )

    @admin.action(description='Quitar categoría a los seleccionados')
    def limpiar_categoria(self, request, queryset):
        """Para corregir errores de clasificación masivos."""
        n = queryset.update(categoria=None)
        self.message_user(
            request,
            f'{n} artículos quedaron sin categoría.',
            level=messages.WARNING,
        )

    @admin.action(description='Mover a otra categoría…')
    def mover_a_categoria_action(self, request, queryset):
        """
        Bulk action de "intermediate page" para mover artículos a otra
        categoría. Caso de uso típico: filtrar por categoría "Sin
        categoría" (o cualquier otra), seleccionar todos, mandarlos a
        una categoría correcta en un solo click. Sin esto Osvaldo
        tendría que abrir cada artículo o usar la grilla.

        Sobrescribe la categoría previa (NO respeta la regla de "no
        pisar"). Si querés respetar, usá `aplicar_reglas_categoria_action`.

        Patrón calcado de `asignar_proveedor_action` — ver ahí para
        el porqué del double-POST con hidden inputs.
        """
        # Si el form intermedio mandó `aplicar`, ejecutamos el update.
        if request.POST.get('aplicar'):
            categoria_id = request.POST.get('categoria_id') or None
            if categoria_id == '__sin_categoria__':
                n = queryset.update(categoria=None)
                self.message_user(
                    request,
                    f'{n} artículos quedaron sin categoría.',
                    level=messages.WARNING,
                )
            else:
                try:
                    categoria = Categoria.objects.get(pk=categoria_id)
                except (Categoria.DoesNotExist, ValueError, TypeError):
                    self.message_user(
                        request,
                        'Categoría inválida.',
                        level=messages.ERROR,
                    )
                    return None
                n = queryset.update(categoria=categoria)
                self.message_user(
                    request,
                    f'{n} artículos movidos a la categoría "{categoria.nombre}".',
                    level=messages.SUCCESS,
                )
            # Volvemos al changelist clásico (no a la grilla).
            return HttpResponseRedirect(reverse('admin:articulo_articulo_changelist') + '?clasico=1')

        # Primer paso: mostrar el form intermedio con select de categorías.
        categorias = Categoria.objects.order_by('nombre')
        contexto = {
            'titulo': 'Mover artículos a categoría',
            'queryset': queryset,
            'cantidad': queryset.count(),
            'categorias': categorias,
            'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
        }
        return render(request, 'admin/articulo/mover_categoria.html', contexto)
    # autocomplete_fields = ['nombre']  # Enable autocomplete for nombre
        
    def total_venta_por_articulo(self, obj):
        total = 0
        for articulo_venta in obj.articulos_vendidos.all():
            # Remove any non-numeric characters except for the decimal point
            precio = articulo_venta.precio.replace("'", "").replace(",", "")
            total += articulo_venta.cantidad * float(precio)
        return total

    def disparar_actualizar_precio_archivo(self, request, queryset):
        # Aquí se dispara la tarea
        errores = actualizar_precios_articulos_desde_drive()
        if isinstance(errores, list):
            for error in errores:
                self.message_user(request, error, level=messages.WARNING)
        else:
            self.message_user(request, errores, level=messages.SUCCESS )

    disparar_actualizar_precio_archivo.short_description = "Disparar actualizar precio xlsx"
    def agregar_10_por_ciento_al_precio(modeladmin, request, queryset):

        for obj in queryset:
            obj.precio_minorista *= decimal.Decimal(1.1)
            obj.precio_mayorista *= decimal.Decimal(1.1)
            obj.save()
        messages.success(request, "Se actualizaron los precios al 10 porciento mas exitosamente")

    def agregar_5_por_ciento_al_precio(modeladmin, request, queryset):
        for obj in queryset:
            obj.precio_minorista *= decimal.Decimal(1.05)
            obj.precio_mayorista *= decimal.Decimal(1.05)
            obj.save()
        messages.success(request, "Se actualizaron los precios al 5 porciento mas exitosamente")

    def agregar_1_por_ciento_al_precio(modeladmin, request, queryset):
        for obj in queryset:
            obj.precio_minorista *= decimal.Decimal(1.01)
            obj.precio_mayorista *= decimal.Decimal(1.01)
            obj.save()
        messages.success(request, "Se actualizaron los precios al 1 porciento mas exitosamente")
  
    def vence_dentro_de_60_dias(self, obj):
        return (obj.vencimiento - date.today()).days < 60
    
    vence_dentro_de_60_dias.boolean = True
    vence_dentro_de_60_dias.short_description = "Vence menos 60 días"


class ReglaCategoriaInline(admin.TabularInline):
    """Reglas asociadas a una categoría, editables desde la misma pantalla."""
    model = ReglaCategoria
    extra = 0
    fields = ('palabras_clave', 'prioridad', 'activa')
    # Usamos el widget custom de lista dinámica para palabras_clave.
    # Sin esto el JSONField se rendería como un <textarea> con JSON
    # crudo — el operador no sabe qué es JSON ni le interesa.
    formfield_overrides = {
        django_models.JSONField: {'widget': ListaPalabrasWidget},
    }


class CategoriaInlineEnRubro(admin.TabularInline):
    """
    Inline para que el operador, al abrir un Rubro, vea/edite las
    categorías que pertenecen a él en una sola pantalla (más rápido
    que ir categoría por categoría seteando el FK).
    """
    from .models import Categoria as _CatModel  # evita ciclo en import-time
    model = _CatModel
    fk_name = 'rubro'
    fields = ('nombre', 'color', 'descripcion')
    extra = 0
    show_change_link = True


class RubroAdmin(StaffFullAccessAdminMixin, admin.ModelAdmin):
    """
    Admin del Rubro (Golosinas, Bebidas, Almacén, …). Lo edita el
    superuser/admin al setear la estructura inicial; el vendedor lo
    consume al elegir Rubro en el editor de Lista de Precios.
    """
    icon_name = 'folder_special'
    list_display = ('nombre_con_color', 'orden', 'descripcion_corta', 'cantidad_categorias')
    list_editable = ('orden',)
    search_fields = ('nombre', 'descripcion')
    ordering = ('orden', 'nombre')
    inlines = [CategoriaInlineEnRubro]

    def get_queryset(self, request):
        from django.db.models import Count
        return (
            super().get_queryset(request)
            .annotate(_n_categorias=Count('categorias', distinct=True))
        )

    def nombre_con_color(self, obj):
        return format_html(
            '<span style="background: {}; color: white; padding: 4px 10px; '
            'border-radius: 12px; font-weight: 500;">{}</span>',
            obj.color, obj.nombre,
        )
    nombre_con_color.short_description = 'Rubro'
    nombre_con_color.admin_order_field = 'nombre'

    def descripcion_corta(self, obj):
        desc = obj.descripcion or ''
        return desc[:60] + ('…' if len(desc) > 60 else '')
    descripcion_corta.short_description = 'Descripción'

    def cantidad_categorias(self, obj):
        return getattr(obj, '_n_categorias', None) or obj.categorias.count()
    cantidad_categorias.short_description = 'Categorías'
    cantidad_categorias.admin_order_field = '_n_categorias'


class CategoriaAdmin(StaffFullAccessAdminMixin, admin.ModelAdmin):
    icon_name = 'category'
    list_display = ('nombre_con_color', 'rubro', 'descripcion_corta', 'cantidad_articulos', 'cantidad_reglas')
    list_filter = ('rubro',)
    list_select_related = ('rubro',)
    search_fields = ('nombre', 'descripcion')
    inlines = [ReglaCategoriaInline]

    def get_queryset(self, request):
        """
        Annotate de los counts en UN query en lugar de pegarle dos
        `.count()` por fila al renderizar el list_display (con 9
        categorías eran 18 queries, manejable; con muchas más se
        agranda. Mejor cerrarlo de una).
        """
        from django.db.models import Count
        return (
            super().get_queryset(request)
            .annotate(
                _n_articulos=Count('articulos', distinct=True),
                _n_reglas=Count('reglas', distinct=True),
            )
        )

    def nombre_con_color(self, obj):
        return format_html(
            '<span style="background: {}; color: white; padding: 4px 10px; '
            'border-radius: 12px; font-weight: 500;">{}</span>',
            obj.color, obj.nombre,
        )
    nombre_con_color.short_description = 'Categoría'
    nombre_con_color.admin_order_field = 'nombre'

    def descripcion_corta(self, obj):
        desc = obj.descripcion or ''
        return desc[:60] + ('…' if len(desc) > 60 else '')
    descripcion_corta.short_description = 'Descripción'

    def cantidad_articulos(self, obj):
        # Usamos el campo anotado por get_queryset; fallback al
        # .count() en caso de que esté faltando (ej. usar el admin
        # desde tests).
        return getattr(obj, '_n_articulos', None) or obj.articulos.count()
    cantidad_articulos.short_description = 'Artículos'
    cantidad_articulos.admin_order_field = '_n_articulos'

    def cantidad_reglas(self, obj):
        return getattr(obj, '_n_reglas', None) or obj.reglas.count()
    cantidad_reglas.short_description = 'Reglas'
    cantidad_reglas.admin_order_field = '_n_reglas'


class ReglaCategoriaAdmin(admin.ModelAdmin):
    # `rule` (Material Icons v3+) no está en el bundle de
    # django-material-admin → se renderiza vacío. Usamos `label`
    # (etiqueta/tag), que matchea con la idea de "asignar categoría
    # por palabras clave" y existe desde Material Icons v1.
    icon_name = 'label'
    list_display = ('categoria', 'palabras_clave_resumen', 'prioridad', 'activa', 'updated_at')
    list_filter = ('activa', 'categoria')
    search_fields = ('categoria__nombre',)
    autocomplete_fields = ('categoria',)
    ordering = ('prioridad', 'categoria__nombre')
    # Mismo override que en el inline — el form de detalle también usa
    # el widget de lista dinámica en lugar del JSONField crudo.
    formfield_overrides = {
        django_models.JSONField: {'widget': ListaPalabrasWidget},
    }

    def palabras_clave_resumen(self, obj):
        kws = obj.palabras_clave or []
        if not kws:
            return format_html('<span style="color: #999;">(sin palabras)</span>')
        muestra = ', '.join(kws[:5])
        if len(kws) > 5:
            muestra += f'… (+{len(kws) - 5})'
        return muestra
    palabras_clave_resumen.short_description = 'Palabras clave'


class ListaPreciosItemInline(admin.TabularInline):
    """
    Inline para gestionar los artículos de una lista de precios desde
    la pantalla de cambio. Es la forma "manual" hasta que tengamos
    la pantalla custom con filtros + carga por categoría.
    """
    model = ListaPreciosItem
    extra = 0
    fields = ('orden', 'articulo', 'nota')
    autocomplete_fields = ('articulo',)
    ordering = ('orden', 'articulo__nombre')


class ListaPreciosAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    """
    Admin de listas de precios. Permite crear/editar a mano una lista
    eligiendo cliente, descuento opcional y artículos uno por uno via
    el inline. La pantalla custom (próxima fase) va a hacer esto más
    cómodo con filtros y "agregar todos los de la categoría X".
    """
    # `price_change` (Material Icons v3+) no está en el bundle de
    # django-material-admin. Usamos `assignment` (clipboard con
    # líneas), que visualmente comunica "lista" y está desde v1.
    icon_name = 'assignment'
    list_display = (
        'nombre', 'cliente_nombre', 'cantidad_items_display',
        'ajuste_display', 'link_publico_estado',
        'creado_por', 'updated_at', 'abrir_editor_visual',
    )
    list_filter = ('tipo_ajuste', 'updated_at')
    search_fields = ('nombre', 'cliente__nombre', 'cliente__apellido')
    autocomplete_fields = ('cliente',)
    readonly_fields = ('creado_por', 'created_at', 'updated_at',
                       'share_token', 'share_expira_at')
    inlines = [ListaPreciosItemInline]
    actions = ['desactivar_link_publico']

    # Redirigimos add/change al editor visual (Tailwind + Alpine) en
    # /articulos/lista-precios/. El admin estándar de Django con su
    # chrome viejo confunde al operador — la pantalla custom es la
    # canónica. Si en el futuro hace falta editar metadata "rara"
    # (auditoría avanzada, debug), se puede llegar via la URL directa.
    def add_view(self, request, form_url='', extra_context=None):
        return HttpResponseRedirect(reverse('lista_precios_pantalla'))

    def change_view(self, request, object_id, form_url='', extra_context=None):
        # Pasamos el id por query param para que la pantalla custom
        # precargue esa lista al abrir.
        return HttpResponseRedirect(
            reverse('lista_precios_pantalla') + f'?lista_id={object_id}'
        )

    def abrir_editor_visual(self, obj):
        """Link en cada fila del list para abrir el editor con esa lista cargada."""
        return format_html(
            '<a href="/articulos/lista-precios/?lista_id={}" '
            'style="display:inline-block; padding:2px 8px; background:#2563eb; '
            'color:white; border-radius:3px; text-decoration:none; font-size:11px;">'
            '✏ Editar visual</a>',
            obj.id,
        )
    abrir_editor_visual.short_description = ''

    @admin.action(description='Desactivar link público (revocar token)')
    def desactivar_link_publico(self, request, queryset):
        """
        Bulk action para revocar links públicos. Útil cuando se
        sospecha que un link "se filtró" entre clientes — Osvaldo
        selecciona varias listas y revoca todos los tokens de una.

        Idempotente: si la lista no tenía link activo, el método del
        modelo no hace nada (no contamos ese caso como "fallo").
        """
        n_revocados = 0
        for lista in queryset:
            if lista.share_token or lista.share_expira_at:
                lista.desactivar_link()
                n_revocados += 1
        self.message_user(
            request,
            f'{n_revocados} link(s) público(s) desactivado(s). '
            f'Las listas seleccionadas siguen accesibles desde el admin.',
            level=messages.WARNING if n_revocados else messages.INFO,
        )

    def link_publico_estado(self, obj):
        """
        Badge visual del estado del link público + botón 📋 para copiar
        al portapapeles directamente desde el list (sin tener que
        entrar al editor visual).

        El `onclick` inline arma `location.origin + /p/.../<uuid>/` en
        runtime — no podemos pre-resolver la URL absoluta en el admin
        porque no tenemos `request` acá. `navigator.clipboard.writeText`
        requiere HTTPS o localhost (que es nuestro caso en dev).
        """
        if obj.link_activo:
            expira = obj.share_expira_at.strftime('%d/%m/%Y') if obj.share_expira_at else 'sin vencimiento'
            return format_html(
                '<span style="background:#16a34a; color:white; padding:2px 8px; '
                'border-radius:10px; font-size:11px; margin-right:6px;" '
                'title="Expira: {}">activo</span>'
                '<button type="button" '
                'onclick="const u=location.origin+\'/p/lista-precios/{}/\';'
                'navigator.clipboard.writeText(u).then(()=>{{this.textContent=\'✓\';'
                'setTimeout(()=>{{this.textContent=\'📋\'}},1500)}});'
                'event.preventDefault();return false;" '
                'title="Copiar link al portapapeles" '
                'style="padding:2px 6px; background:#f1f5f9; border:1px solid #cbd5e1; '
                'border-radius:4px; cursor:pointer; font-size:12px;">'
                '📋</button>',
                expira, obj.share_token,
            )
        if obj.share_token or obj.share_expira_at:
            return format_html(
                '<span style="background:#9ca3af; color:white; padding:2px 8px; '
                'border-radius:10px; font-size:11px;">expirado</span>'
            )
        return format_html('<span style="color:#9ca3af;">—</span>')
    link_publico_estado.short_description = 'Link público'

    def get_queryset(self, request):
        # Annotate del count para no hacer un .count() por fila en
        # el list_display (mismo patrón de N+1 que arreglamos en
        # ClienteAdmin y CategoriaAdmin).
        from django.db.models import Count
        return (
            super().get_queryset(request)
            .select_related('cliente', 'creado_por')
            .annotate(_n_items=Count('items'))
        )

    def cliente_nombre(self, obj):
        return obj.cliente.nombre_completo()
    cliente_nombre.short_description = 'Cliente'
    cliente_nombre.admin_order_field = 'cliente__nombre'

    def cantidad_items_display(self, obj):
        n = getattr(obj, '_n_items', None)
        if n is None:
            n = obj.cantidad_items()
        return n
    cantidad_items_display.short_description = 'Items'
    cantidad_items_display.admin_order_field = '_n_items'

    def ajuste_display(self, obj):
        """
        Muestra el ajuste como "5% desc." o "5% aum." con color.
        Reemplaza la columna pelada de `descuento_porcentaje` que no
        decía si era descuento o aumento (se confundía al ojo).
        """
        if not obj.descuento_porcentaje:
            return '—'
        pct = obj.descuento_porcentaje
        if obj.tipo_ajuste == 'aumento':
            return format_html(
                '<span style="color:#b45309;font-weight:600">↑ {}% aum.</span>',
                pct,
            )
        return format_html(
            '<span style="color:#047857;font-weight:600">↓ {}% desc.</span>',
            pct,
        )
    ajuste_display.short_description = 'Ajuste'
    ajuste_display.admin_order_field = 'descuento_porcentaje'

    def save_model(self, request, obj, form, change):
        if not change and not obj.creado_por_id:
            obj.creado_por = request.user
        super().save_model(request, obj, form, change)


class DifusionListaPreciosEnvioAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    """
    Vista persistente del histórico de envíos de difusión.

    El panel de progreso de la pantalla de Difundir solo muestra los
    últimos 50 envíos durante la sesión actual. Cuando el operador
    cierra la pantalla o quiere ver envíos viejos (¿qué pasó con esa
    difusión del mes pasado? ¿quién recibió la última lista?), tiene
    que venir acá.

    Todo es read-only desde el admin — los envíos solo se crean desde
    la pantalla de difundir (via API). Lo que SÍ ofrecemos es:
      - Filtros por status, modo, lista (para ver "todos los fallidos
        de la lista X").
      - Búsqueda por cliente / teléfono / mensaje de error (para
        encontrar el envío de un cliente puntual).
      - Bulk action "Reintentar seleccionados" que re-encola los
        fallidos creando envíos pendientes nuevos.
    """
    list_display = (
        'created_at_short',
        'lista_link',
        'cliente_link',
        'modo_badge',
        'status_badge',
        'sent_at_short',
        'error_msg_short',
    )
    list_filter = ('status', 'modo', 'lista', 'created_at')
    search_fields = (
        'cliente__nombre', 'cliente__apellido',
        'telefono_usado', 'error_msg',
    )
    list_select_related = ('lista', 'cliente')
    readonly_fields = (
        'lista', 'cliente', 'modo', 'status', 'telefono_usado',
        'error_msg', 'sent_at', 'created_at', 'creado_por',
    )
    date_hierarchy = 'created_at'
    actions = ['reintentar_fallidos']
    icon_name = 'forward_to_inbox'

    class Media:
        # Mismo CSS de polish que el resto del admin.
        css = {
            'all': (
                'admin/wa_campania/admin_fixes.css',
                'admin/configuracion/polish.css',
            ),
        }

    def has_add_permission(self, request):
        # Solo se crean desde la pantalla de difundir.
        return False

    def has_change_permission(self, request, obj=None):
        # Read-only desde el admin. Si necesitás cambiar un envío,
        # mejor crear uno nuevo (reintentar).
        return False

    # ---------- Columnas custom ----------

    def created_at_short(self, obj):
        return obj.created_at.strftime('%d/%m %H:%M')
    created_at_short.short_description = 'Creado'
    created_at_short.admin_order_field = 'created_at'

    def sent_at_short(self, obj):
        if not obj.sent_at:
            return format_html('<span style="color:#94a3b8;">—</span>')
        return obj.sent_at.strftime('%d/%m %H:%M:%S')
    sent_at_short.short_description = 'Procesado'
    sent_at_short.admin_order_field = 'sent_at'

    def lista_link(self, obj):
        return format_html(
            '<a href="/articulos/lista-precios/?lista_id={}">{}</a>',
            obj.lista_id, obj.lista.nombre,
        )
    lista_link.short_description = 'Lista'
    lista_link.admin_order_field = 'lista__nombre'

    def cliente_link(self, obj):
        return format_html(
            '<a href="/admin/cliente/cliente/{}/change/">{}</a>'
            '<div style="font-size:11px;color:#64748b;font-family:monospace;">{}</div>',
            obj.cliente_id, obj.cliente.nombre_completo(), obj.telefono_usado,
        )
    cliente_link.short_description = 'Cliente'
    cliente_link.admin_order_field = 'cliente__nombre'

    def modo_badge(self, obj):
        labels = {
            'link': '📎 link',
            'pdf': '📄 PDF',
            'ambos': '✨ ambos',
        }
        return format_html(
            '<span style="background:#f1f5f9;color:#475569;padding:2px 8px;'
            'border-radius:10px;font-size:11px;">{}</span>',
            labels.get(obj.modo, obj.modo),
        )
    modo_badge.short_description = 'Modo'
    modo_badge.admin_order_field = 'modo'

    def status_badge(self, obj):
        colors = {
            'pendiente': ('#64748b', '#f1f5f9'),
            'enviando':  ('#1d4ed8', '#dbeafe'),
            'enviado':   ('#047857', '#d1fae5'),
            'fallido':   ('#b91c1c', '#fee2e2'),
        }
        icons = {
            'pendiente': '⏳', 'enviando': '⏩',
            'enviado': '✓', 'fallido': '✗',
        }
        fg, bg = colors.get(obj.status, ('#475569', '#e2e8f0'))
        return format_html(
            '<span style="background:{};color:{};padding:3px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;'
            'text-transform:uppercase;white-space:nowrap;">{} {}</span>',
            bg, fg, icons.get(obj.status, ''), obj.get_status_display(),
        )
    status_badge.short_description = 'Estado'
    status_badge.admin_order_field = 'status'

    def error_msg_short(self, obj):
        if not obj.error_msg:
            return format_html('<span style="color:#94a3b8;">—</span>')
        truncated = obj.error_msg[:80] + ('…' if len(obj.error_msg) > 80 else '')
        return format_html(
            '<span style="color:#b91c1c;font-size:12px;" title="{}">{}</span>',
            obj.error_msg, truncated,
        )
    error_msg_short.short_description = 'Error'

    # ---------- Bulk action ----------

    @admin.action(description='Reintentar enviar (solo los fallidos)')
    def reintentar_fallidos(self, request, queryset):
        """
        Re-encola los envíos fallidos del queryset creando NUEVOS
        registros pendientes (no edita los viejos — mantenemos
        histórico). Agrupa por lista para encolar UNA task de worker
        por lista (evita N tasks paralelas).
        """
        from articulo.models import DifusionListaPreciosEnvio
        from collections import defaultdict

        # Solo los fallidos: si seleccionan enviados/pendientes, los
        # ignoramos (no tiene sentido reintentar un OK ni acelerar uno
        # que ya está en cola).
        fallidos = queryset.filter(status=DifusionListaPreciosEnvio.STATUS_FALLIDO)

        if not fallidos.exists():
            self.message_user(
                request,
                'Ninguno de los seleccionados está en estado "fallido". '
                'Solo se reintentan los fallidos.',
                level=messages.WARNING,
            )
            return

        # Agrupar por lista para encolar 1 task por lista.
        por_lista: dict[int, list] = defaultdict(list)
        for envio in fallidos:
            por_lista[envio.lista_id].append(envio)

        nuevos_total = 0
        for lista_id, envios in por_lista.items():
            nuevos = []
            for viejo in envios:
                nuevos.append(DifusionListaPreciosEnvio(
                    lista_id=lista_id,
                    cliente_id=viejo.cliente_id,
                    modo=viejo.modo,
                    telefono_usado=viejo.telefono_usado,
                    status=DifusionListaPreciosEnvio.STATUS_PENDIENTE,
                    creado_por=request.user if request.user.is_authenticated else None,
                ))
            DifusionListaPreciosEnvio.objects.bulk_create(nuevos)
            nuevos_total += len(nuevos)
            # Encolar la task que va a procesar los pendientes de esta lista.
            try:
                from django_q.tasks import async_task
                async_task('articulo.tasks_difusion.procesar_difusion', lista_id)
            except Exception:
                # Fallback inline (raro): el worker no está disponible.
                from articulo.tasks_difusion import procesar_difusion
                procesar_difusion(lista_id)

        self.message_user(
            request,
            f'Re-encolados {nuevos_total} envío(s) de {len(por_lista)} '
            f'lista(s). El worker los procesa en background.',
            level=messages.SUCCESS,
        )


class SolicitudListaClienteAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    """
    Bandeja de "clientes que pidieron la lista por WhatsApp pero no
    tienen una asignada". Las crea `wa_campania.auto_responder` cuando
    detecta el caso. El operador entra acá, ve quién pidió y va al
    editor de lista con un click para armarle una.
    """
    icon_name = 'inbox'
    list_display = (
        'cliente_link',
        'mensaje_corto',
        'estado_badge',
        'created_at',
        'accion_rapida',
    )
    list_filter = ('resuelta', 'created_at')
    search_fields = (
        'cliente__nombre', 'cliente__apellido', 'mensaje_original', 'notas',
    )
    list_select_related = ('cliente',)
    readonly_fields = ('cliente', 'mensaje_original', 'created_at', 'resuelta_at')
    fields = ('cliente', 'mensaje_original', 'resuelta', 'notas',
              'created_at', 'resuelta_at')
    actions = ['marcar_resueltas']
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        # Las solicitudes se crean SOLO desde el auto-responder. El
        # operador no las edita a mano.
        return False

    def get_queryset(self, request):
        # Mostrar pendientes primero por default — son las accionables.
        return super().get_queryset(request).order_by('resuelta', '-created_at')

    # ---------- Columnas custom ----------
    def cliente_link(self, obj):
        # Link al cliente en el admin de cliente para ver su info
        # completa (teléfono, dirección, saldo, historial).
        return format_html(
            '<a href="/admin/cliente/cliente/{}/change/">{}</a>',
            obj.cliente_id, obj.cliente.nombre_completo(),
        )
    cliente_link.short_description = 'Cliente'
    cliente_link.admin_order_field = 'cliente__nombre'

    def mensaje_corto(self, obj):
        if not obj.mensaje_original:
            return format_html('<span style="color:#94a3b8;">—</span>')
        truncated = obj.mensaje_original[:60] + ('…' if len(obj.mensaje_original) > 60 else '')
        return format_html(
            '<span style="font-style:italic;color:#475569;">"{}"</span>',
            truncated,
        )
    mensaje_corto.short_description = 'Qué dijo'

    def estado_badge(self, obj):
        if obj.resuelta:
            return format_html(
                '<span style="background:#d1fae5;color:#047857;padding:3px 10px;'
                'border-radius:12px;font-size:11px;font-weight:600;">✓ RESUELTA</span>'
            )
        return format_html(
            '<span style="background:#fef3c7;color:#92400e;padding:3px 10px;'
            'border-radius:12px;font-size:11px;font-weight:600;">⏳ PENDIENTE</span>'
        )
    estado_badge.short_description = 'Estado'
    estado_badge.admin_order_field = 'resuelta'

    def accion_rapida(self, obj):
        """Botón directo al editor para armarle la lista al cliente."""
        if obj.resuelta:
            return format_html('<span style="color:#94a3b8;">—</span>')
        # El editor acepta ?cliente_id=N para pre-seleccionar. Si el
        # editor todavía no soporta ese param, el operador igual llega
        # a la pantalla y manualmente elige el cliente del autocomplete.
        return format_html(
            '<a href="/articulos/lista-precios/?cliente_id={}" '
            'style="background:#2563eb;color:white;padding:4px 10px;'
            'border-radius:6px;text-decoration:none;font-size:12px;font-weight:600;">'
            '➜ Armar lista</a>',
            obj.cliente_id,
        )
    accion_rapida.short_description = 'Acción'

    # ---------- Bulk action ----------
    @admin.action(description='Marcar como resueltas (sin abrir cada una)')
    def marcar_resueltas(self, request, queryset):
        from django.utils import timezone
        n = queryset.filter(resuelta=False).update(
            resuelta=True, resuelta_at=timezone.now(),
        )
        self.message_user(
            request,
            f'{n} solicitud(es) marcadas como resueltas.',
            level=messages.SUCCESS if n else messages.INFO,
        )

    def save_model(self, request, obj, form, change):
        # Cuando se tilda "resuelta" desde el form, sellamos timestamp.
        # Si se destilda (raro), limpiamos.
        from django.utils import timezone
        if obj.resuelta and not obj.resuelta_at:
            obj.resuelta_at = timezone.now()
        elif not obj.resuelta:
            obj.resuelta_at = None
        super().save_model(request, obj, form, change)


# # admin.site.get_app_list = get_app_list
# admin.site.site_header = 'Administrador Osvaldo'
# admin.site.index_title = 'Osvaldo Administrador'
# admin.site.site_title = 'Osvaldo Programs'
# admin.site.register(Articulo, ArticuloAdmin)
