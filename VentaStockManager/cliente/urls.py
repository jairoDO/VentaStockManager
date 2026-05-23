from django.urls import path
from .views import (
    mostrar_todos_los_clientes,
    procesar_nuevo_cliente,
    ListaArticulosView,
    ClienteAutocomplete,
    extracto_cliente,
)
from .views_movimientos import registrar_movimiento

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
    # Pantalla custom Alpine para registrar pagos/deudas. Reemplaza el
    # form de /admin/cliente/movimientocuenta/add/ que tenía bugs
    # visuales irresolubles con material-admin (inputs invisibles).
    path('<int:cliente_id>/movimiento/', registrar_movimiento, name='cliente_registrar_movimiento'),
]

# NOTA: la gestión de usuarios (/usuarios/...) vive en cliente/urls_usuarios.py
# y se monta en raíz desde VentaStockManager/urls.py. Va en módulo aparte
# porque conceptualmente NO es de clientes (es admin del sistema) y queremos
# que la URL pública sea /usuarios/, no /clientes/usuarios/.
