"""
Admin de la configuración general.

Pattern singleton: el admin bloquea `add` (ya hay una) y `delete`
(no debería desaparecer nunca). Solo superusers pueden ver/editar
para evitar que un vendedor cambie la retención por accidente.
"""

from __future__ import annotations

from django.contrib import admin

from configuracion.models import ConfiguracionGeneral


class ConfiguracionGeneralAdmin(admin.ModelAdmin):
    icon_name = 'tune'
    list_display = ('__str__', 'updated_at')
    readonly_fields = ('updated_at', 'link_panel_tareas')
    fieldsets = (
        ('Retención de ventas', {
            'fields': ('ventas_retencion_meses',),
            'description': (
                '<b>Importante:</b> las ventas anteriores a este umbral '
                'se <b>archivan</b> automáticamente (no se borran). '
                'Quedan ocultas del listado normal pero la data sigue '
                'en la DB y se puede consultar con el filtro "Archivadas".'
            ),
        }),
        ('Tareas automáticas', {
            'fields': ('link_panel_tareas',),
            'description': (
                'Las tareas asíncronas (archivado, sync de Sheets, etc.) '
                'corren periódicamente por cron, pero también podés '
                'dispararlas a mano cuando lo necesites.'
            ),
        }),
        ('Estado', {
            'fields': ('updated_at',),
        }),
    )

    def link_panel_tareas(self, obj):
        """Link directo al panel de tareas manuales."""
        from django.utils.html import format_html
        return format_html(
            '<a href="/configuracion/panel-tareas/" target="_blank" '
            'style="display: inline-block; padding: 6px 14px; '
            'background: #2196f3; color: white; border-radius: 4px; '
            'text-decoration: none; font-weight: 500;">'
            '⚙ Abrir panel de tareas</a>'
        )
    link_panel_tareas.short_description = 'Ejecutar tareas a mano'

    def has_module_permission(self, request):
        return request.user.is_authenticated and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.is_superuser

    def has_add_permission(self, request):
        # Singleton: si ya hay una, no se puede crear otra.
        # El helper `get_config()` se encarga de crearla la primera
        # vez automáticamente, así que desde el admin nunca hace
        # falta el botón "Add".
        return False

    def has_delete_permission(self, request, obj=None):
        # Nunca permitir borrar — los commands dependen de esta fila.
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.is_superuser
