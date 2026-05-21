"""
URLs de la app wa_campania.

Por ahora solo el panel de conexión + sus endpoints proxy. Las
Campaña/EnvioWhatsapp se gestionan desde el admin clásico (registro
en MyAdminSite), no exponen URLs propias.
"""

from django.urls import path

from . import views


urlpatterns = [
    path('wa-campania/conexion/', views.panel_conexion, name='wa_panel_conexion'),
    path(
        'wa-campania/api/conexion/status/',
        views.api_conexion_status,
        name='wa_api_conexion_status',
    ),
    path(
        'wa-campania/api/conexion/qr.png',
        views.api_conexion_qr,
        name='wa_api_conexion_qr',
    ),
    path(
        'wa-campania/api/conexion/logout/',
        views.api_conexion_logout,
        name='wa_api_conexion_logout',
    ),
    path(
        'wa-campania/api/conexion/restart/',
        views.api_conexion_restart,
        name='wa_api_conexion_restart',
    ),
    path(
        'wa-campania/api/conexion/test/',
        views.api_conexion_test,
        name='wa_api_conexion_test',
    ),
    # Endpoint que llama el wa-bot por cada mensaje entrante.
    # Auth por X-Bot-Token (NO session). Ver wa_campania/auto_responder.py
    # para la lógica de decisión.
    path(
        'wa-campania/api/incoming/',
        views.api_incoming_message,
        name='wa_api_incoming',
    ),
]
