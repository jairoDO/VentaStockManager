from django.urls import re_path, path
from django.conf.urls import handler404

from .views import custom_404_view, redirect_to_ventas
handler404 = custom_404_view

from venta.views import (
    venta_detalle, ventas_por_vendedor, calcular_ganancia_articulos, comprovante_de_venta, ver_pedido, 
    ArticuloAutocomplete, ventas_recientes_por_vendedor, ventas_mensual_por_vendedor, generar_pdf_pedido, generar_pdf_pedidos

)
from .views import ClienteCreateView, ClienteUpdateView

# Vistas de la pantalla nueva de venta (Alpine + JSON APIs). Las
# importamos aparte para que quede claro al lector qué es "pantalla
# vieja del admin" vs "pantalla nueva custom".
from venta.views_nueva import (
    venta_nueva,
    venta_editar,
    api_articulos_buscar,
    api_clientes_buscar,
    api_cliente_saldo,
    api_venta_guardar,
)

# "Caja del día" — resumen de ventas y cobranza para reconciliar la
# caja al final del día. Server-rendered, simple, con selector de fecha.
from venta.views_caja import caja_del_dia

# Pantalla intermedia "Generar PDFs y registrar pago" (disparada desde
# la acción del PedidoAdmin).
from venta.views_cobrar import cobrar_y_generar_pdf

urlpatterns = [
    # Pantalla nueva de venta (Alpine + Tailwind). Estas rutas tienen
    # que ir ANTES que las legacy de comprobante para evitar que el
    # converter <int:venta_id> capture "nueva" — Django lo rechaza
    # igual por tipo, pero ser explícito acá ahorra debugging si
    # mañana se agrega una ruta similar.
    path('venta/nueva/', venta_nueva, name='venta_nueva'),
    path('venta/<int:id>/editar/', venta_editar, name='venta_editar'),
    path('venta/api/articulos/buscar/', api_articulos_buscar, name='venta_api_articulos_buscar'),
    path('venta/api/clientes/buscar/', api_clientes_buscar, name='venta_api_clientes_buscar'),
    path('venta/api/clientes/<int:cliente_id>/saldo/', api_cliente_saldo, name='venta_api_cliente_saldo'),
    path('venta/api/guardar/', api_venta_guardar, name='venta_api_guardar'),

    re_path(
            r'^venta/(?P<venta_id>\d+)/detalle_de_venta',
            venta_detalle,
            name='venta_detalle'),
    re_path(
            r'^ventas_por_vendedor/(?P<id_vendedor>\d+)/$',
             ventas_por_vendedor, 
             name='ventas_por_vendedor'),
    re_path(
            r'^ventas_recientes_por_vendedor/(?P<id_vendedor>\d+)/$',
             ventas_recientes_por_vendedor, 
             name='ventas_recientes_por_vendedor'),    
    re_path(
            r'^ventas_mensual_por_vendedor/(?P<id_vendedor>\d+)/$',
             ventas_mensual_por_vendedor, 
             name='ventas_mensual_por_vendedor'),    

    path('ganancia_por_articulos/', calcular_ganancia_articulos, name='ganancia_por_articulos'),    
    path('articulo-autocomplete/', ArticuloAutocomplete.as_view(), name='articulo-autocomplete'),
    path('venta/<int:venta_id>/', comprovante_de_venta, name='comprovante_de_venta'),
    path('venta/pedido/<int:pedido_id>/', ver_pedido, name='ver_pedido'),
    path('pedido/generar-pdf/<int:pedido_id>', generar_pdf_pedido, name='generar_pdf_pedido'),
    path('cliente/add/', ClienteCreateView.as_view(), name='cliente_add'),
    path('cliente/<int:pk>/edit/', ClienteUpdateView.as_view(), name='cliente_edit'),
    path('venta/pedido/generar-pdfs/', generar_pdf_pedidos, name='generar_pdf_pedidos'),
    path('admin/venta/', redirect_to_ventas, name='redirect_to_ventas'),
    # Caja del día — pantalla read-only para reconciliar caja.
    path('caja/', caja_del_dia, name='caja_del_dia'),
    # Pantalla intermedia "Generar PDFs y registrar pago" — disparada
    # desde la acción del PedidoAdmin con ?pedidos_ids=1,2,3.
    path('venta/pedido/cobrar-y-generar-pdf/', cobrar_y_generar_pdf,
         name='cobrar_y_generar_pdf'),
]
# url(r'^/(?P<venta_id>\d+)/detalle/$', views.venta_detalle, name='category-detail'),

  