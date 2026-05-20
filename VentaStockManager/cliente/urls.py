from django.urls import path
from .views import (
    mostrar_todos_los_clientes,
    procesar_nuevo_cliente,
    ListaArticulosView,
    ClienteAutocomplete,
    extracto_cliente,
)

urlpatterns = [
    path('mostrar_todos_los_clientes/', mostrar_todos_los_clientes, name='clientes'),
    path('procesar_nuevo_cliente/', procesar_nuevo_cliente, name='procesar_cliente'),
    path('mis-articulos/', ListaArticulosView.as_view(), name='mis_articulos'),
    path('cliente-autocomplete/', ClienteAutocomplete.as_view(), name='cliente-autocomplete'),
    # Pantalla custom "extracto del cliente" — accesible desde el
    # ClienteAdmin (botón "Ver extracto") o directamente por URL.
    # Este urls.py cuelga de /clientes/ en el root URLconf, así que
    # la URL pública final es /clientes/<id>/extracto/.
    path('<int:cliente_id>/extracto/', extracto_cliente, name='cliente_extracto'),
]
