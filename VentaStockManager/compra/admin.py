from django.contrib import admin
#from .forms import CompraAdminForm
from .models import Proveedor, Compra, DetalleCompra
from .forms import CompraAdminForm
class DetalleCompraInline(admin.TabularInline):
    model = DetalleCompra
    extra = 1  # Allows adding one extra detail by default

class CompraAdmin(admin.ModelAdmin):
      
    icon_name = "shopping_cart"
    inlines = [DetalleCompraInline]
    list_display = ('fecha_compra', 'proveedor', 'cantidad_compras_realizadas', 'monto_total')
    form = CompraAdminForm
    def cantidad_compras_realizadas(self, obj):
        return obj.detalles_compra.count()

    cantidad_compras_realizadas.short_description = 'Cantidad de compras realizadas'

    
    def monto_total(self, obj):
        total = sum(detalle.precio_unitario * detalle.cantidad for detalle in obj.detalles_compra.all())
        return f"${total:.2f}"

    monto_total.short_description = 'total de la compra'

  
class ProvedorAdmin(admin.ModelAdmin):
      icon_name = "local_shipping"
      ordering = ['nombre']
      model = Proveedor
      # Necesario para que `autocomplete_fields = ('proveedor',)` en
      # ArticuloAdmin pueda buscar proveedores en el dropdown.
      search_fields = ('nombre',)
      list_display = ('nombre', 'cantidad_articulos')

      def get_queryset(self, request):
          # Count anotado para no hacer una query por fila al
          # renderizar `cantidad_articulos`.
          from django.db.models import Count
          return super().get_queryset(request).annotate(
              _n_articulos=Count('articulos'),
          )

      def cantidad_articulos(self, obj):
          return getattr(obj, '_n_articulos', None) or obj.articulos.count()
      cantidad_articulos.short_description = 'Artículos'
      cantidad_articulos.admin_order_field = '_n_articulos'
      

# admin_site.site.register(Proveedor, ProvedorAdmin)
# 
# admin_site.site.register(Compra, CompraAdmin)


#@admin.register(Compra)
#lass CompraAdmin(admin.ModelAdmin):
  #  form = CompraAdminForm
####


