from django.contrib import admin
from datetime  import date
from django.contrib import messages
from django.core.management import call_command
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html
# Register your models here.
from articulo.models import Articulo, Categoria, ListaPrecios, ListaPreciosItem, ReglaCategoria
from django_q.tasks import async_task
from .task import actualizar_precios_articulos_desde_drive
import decimal

class ArticuloAdmin(admin.ModelAdmin):

    list_display = (
        'marca', 'codigo_interno', 'codigo', 'nombre', 'stock',
        'precio_minorista', 'categoria_badge', 'proveedor_nombre',
        'vence_dentro_de_60_dias', 'total_venta_por_articulo',
    )
    list_filter = ('categoria', 'proveedor', 'marca')
    list_select_related = ('categoria', 'proveedor')  # evita N+1 al renderizar columnas FK
    search_fields = ("nombre", 'codigo', 'codigo_interno')
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
            return None  # vuelve al changelist

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


class CategoriaAdmin(admin.ModelAdmin):
    icon_name = 'category'
    list_display = ('nombre_con_color', 'descripcion_corta', 'cantidad_articulos', 'cantidad_reglas')
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
    icon_name = 'rule'
    list_display = ('categoria', 'palabras_clave_resumen', 'prioridad', 'activa', 'updated_at')
    list_filter = ('activa', 'categoria')
    search_fields = ('categoria__nombre',)
    autocomplete_fields = ('categoria',)
    ordering = ('prioridad', 'categoria__nombre')

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


class ListaPreciosAdmin(admin.ModelAdmin):
    """
    Admin de listas de precios. Permite crear/editar a mano una lista
    eligiendo cliente, descuento opcional y artículos uno por uno via
    el inline. La pantalla custom (próxima fase) va a hacer esto más
    cómodo con filtros y "agregar todos los de la categoría X".
    """
    icon_name = 'price_change'
    list_display = (
        'nombre', 'cliente_nombre', 'cantidad_items_display',
        'descuento_porcentaje', 'creado_por', 'updated_at',
    )
    list_filter = ('updated_at',)
    search_fields = ('nombre', 'cliente__nombre', 'cliente__apellido')
    autocomplete_fields = ('cliente',)
    readonly_fields = ('creado_por', 'created_at', 'updated_at')
    inlines = [ListaPreciosItemInline]

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

    def save_model(self, request, obj, form, change):
        if not change and not obj.creado_por_id:
            obj.creado_por = request.user
        super().save_model(request, obj, form, change)


# # admin.site.get_app_list = get_app_list
# admin.site.site_header = 'Administrador Osvaldo'
# admin.site.index_title = 'Osvaldo Administrador'
# admin.site.site_title = 'Osvaldo Programs'
# admin.site.register(Articulo, ArticuloAdmin)
