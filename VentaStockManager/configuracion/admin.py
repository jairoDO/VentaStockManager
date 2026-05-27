"""
Admin de la configuración general.

Pattern singleton: el admin bloquea `add` (ya hay una) y `delete`
(no debería desaparecer nunca). Solo superusers pueden ver/editar
para evitar que un vendedor cambie la retención por accidente.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse

from configuracion.models import ConfiguracionGeneral, get_config


class ConfiguracionGeneralAdmin(admin.ModelAdmin):
    icon_name = 'tune'

    class Media:
        # Reusamos el CSS de wa_campania (mismo bug: labels readonly
        # encimados en material-admin) + nuestro propio fix para que
        # los fieldsets se vean como cards modernos consistentes con
        # las pantallas custom (grilla, lista precios, panel tareas).
        # Ver `configuracion_admin_polish.css` para los detalles.
        css = {
            'all': (
                'admin/wa_campania/admin_fixes.css',
                'admin/configuracion/polish.css',
            ),
        }

    # Singleton: como hay UNA sola fila, no tiene sentido que el operador
    # pase por el changelist. `changelist_view` redirige al detalle
    # (creándolo con defaults si la fila aún no existe).
    def changelist_view(self, request, extra_context=None):
        # Asegurar que la fila existe (1ra vez después del bootstrap).
        # `get_config` hace get_or_create con defaults razonables.
        cfg = get_config()
        return HttpResponseRedirect(
            reverse('admin:configuracion_configuraciongeneral_change', args=[cfg.pk])
        )

    list_display = ('__str__', 'updated_at')
    readonly_fields = (
        'updated_at',
        'link_panel_tareas',
        'link_panel_whatsapp',
        'recordatorios_saldo_ultima_corrida_at',
        'recordatorios_saldo_preview',
        'alerta_inactividad_ultima_corrida_at',
        'alerta_inactividad_preview',
        'auditlog_ultima_purga_at',
        'auditlog_ultima_purga_borrados',
    )
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
        ('Listas de precios', {
            'fields': ('lista_precios_link_dias',),
            'description': (
                'Configuración del link público compartible de las listas '
                'de precios. El operador puede generar un link por lista, '
                'con vencimiento automático para que no quede expuesto '
                'indefinidamente. Cambiar este valor NO afecta a links '
                'ya emitidos — solo a los que se generen de acá en más.'
            ),
        }),
        ('Integración con Google Sheets', {
            'fields': ('sheets_sync_habilitado', 'sheets_delete_sync_habilitado'),
            'description': (
                '<b>Master switch</b> de la integración con la planilla. '
                'Si la primera está apagada, la app NO sincroniza con '
                'Sheets en ninguna dirección — útil mientras migramos '
                'fuente de verdad de Sheets a la app. Cambiar acá NO '
                'requiere redeploy, toma efecto inmediato.'
                '<br><br>'
                'La opción de "delete sync" controla específicamente el '
                'borrado bidireccional (cuando borrás un artículo en la '
                'app, vacía la fila en el Sheet). Requiere que el master '
                'switch también esté prendido + que el service-account '
                'sea <b>Editor</b> del Sheet (no Viewer).'
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
        ('WhatsApp', {
            'fields': ('link_panel_whatsapp', 'auto_responder_habilitado'),
            'description': (
                'Conexión con WhatsApp para las campañas. Acá ves si el '
                'bot está vinculado a una cuenta, escaneás el QR para '
                'vincular una nueva, o desconectás la actual. Antes de '
                'cualquier envío masivo conviene pasar por acá y '
                'confirmar que dice "Conectado".<br><br>'
                '<b>Auto-responder</b>: si está prendido, cuando un cliente '
                'le manda al bot palabras como "lista", "precios", "saldo" '
                'le respondemos automáticamente con su lista o saldo. '
                'Solo responde a clientes registrados — números desconocidos '
                'NO reciben respuesta automática (los ves en WhatsApp Web '
                'normal para contestar a mano).'
            ),
        }),
        ('Recordatorios de saldo deudor', {
            'fields': (
                'recordatorios_saldo_habilitado',
                'recordatorios_saldo_preview',
                'recordatorios_saldo_dias_inactividad',
                'recordatorios_saldo_monto_minimo',
                'recordatorios_saldo_frecuencia_dias',
                'recordatorios_saldo_template',
                'recordatorios_saldo_ultima_corrida_at',
            ),
            'description': (
                'Recordatorios automáticos por WhatsApp a clientes con '
                'saldo deudor + sin compras recientes. El cron corre todos '
                'los días pero respeta la frecuencia: si ya le mandamos a '
                'un cliente hace menos de N días, lo saltea. <br><br>'
                '<b>Preview</b>: muestra cuántos clientes serían contactados '
                '<i>ahora mismo</i> con la config actual. Es el chequeo más '
                'rápido para validar antes de prender el master switch.'
            ),
        }),
        ('Alerta de clientes inactivos', {
            'fields': (
                'alerta_inactividad_habilitada',
                'alerta_inactividad_preview',
                'alerta_inactividad_dias',
                'alerta_inactividad_ultima_corrida_at',
            ),
            'description': (
                'Alerta <b>interna</b> (no manda WhatsApp) que avisa cuando '
                'un cliente que <b>solía comprar</b> dejó de hacerlo por más '
                'días que el umbral. La detección corre <b>una vez por día</b>. '
                'Las alertas aparecen en el admin (Cliente → Alertas de '
                'clientes inactivos) y en el badge del header.<br><br>'
                'Se <b>autoresuelven</b> cuando el cliente vuelve a comprar. '
                'Solo aplica a clientes con al menos una venta registrada — '
                'los que nunca compraron no generan alerta.<br><br>'
                '<b>Preview</b>: cuántos clientes están inactivos <i>ahora</i> '
                'con el umbral guardado.'
            ),
        }),
        ('Purga de auditoría', {
            'fields': (
                'auditlog_purge_habilitado',
                'auditlog_retencion_dias',
                'auditlog_ultima_purga_at',
                'auditlog_ultima_purga_borrados',
            ),
            'description': (
                'django-auditlog guarda 1 registro por cada cambio en los '
                'modelos auditados (ventas, clientes, artículos, etc). '
                'Con el tiempo la tabla crece y degrada queries — esta '
                'task corre <b>semanalmente</b> y borra los registros '
                'más viejos que la retención configurada.<br><br>'
                '<b>Default 180 días</b> (6 meses). Suficiente para '
                'auditar incidentes razonables sin guardar historial '
                'infinito. Si necesitás más para atrás, subilo. Si la '
                'tabla crece muy rápido (kioskos muy activos), bajalo. '
                'Apagar el master switch desactiva la purga (la tabla '
                'crecerá sin límite — solo recomendado en debug).'
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
            '<a href="/configuracion/panel-tareas/" '
            'style="display: inline-block; padding: 6px 14px; '
            'background: #2196f3; color: white; border-radius: 4px; '
            'text-decoration: none; font-weight: 500;">'
            '⚙ Abrir panel de tareas</a>'
        )
    link_panel_tareas.short_description = 'Ejecutar tareas a mano'

    def recordatorios_saldo_preview(self, obj):
        """
        Muestra cuántos clientes son candidatos AHORA MISMO con la
        config guardada. Es un read-only field — se calcula cada vez
        que se abre el form. Devuelve HTML con un número grande, un
        breakdown y un sample de los primeros 5 nombres.

        OJO: usa la config persistida (`obj`), no los valores del form
        que el operador puede estar editando. Para ver el efecto de
        cambios todavía sin guardar, hay que guardar primero. Esto es
        una limitación aceptable — la alternativa (refresh AJAX en vivo)
        es mucho más laburo para poco beneficio.
        """
        from django.utils.html import format_html
        from cliente.tasks_recordatorios import _clientes_elegibles

        # Materializo el iterator a una lista cortada a 5 + count total.
        # `_clientes_elegibles` aplica TODOS los filtros (puede_recibir,
        # whatsapp, saldo deudor, monto_minimo, inactividad) — la
        # única condición que NO chequea es "ya recibió uno hace poco"
        # (esa depende del histórico y la mostramos aparte).
        candidatos = list(_clientes_elegibles(obj))
        total = len(candidatos)

        if total == 0:
            return format_html(
                '<div style="padding: 12px; background: #f1f5f9; border-radius: 6px;">'
                '<div style="font-size: 24px; font-weight: 700; color: #475569;">0</div>'
                '<div style="color: #64748b; font-size: 13px;">candidatos con la config actual.</div>'
                '<div style="margin-top: 6px; font-size: 12px; color: #94a3b8;">'
                'Probá bajar "días de inactividad" o "monto mínimo", o destildar opt-in'
                ' (no recomendado).'
                '</div>'
                '</div>'
            )

        sample_html = ''.join(
            f'<li style="margin-bottom: 2px;">'
            f'{c.nombre_completo()} '
            f'<span style="color: #94a3b8; font-size: 11px;">'
            f'(saldo ${c.saldo_calc:.2f}, '
            f'última compra {c.ultima_compra or "—"})'
            f'</span>'
            f'</li>'
            for c in candidatos[:5]
        )
        mas_html = ''
        if total > 5:
            mas_html = f'<li style="color: #64748b;"><i>… y {total - 5} más</i></li>'

        return format_html(
            '<div style="padding: 12px; background: #ecfeff; border: 1px solid #67e8f9; '
            'border-radius: 6px;">'
            '<div style="font-size: 24px; font-weight: 700; color: #0e7490;">{}</div>'
            '<div style="color: #155e75; font-size: 13px;">candidatos con la config actual.</div>'
            '<details style="margin-top: 8px;">'
            '<summary style="cursor: pointer; color: #0e7490;">Ver muestra</summary>'
            '<ul style="margin: 6px 0 0 0; padding-left: 20px; font-size: 12px;">{}{}</ul>'
            '</details>'
            '<div style="margin-top: 8px; padding: 8px; background: #fefce8; '
            'border-radius: 4px; font-size: 11px; color: #854d0e;">'
            '⚠ El "preview" usa los valores <b>guardados</b>. Para ver el efecto de '
            'cambios en este form, guardalos primero y volvé a entrar.'
            '</div>'
            '</div>',
            total, format_html(sample_html), format_html(mas_html),
        )
    recordatorios_saldo_preview.short_description = '👁 Preview de candidatos'

    def alerta_inactividad_preview(self, obj):
        """
        Cuántos clientes están inactivos AHORA con el umbral guardado.
        Read-only, calculado al abrir el form. Usa la misma lógica de
        elegibilidad que la task (clientes que compraron antes + última
        compra anterior al umbral), pero NO chequea anti-spam (eso solo
        importa al crear alertas, no para el conteo informativo).
        """
        from datetime import timedelta
        from django.db.models import Max
        from django.utils import timezone
        from django.utils.html import format_html
        from cliente.models import Cliente

        dias = int(obj.alerta_inactividad_dias or 30)
        hoy = timezone.now().date()
        desde = hoy - timedelta(days=dias)

        qs = (
            Cliente.objects
            .annotate(ultima_compra=Max('ventas__fecha_compra'))
            .filter(ultima_compra__isnull=False)
            .filter(ultima_compra__lt=desde)
        )
        candidatos = list(qs[:6])
        total = qs.count()

        if total == 0:
            return format_html(
                '<div style="padding: 12px; background: #f1f5f9; border-radius: 6px;">'
                '<div style="font-size: 24px; font-weight: 700; color: #475569;">0</div>'
                '<div style="color: #64748b; font-size: 13px;">clientes inactivos con el umbral actual ({} días).</div>'
                '</div>',
                dias,
            )

        sample_html = ''.join(
            f'<li style="margin-bottom: 2px;">'
            f'{c.nombre_completo()} '
            f'<span style="color: #94a3b8; font-size: 11px;">'
            f'(última compra {c.ultima_compra or "—"})'
            f'</span>'
            f'</li>'
            for c in candidatos[:5]
        )
        mas_html = ''
        if total > 5:
            mas_html = f'<li style="color: #64748b;"><i>… y {total - 5} más</i></li>'

        return format_html(
            '<div style="padding: 12px; background: #fff7ed; border: 1px solid #fed7aa; '
            'border-radius: 6px;">'
            '<div style="font-size: 24px; font-weight: 700; color: #c2410c;">{}</div>'
            '<div style="color: #9a3412; font-size: 13px;">clientes inactivos hace más de {} días.</div>'
            '<details style="margin-top: 8px;">'
            '<summary style="cursor: pointer; color: #c2410c;">Ver muestra</summary>'
            '<ul style="margin: 6px 0 0 0; padding-left: 20px; font-size: 12px;">{}{}</ul>'
            '</details>'
            '</div>',
            total, dias, format_html(sample_html), format_html(mas_html),
        )
    alerta_inactividad_preview.short_description = '👁 Preview de inactivos'

    def link_panel_whatsapp(self, obj):
        """
        Link al panel de conexión WhatsApp.

        Antes tenía `target="_blank"` para que el operador pudiera
        dejarlo abierto en una pestaña mientras escaneaba el QR. Pero
        feedback de prod: confunde abrir todo en pestañas nuevas.
        Ahora abre en la misma — si el operador quiere otra pestaña,
        usa Cmd+Click explícitamente.
        """
        from django.utils.html import format_html
        return format_html(
            '<a href="/wa-campania/conexion/" '
            'style="display: inline-block; padding: 6px 14px; '
            'background: #16a34a; color: white; border-radius: 4px; '
            'text-decoration: none; font-weight: 500;">'
            '💬 Abrir panel WhatsApp</a>'
        )
    link_panel_whatsapp.short_description = 'Conexión WhatsApp'

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
