"""
URL configuration for VentaStockManager project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.views.generic.base import RedirectView
from .admin import admin_site
from venta.views import redirect_to_ventas
urlpatterns = [
    
    path('admin/venta/', redirect_to_ventas, name='redirect_to_ventas'),
    path('admin/articulo/', RedirectView.as_view(url='/admin/articulo/articulo/', permanent=True), name='redirect_to_articulos'),
    path('admin/cliente/', RedirectView.as_view(url='/admin/cliente/cliente/', permanent=True), name='redirect_to_clientes'),
    path('admin/compra/', RedirectView.as_view(url='/admin/compra/compra/', permanent=True), name='redirect_to_compras'),
    path('admin/vendedor/', RedirectView.as_view(url='/admin/vendedor/vendedor/', permanent=True), name='redirect_to_vendedores'),
    path('admin/compra/', RedirectView.as_view(url='/admin/compra/compra/', permanent=True), name='redirect_to_compras'),
    path('admin/vendedor/', RedirectView.as_view(url='/admin/vendedor/vendedor/', permanent=True), name='redirect_to_vendedores'),
    path('admin/factura_config/factura_configuration/', RedirectView.as_view(url='/admin/factura_config/facturaconfiguration/', permanent=True), name='redirect_to_factura_config'),
    path("admin/", admin_site.urls),    
    # path('admin/', include('material.admin.urls')),
    
    path("clientes/", include('cliente.urls')),
    path("", include('compra.urls')),
    path("", include('articulo.urls')),
    path("", include('venta.urls')),
    path("", include('vendedor.urls')),
    # Panel de tareas manuales y futuras vistas de la app configuración.
    path("configuracion/", include('configuracion.urls')),
    # Panel de conexión WhatsApp (QR, status, logout) — solo superuser.
    path("", include('wa_campania.urls')),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('', RedirectView.as_view(url='/admin/', permanent=True)),  # Redirigir a admin
]

