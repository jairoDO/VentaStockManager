"""
URLconf separado para la pantalla custom de gestión de Rubros.

Conceptualmente NO es de `articulo/urls.py` (que cuelga de /articulos/):
queremos URL pública /rubros/ en raíz, igual que /usuarios/. Se monta
desde VentaStockManager/urls.py.
"""
from django.urls import path

from .views_rubros import (
    gestion_rubros, api_crear_rubro, api_editar_rubro,
    api_eliminar_rubro, api_asignar_categorias,
)

urlpatterns = [
    path('rubros/', gestion_rubros, name='gestion_rubros'),
    path('rubros/crear/', api_crear_rubro, name='api_crear_rubro'),
    path('rubros/<int:rubro_id>/editar/', api_editar_rubro, name='api_editar_rubro'),
    path('rubros/<int:rubro_id>/eliminar/', api_eliminar_rubro, name='api_eliminar_rubro'),
    path('rubros/<int:rubro_id>/asignar-categorias/', api_asignar_categorias, name='api_asignar_categorias'),
]
