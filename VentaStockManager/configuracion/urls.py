"""URLs de la app configuración."""

from django.urls import path

from configuracion.views import panel_tareas


urlpatterns = [
    # Panel para disparar tareas asíncronas a mano. Sirve como
    # complemento del cron de django-q2 (que tarda en correr) y
    # como herramienta de debug para el operador.
    path('panel-tareas/', panel_tareas, name='configuracion_panel_tareas'),
]
