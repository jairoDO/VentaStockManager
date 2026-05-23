from django.urls import path
from .views import mostrar_articulos, lista_precios
from .views_grilla import (
    grilla_precios, api_grilla_listar, api_grilla_guardar, api_grilla_eliminar,
)
from .views_reglas import api_reglas_preview, api_reglas_aplicar_ahora
from .views_lista_precios import (
    lista_precios_pantalla,
    lista_precios_difundir,
    api_lista_precios_difundir_clientes,
    api_lista_precios_difundir_enviar,
    api_lista_precios_difundir_progreso,
    api_listas_del_cliente,
    api_detalle_lista,
    api_detalle_lista_directo,
    api_articulos_disponibles,
    api_guardar_lista,
    api_pdf_lista,
    api_compartir_lista,
    api_desactivar_link_lista,
    vista_publica_lista,
    vista_pdf_publica,
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
    # Eliminar artículos seleccionados. Solo superuser (decorador
    # @superuser_required en la view). Borra uno por uno y reporta
    # cuáles fallaron (los con ventas asociadas no se pueden borrar).
    path('articulos/api/grilla/eliminar/', api_grilla_eliminar, name='grilla_precios_api_eliminar'),

    # Preview en vivo de qué artículos matchean una regla mientras el
    # operador escribe palabras clave en /admin/articulo/categoria/N/change/.
    # Doble endpoint: GET preview (cuenta + muestra) y POST aplicar-ahora
    # (asigna sin esperar al cron). Ver `views_reglas.py`.
    path('articulos/api/reglas/preview/', api_reglas_preview, name='reglas_api_preview'),
    path('articulos/api/reglas/aplicar-ahora/', api_reglas_aplicar_ahora, name='reglas_api_aplicar_ahora'),

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
    # Atajo: detalle por lista_id directo (sin cliente_id). Lo usa la
    # pantalla custom cuando viene del admin con ?lista_id=N.
    path(
        'articulos/api/lista-precios/<int:lista_id>/detalle-directo/',
        api_detalle_lista_directo,
        name='lista_precios_api_detalle_directo',
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
    # Compartir / desactivar link público. Solo se acceden por POST
    # (no queremos que un GET accidental compartido en un slack rompa
    # links existentes).
    path(
        'articulos/api/lista-precios/<int:lista_id>/compartir/',
        api_compartir_lista,
        name='lista_precios_api_compartir',
    ),
    path(
        'articulos/api/lista-precios/<int:lista_id>/desactivar-link/',
        api_desactivar_link_lista,
        name='lista_precios_api_desactivar_link',
    ),

    # Pantalla de difusión manual: el operador ve sus clientes
    # filtrables y va apretando "Enviar" en cada uno (o un par bulk)
    # — abre wa.me en pestaña nueva. No usa el wa-bot, todo manual.
    path(
        'articulos/lista-precios/<int:lista_id>/difundir/',
        lista_precios_difundir,
        name='lista_precios_difundir',
    ),
    path(
        'articulos/api/lista-precios/<int:lista_id>/difundir/clientes/',
        api_lista_precios_difundir_clientes,
        name='lista_precios_difundir_api_clientes',
    ),
    # Difundir v2: envío automático via wa-bot.
    path(
        'articulos/api/lista-precios/<int:lista_id>/difundir/enviar/',
        api_lista_precios_difundir_enviar,
        name='lista_precios_difundir_api_enviar',
    ),
    path(
        'articulos/api/lista-precios/<int:lista_id>/difundir/progreso/',
        api_lista_precios_difundir_progreso,
        name='lista_precios_difundir_api_progreso',
    ),

    # Vistas PÚBLICAS (sin auth). Las dejamos bajo el prefijo `/p/`
    # para que sea obvio en logs/access que son endpoints públicos
    # y nunca se confundan con el namespace interno `/articulos/`.
    # El UUID del path matchea con `<uuid:token>` para validar el
    # formato antes de tocar la DB.
    path(
        'p/lista-precios/<uuid:token>/',
        vista_publica_lista,
        name='lista_precios_publica_web',
    ),
    path(
        'p/lista-precios/<uuid:token>/pdf/',
        vista_pdf_publica,
        name='lista_precios_publica_pdf',
    ),
]
