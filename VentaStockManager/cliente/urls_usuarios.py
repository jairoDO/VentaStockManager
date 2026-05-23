"""
URLconf separado para la pantalla de gestión de usuarios.

Por qué un módulo aparte (y no dentro de cliente/urls.py):
  - cliente/urls.py se monta bajo /clientes/. Si pusiéramos las rutas
    acá, terminarían en /clientes/usuarios/, que es confuso: la gestión
    de usuarios es del SISTEMA, no de clientes.
  - Acá las exponemos en raíz (/usuarios/, /usuarios/crear/, etc.) vía
    un include separado en el root URLconf.

Acceso: solo superuser (validado en views_usuarios.py).
"""
from django.urls import path

from .views_usuarios import (
    lista_usuarios, crear_usuario, cambiar_tipo,
    desactivar_usuario, resetear_password,
)

urlpatterns = [
    path('usuarios/', lista_usuarios, name='gestion_usuarios'),
    path('usuarios/crear/', crear_usuario, name='crear_usuario'),
    path('usuarios/<int:user_id>/cambiar-tipo/', cambiar_tipo, name='cambiar_tipo_usuario'),
    path('usuarios/<int:user_id>/desactivar/', desactivar_usuario, name='desactivar_usuario'),
    path('usuarios/<int:user_id>/resetear-password/', resetear_password, name='resetear_password_usuario'),
]
