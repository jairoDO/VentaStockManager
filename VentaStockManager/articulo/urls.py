from django.urls import path
from .views import mostrar_articulos, lista_precios
from .views_grilla import grilla_precios, api_grilla_listar, api_grilla_guardar
from .views_lista_precios import (
    lista_precios_pantalla,
    api_listas_del_cliente,
    api_detalle_lista,
    api_articulos_disponibles,
    api_guardar_lista,
    api_pdf_lista,
)

urlpatterns = [
    path('mostrar_articulos/', mostrar_articulos, name='mostrar_articulos'),
    path('lista_precios/', lista_precios, name='lista_precio'),
    # Grilla de precios: pantalla custom tipo planilla. El template
    # se renderiza desde `grilla_precios` y los datos los carga el
    # front contra los endpoints `api/grilla/*`.
    #
    # Las urls incluyen el prefijo `articulos/` a propósito: el
    # urlconf raíz mete `articulo.urls` en la raíz `""`, así que
    # las otras urls (`mostrar_articulos/`, `lista_precios/`) quedan
    # también pegadas a la raíz. Para que la grilla viva en un
    # namespace claro (y no contaminemos `/api/...` global), las
    # prefijamos acá adentro.
    path('articulos/grilla-precios/', grilla_precios, name='grilla_precios'),
    path('articulos/api/grilla/', api_grilla_listar, name='grilla_precios_api_listar'),
    path('articulos/api/grilla/guardar/', api_grilla_guardar, name='grilla_precios_api_guardar'),

    # Pantalla custom de lista de precios. Igual filosofía que la
    # grilla: el template carga todo vía estas APIs.
    path('articulos/lista-precios/', lista_precios_pantalla, name='lista_precios_pantalla'),
    path(
        'articulos/api/lista-precios/cliente/<int:cliente_id>/listas/',
        api_listas_del_cliente,
        name='lista_precios_api_listas_cliente',
    ),
    path(
        'articulos/api/lista-precios/cliente/<int:cliente_id>/lista/<int:lista_id>/',
        api_detalle_lista,
        name='lista_precios_api_detalle',
    ),
    path(
        'articulos/api/lista-precios/articulos/',
        api_articulos_disponibles,
        name='lista_precios_api_articulos',
    ),
    path(
        'articulos/api/lista-precios/guardar/',
        api_guardar_lista,
        name='lista_precios_api_guardar',
    ),
    path(
        'articulos/api/lista-precios/pdf/<int:lista_id>/',
        api_pdf_lista,
        name='lista_precios_api_pdf',
    ),
]
