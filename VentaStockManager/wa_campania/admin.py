"""
Admin para campañas de WhatsApp.

Diseño:
  - `CampaniaAdmin` permite crear, editar y disparar campañas. Solo
    los superusers ven el menú — para mitigar el riesgo de que se
    mande un broadcast por accidente.
  - `EnvioWhatsappAdmin` es read-only: el operador NO debería estar
    editando estados a mano. Sirve para auditar quién recibió qué.
  - El "envío" se dispara con una `admin action` sobre una campaña
    seleccionada. Esto evita customizar el change_view: usamos el
    flujo nativo del admin.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html
from django_q.tasks import async_task

from wa_campania.audiencia import resolver_clientes
from wa_campania.models import Campania, EnvioWhatsapp
from wa_campania.tasks import crear_envios_pendientes


class _SuperuserOnlyMixin:
    """
    Las campañas tocan dinero (publicidad masiva, posible ban de
    WhatsApp) — solo superusers pueden ver/editar.
    """

    def has_module_permission(self, request):
        return request.user.is_authenticated and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_authenticated and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.is_superuser


class CampaniaAdmin(_SuperuserOnlyMixin, admin.ModelAdmin):
    icon_name = 'campaign'
    list_display = (
        'nombre',
        'estado',
        'total_envios_display',
        'enviados_ok_display',
        'fallidos_display',
        'creado_por',
        'created_at',
        'enviada_at',
    )
    list_filter = ('estado', 'created_at')
    search_fields = ('nombre', 'mensaje')
    readonly_fields = (
        'estado',
        'creado_por',
        'created_at',
        'enviada_at',
        'preview_audiencia',
        'resumen_envios',
    )
    fieldsets = (
        ('Mensaje', {
            'fields': ('nombre', 'mensaje', 'adjunto'),
            'description': (
                'Variables disponibles en el mensaje: '
                '<code>{{nombre}}</code>, <code>{{apellido}}</code>, '
                '<code>{{saldo}}</code>. Se sustituyen al enviar.'
            ),
        }),
        ('Audiencia', {
            'fields': ('audiencia_filtro', 'preview_audiencia'),
            'description': (
                'JSON con los filtros. Ejemplo: <pre>'
                '{"compraron_ultimos_dias": 30, "solo_con_whatsapp_valido": true}'
                '</pre>'
                'Marcar <code>"todos": true</code> para enviar a TODOS '
                'los clientes con WhatsApp válido.'
            ),
        }),
        ('Estado', {
            'fields': ('estado', 'creado_por', 'created_at', 'enviada_at', 'resumen_envios'),
        }),
    )
    actions = ['accion_enviar_campania']

    def total_envios_display(self, obj):
        return obj.total_envios
    total_envios_display.short_description = 'Envíos'

    def enviados_ok_display(self, obj):
        n = obj.total_enviados_ok
        if n == 0:
            return '-'
        return format_html(
            '<span style="color: #2e7d32; font-weight: bold;">{}</span>',
            n,
        )
    enviados_ok_display.short_description = 'Enviados ✓'

    def fallidos_display(self, obj):
        n = obj.total_fallidos
        if n == 0:
            return '-'
        return format_html(
            '<span style="color: #c62828; font-weight: bold;">{}</span>',
            n,
        )
    fallidos_display.short_description = 'Fallidos'

    def preview_audiencia(self, obj):
        # Mostramos cuántos clientes va a alcanzar la campaña según
        # los filtros actuales. Esto previene el típico bug "guardé
        # mal el filtro y mandó a 0 personas / a 1126 personas".
        if not obj or not obj.pk:
            return '(guardar primero para ver el preview)'
        try:
            n = resolver_clientes(obj.audiencia_filtro).count()
        except Exception as exc:
            return format_html('<span style="color: #c62828;">Error: {}</span>', str(exc))
        if n == 0:
            return format_html(
                '<span style="color: #c62828;">⚠ 0 destinatarios — revisar filtros</span>'
            )
        return format_html(
            '<span style="color: #2e7d32; font-weight: bold;">{} destinatarios</span>',
            n,
        )
    preview_audiencia.short_description = 'Preview audiencia'

    def resumen_envios(self, obj):
        if not obj or not obj.pk:
            return '-'
        total = obj.total_envios
        if total == 0:
            return 'Sin envíos todavía.'
        ok = obj.total_enviados_ok
        ko = obj.total_fallidos
        return format_html(
            'Total: <b>{}</b> | Enviados OK: <b style="color: #2e7d32;">{}</b> '
            '| Fallidos: <b style="color: #c62828;">{}</b>',
            total, ok, ko,
        )
    resumen_envios.short_description = 'Resumen'

    def save_model(self, request, obj, form, change):
        # Firmamos quién creó la campaña (auditlog también lo
        # registra, pero el campo "duro" lo usamos en list_display).
        if not change and not obj.creado_por_id:
            obj.creado_por = request.user
        # Si guardan con audiencia_filtro vacío, sembramos defaults.
        if not obj.audiencia_filtro:
            obj.audiencia_filtro = dict(Campania.AUDIENCIA_DEFAULT)
        super().save_model(request, obj, form, change)

    @admin.action(description='Enviar campaña a la audiencia configurada')
    def accion_enviar_campania(self, request, queryset):
        # Para evitar disparos accidentales, solo procesamos UNA
        # campaña por vez. Si seleccionaron varias, abortamos.
        if queryset.count() != 1:
            self.message_user(
                request,
                'Seleccioná UNA sola campaña para enviar.',
                level=messages.ERROR,
            )
            return
        campania = queryset.first()
        if campania.estado != Campania.ESTADO_BORRADOR:
            self.message_user(
                request,
                f'La campaña ya está en estado "{campania.get_estado_display()}". '
                f'Solo se pueden enviar campañas en borrador.',
                level=messages.WARNING,
            )
            return
        # Crear envíos pendientes (síncrono — es rápido, queries bulk).
        n = crear_envios_pendientes(campania)
        if n == 0:
            self.message_user(
                request,
                'La audiencia resolvió a 0 clientes. Revisá los filtros.',
                level=messages.ERROR,
            )
            return
        # Encolar el procesamiento real en django-q2.
        async_task('wa_campania.tasks.enviar_campania', campania.id)
        self.message_user(
            request,
            f'Campaña encolada. Se van a procesar {n} envíos en background '
            f'(uno cada ~4 segundos). Refrescá esta página para ver el progreso.',
            level=messages.SUCCESS,
        )


class EnvioWhatsappAdmin(_SuperuserOnlyMixin, admin.ModelAdmin):
    """Read-only. Para auditar y debuggear, no para editar."""
    icon_name = 'forward_to_inbox'
    list_display = (
        'campania',
        'cliente',
        'telefono_usado',
        'status',
        'sent_at',
        'error_msg_short',
    )
    list_filter = ('status', 'campania', 'sent_at')
    search_fields = ('cliente__nombre', 'cliente__apellido', 'telefono_usado', 'error_msg')
    readonly_fields = [
        'campania', 'cliente', 'telefono_usado', 'mensaje_renderizado',
        'status', 'error_msg', 'sent_at', 'created_at',
    ]
    date_hierarchy = 'created_at'

    def error_msg_short(self, obj):
        if not obj.error_msg:
            return '-'
        return obj.error_msg[:60] + ('…' if len(obj.error_msg) > 60 else '')
    error_msg_short.short_description = 'Error'

    def has_add_permission(self, request):
        # Ni siquiera los superusers crean envíos a mano.
        return False
