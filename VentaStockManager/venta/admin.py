# from django.contrib import admin
from venta.models import Venta, ArticuloVenta, Pedido
from articulo.models import Articulo
from vendedor.models import Vendedor
from django.utils.html import format_html
from django.urls import reverse
from django.contrib import admin
from django.urls import path
from django.http import HttpResponseRedirect
from django.utils import timezone
from venta.views import generar_pdf_pedidos
from django.contrib import messages
from cliente.admin_permissions import SuperuserOnlyAdminMixin, StaffFullAccessAdminMixin
from .forms import ArticuloVentaInlineFormSet
from venta.forms import ArticuloVentaForm
import logging
from django.core import validators
from django.core.exceptions import ValidationError
from venta.utils import parse_precio

# import autocomplete_all

# from django.db.models.query import SelectQuerySet
from django.contrib import admin
from venta.forms import   VentaForm


class ArticuloVentaInline(admin.TabularInline):
    model = ArticuloVenta
    form = ArticuloVentaForm
    formset = ArticuloVentaInlineFormSet
    extra = 12
    min_num = 0
    max_num = None
    validate_min = False
    validate_max = False
    can_delete = True
    verbose_name = "Item de venta"
    verbose_name_plural = "Items de ventas"
    empty_value_display = 'Busca un articulo'
    raw_id_fields = ["articulo"]
    show_add_another = True
    show_change_link = True
    autocomplete_fields = ["articulo"]
    
    fields = ("articulo", "cantidad", "precio", "precio_total")
    readonly_fields = ("precio_total",)

    
    def precio_total(self, obj):
        if obj.cantidad is None or obj.price is None:
            return 0
        return obj.total
    readonly_fields = ("precio_total", )
    fields = ("articulo", "cantidad" , "precio", "precio_total")
    
    precio_total.short_description = "Total"
    def has_delete_permission(self, request, obj=None):
        return True
    
    def clean(self):
        cleaned_data = super().clean()
        if not self.cleaned_data.get('DELETE', False):
            cantidad = cleaned_data.get('cantidad')
            if cantidad is None or cantidad <= 0:
                raise ValidationError("La cantidad debe ser mayor que cero.")
        return cleaned_data
    # def formfield_overrides(self, request, form):
    #     overrides = super().formfield_overrides(request, form)        
    #     if form.model == ArticuloVenta:
    #         overrides["articulo"] = {"widget": forms.Select(attrs={"style": "width: 200px"})}
    #     return overrides

    # def precio_minorista_2(self, obj):
    #     if obj.articulo is None:
    #         return "-- select-articulo-first"q23
    #     return str(obj.articulo.precio_minorista)
    #readonly_fields = ('precio_minorista', 'precio_mayorista')(self, request, queryset)
    class Media:
        js = ('js/articulo_venta_admins.js',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "articulo":
            kwargs["queryset"] = Articulo.objects.filter(stock__gt=0)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

class ArchivadaListFilter(admin.SimpleListFilter):
    """
    Filter custom para mostrar/ocultar ventas archivadas. Default
    "Activas" (no archivadas) — las archivadas quedan fuera del flujo
    normal salvo que el operador las pida explícitamente.
    """
    title = 'Estado de archivo'
    parameter_name = 'archivada'

    def lookups(self, request, model_admin):
        return (
            ('activas', 'Activas'),
            ('archivadas', 'Archivadas'),
            ('todas', 'Todas'),
        )

    def queryset(self, request, queryset):
        # Default: si no hay param, mostrar solo activas.
        val = self.value() or 'activas'
        if val == 'archivadas':
            return queryset.filter(archivada_en__isnull=False)
        if val == 'todas':
            return queryset
        return queryset.filter(archivada_en__isnull=True)

    def choices(self, changelist):
        # Hacemos que "Activas" aparezca seleccionada cuando no hay
        # param explícito.
        value = self.value() or 'activas'
        for lookup, title in self.lookup_choices:
            yield {
                'selected': value == str(lookup),
                'query_string': changelist.get_query_string(
                    {self.parameter_name: lookup}, []
                ),
                'display': title,
            }


class VentaAdmin(StaffFullAccessAdminMixin, admin.ModelAdmin):
    form = VentaForm
    ordering = ('-fecha_compra',)
    list_display = ['fecha_compra', 'fecha_entrega', 'cliente', 'vendedor', 'total_venta_por_articulo']
    # Orden de filtros importa: ArchivadaListFilter primero para que
    # el operador vea el toggle activas/archivadas arriba.
    list_filter = [ArchivadaListFilter, 'fecha_compra', 'fecha_entrega']
    icon_name = "monetization_on"
    inlines = [ArticuloVentaInline]
    # Buscador venta por cliente y vendedor (mismas claves que PedidoAdmin).
    # Antes era ('cliente__nombre') sin coma — una string, no una tupla —
    # con lo cual Django iteraba carácter por carácter.
    search_fields = (
        'cliente__nombre',
        'vendedor__nombre',
        'vendedor__apellido',
        'vendedor__usuario__username',
    )
    data_hierarchy = "fecha_compra"
    raw_id_fields = ["cliente"]
    autocomplete_fields = ['cliente']

    # Redirigimos add/change a la pantalla custom (Alpine + Tailwind).
    # Mantenemos list_view, search, list_filter y todo el resto del
    # admin intactos — la lista del admin sigue siendo la "pantalla de
    # navegación", y la pantalla custom solo reemplaza el formulario
    # de carga/edición que era la parte más rota.
    def add_view(self, request, form_url='', extra_context=None):
        from django.urls import reverse
        return HttpResponseRedirect(reverse('venta_nueva'))

    def change_view(self, request, object_id, form_url='', extra_context=None):
        from django.urls import reverse
        return HttpResponseRedirect(reverse('venta_editar', args=[object_id]))

    def save_model(self, request, obj, form, change):
        # Save the main object first to get an ID
        super().save_model(request, obj, form, change)


    
    def save_related(self, request, form, formsets, change):
        """
        Save related objects and calculate total.
        """
        try:
            total_venta = 0
            
            # Save formsets
            for formset in formsets:
                # Validate and clean formset data before saving
                if formset.is_valid():
                    instances = formset.save(commit=False)
                    
                    # Process each instance
                    for instance in instances:
                        if instance.articulo_id and instance.cantidad and instance.precio:
                            instance.venta = form.instance
                            instance.save()
                            
                            # Calculate running total
                            precio_limpio = float(str(instance.precio).replace("'", "").replace(",", ""))
                            total_venta += instance.cantidad * precio_limpio
                    
                    # Handle deletions
                    for obj in formset.deleted_objects:
                        obj.delete()
            
            # Update total sale price
            form.instance.precio_total = total_venta
            form.instance.save()
            
            messages.success(request, f'Venta actualizada. Total: ${total_venta:,.2f}')
        except Exception as e:
            logging.error(f"Error in save_related: {str(e)}")
            messages.error(request, f"Error al guardar la venta: {str(e)}")
            raise
    
    def cantidad_articulos_vendidos(self, obj):
        return obj.articulos_vendidos.count()

    cantidad_articulos_vendidos.short_description = 'Cantidad de artículos vendidos'
    
    def get_changeform_initial_data(self, request):
        # Obtiene los datos iniciales para el formulario de creación
        initial = super().get_changeform_initial_data(request)
        vendedor, created = Vendedor.objects.get_or_create(usuario=request.user)
        
        initial['vendedor'] = vendedor
        initial['fecha_compra'] = timezone.now()
        return initial  

    def total_venta_por_articulo(self, obj):
        total = 0
        for articulo_venta in obj.ventas.all():
            cantidad = articulo_venta.cantidad or 0
            total += cantidad * parse_precio(articulo_venta.precio)
        return total


    total_venta_por_articulo.short_description = 'Total Venta por Artículo'
  

    def precio_total(self, venta):
        if not venta.id:
            return f'\n{" "*8}$0.00'
        else:
            return venta.precio_total

    precio_total.short_description = 'Total De compra'
    readonly_fields = ('precio_total',)

    fieldsets = (
        ("Detalle de venta", {
            "fields":  (("cliente", "fecha_entrega", "fecha_compra"), ("precio_total", "vendedor" )),
            "classes": ('fw-bold', 'align-right', 'required'),
        }),
    )
    # NOTA: `search_fields` y `data_hierarchy` ya están definidos arriba.
    # No los volvemos a setear acá para no pisarlos.

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'precio_total' in form.base_fields:
            form.base_fields['precio_total'].widget.attrs['id'] = 'id_precio_total'
        return form

    def save_formset(self, request, form, formset, change):
        try:
            instances = formset.save(commit=False)
            for instance in instances:
                if instance.articulo and instance.cantidad and instance.precio:
                    instance.venta = form.instance
                    instance.save()
            
            # Handle deletions
            for obj in formset.deleted_objects:
                obj.delete()
            
        except Exception as e:
            messages.error(request, str(e))
            raise

# admin_site.site.register(Venta, VentaAdmin)

class PedidoAdmin(StaffFullAccessAdminMixin, admin.ModelAdmin):

    readonly_fields = ('venta','mostrar_articulos')
    ordering = ('-venta__fecha_compra',)
    list_display = [
        'id', 'venta_fecha_compra', 'venta_fecha_entrega', 'venta_cliente',
        'venta_vendedor', 'total_venta_por_articulo',
        'cantidad_articulos_vendidos',
        'pagado_badge', 'cobrado_display',
        'descargar_pdf',
    ]
    # `pagado` permite filtrar "lo del día que ya cobré" vs pendientes.
    # `venta__vendedor` es necesario para el flujo "Informe diario por
    # vendedor" — el operador filtra por vendedor + fecha, selecciona
    # los pedidos que le interesan y usa el bulk action para generar
    # el informe consolidado.
    list_filter = [
        'pagado', 'estado',
        'venta__vendedor',
        'venta__fecha_compra', 'venta__fecha_entrega',
    ]
    # Buscador pedido por nombre de cliente y por vendedor
    # (nombre/apellido del vendedor o su usuario de login).
    search_fields = (
        'venta__cliente__nombre',
        'venta__vendedor__nombre',
        'venta__vendedor__apellido',
        'venta__vendedor__usuario__username',
    )
    icon_name = "library_books"
    actions = [
        'generar_pdfs',
        'informe_diario_vendedor_a4',
        'informe_diario_vendedor_configurado',
        'marcar_como_saldada',
        'registrar_pago',
    ]

    # Define constants
    ARTICULO_LABEL = 'Artículo'
    
    def cantidad_articulos_vendidos(self, obj):
        return sum(articulo_venta.cantidad for articulo_venta in obj.venta.ventas.all())
    cantidad_articulos_vendidos.short_description = '# Artículos'

    def pagado_badge(self, obj):
        """Icono ✓/✗ para `pedido.pagado` — más legible que True/False en la lista."""
        if obj.pagado:
            return format_html(
                '<span style="color:#047857; font-weight:600;">✓ Pagado</span>'
            )
        return format_html(
            '<span style="color:#94a3b8;">✗ Pendiente</span>'
        )
    pagado_badge.short_description = '¿Pagado?'
    pagado_badge.admin_order_field = 'pagado'

    def cobrado_display(self, obj):
        """
        Monto efectivamente cobrado por la acción "Registrar pago" /
        "Marcar como saldada" (clientes con cuenta). NULL = todavía no
        se registró (caso típico de pedido pendiente o cobrado por
        otra vía sin registrar el monto).
        """
        if obj.monto_pagado is None:
            return format_html('<span style="color:#94a3b8;">—</span>')
        return format_html(
            '<span style="color:#047857; font-weight:600;">${}</span>',
            f'{obj.monto_pagado:,.2f}',
        )
    cobrado_display.short_description = 'Cobrado'
    cobrado_display.admin_order_field = 'monto_pagado'

    def changelist_view(self, request, extra_context=None):
        """
        Inyecta en el contexto del changelist:
          - `total_cobrado_lista`: suma de `monto_pagado` de los pedidos
            visibles con el filtro actual. Útil para reconciliar caja:
            filtrá "pagado=Sí + fecha_compra=hoy" → ves cuánto debería
            estar en efectivo.
          - `total_pendiente_lista`: cuántos quedan por cobrar.
        """
        from decimal import Decimal
        from django.db.models import Sum, Count, Q
        response = super().changelist_view(request, extra_context=extra_context)
        try:
            qs = response.context_data['cl'].queryset
        except (AttributeError, KeyError):
            return response  # caso raro: changelist sin context (e.g. error temprano)

        agg = qs.aggregate(
            cobrado=Sum('monto_pagado'),
            pendientes=Count('id', filter=Q(pagado=False)),
            saldados=Count('id', filter=Q(pagado=True)),
        )
        cobrado = agg['cobrado'] or Decimal('0')
        response.context_data['resumen_cobranza'] = {
            'cobrado': cobrado,
            'cobrado_str': f'{cobrado:,.2f}',
            'pendientes': agg['pendientes'] or 0,
            'saldados': agg['saldados'] or 0,
        }
        return response


    def total_venta_por_articulo(self, obj):
        total = 0
        for articulo_venta in obj.venta.ventas.all():
            cantidad = articulo_venta.cantidad or 0
            total += cantidad * parse_precio(articulo_venta.precio)
        return total


    total_venta_por_articulo.short_description = 'Total Venta'

    def venta_fecha_compra(self, obj):
        return obj.venta.fecha_compra
    venta_fecha_compra.short_description = 'Fecha de Compra'

    def venta_fecha_entrega(self, obj):
        return obj.venta.fecha_entrega
    venta_fecha_entrega.short_description = 'Fecha de Entrega'

    def venta_cliente(self, obj):
        return obj.venta.cliente
    venta_cliente.short_description = 'Cliente'

    def venta_vendedor(self, obj):
        # Usa Vendedor.display_name() para mostrar
        # "username (nombre apellido)". Misma lógica que el PDF de
        # pedido para evitar inconsistencias entre listado y comanda.
        v = obj.venta.vendedor
        return v.display_name() if v else '-'
    venta_vendedor.short_description = 'Vendedor'
    
    def descargar_pdf(self, obj):
        if obj:
            url = reverse('generar_pdf_pedido', args=[obj.id])
            return format_html('<a href="{}" target="_blank">Descargar PDF</a>', url)
        return ''

    descargar_pdf.short_description = 'Descargar PDF'
    
    def generar_pdfs(self, request, queryset):
        pedido_ids = queryset.values_list('id', flat=True)
        return HttpResponseRedirect(reverse('generar_pdf_pedidos') + f"?pedidos_ids={','.join(map(str, pedido_ids))}")

    generar_pdfs.short_description = "Generar PDFs para pedidos seleccionados"

    @admin.action(description='📄 Informe diario por vendedor (A4)')
    def informe_diario_vendedor_a4(self, request, queryset):
        """
        Genera un PDF A4 consolidado con los pedidos seleccionados,
        agrupados por vendedor.

        Los campos a incluir salen de `ConfiguracionGeneral`
        (fieldset "Informe diario por vendedor" en /admin/configuracion/).
        Ver `venta/views_informe.py`.
        """
        ids = ','.join(map(str, queryset.values_list('id', flat=True)))
        return HttpResponseRedirect(
            reverse('informe_diario_vendedor') + f'?pedidos_ids={ids}&tamano=a4'
        )

    @admin.action(description='🎨 Informe diario por vendedor (según configuración de comprobante)')
    def informe_diario_vendedor_configurado(self, request, queryset):
        """
        Igual que `informe_diario_vendedor_a4` pero usa el tamaño de
        página + fuentes + colores del `FacturaConfiguration` — así
        el informe queda visualmente idéntico a los comprobantes
        individuales que ya se imprimen para los pedidos.
        """
        ids = ','.join(map(str, queryset.values_list('id', flat=True)))
        return HttpResponseRedirect(
            reverse('informe_diario_vendedor') + f'?pedidos_ids={ids}&tamano=config'
        )

    @admin.action(description='✓ Marcar como saldada (venta al contado)')
    def marcar_como_saldada(self, request, queryset):
        """
        Marca los pedidos seleccionados como pagados. Comportamiento
        según si el cliente usa cuenta corriente o no:

          - Cliente CON `CuentaCliente`: registra el pago por el TOTAL
            de la venta (crea PAGO + VENTA_A_CUENTA si falta) — así el
            saldo del cliente refleja correctamente que esa venta se
            cobró. Caso típico: cliente al que a veces le fiamos y
            necesitamos llevar la cuenta.
          - Cliente SIN cuenta corriente: solo `pagado=True`, no toca
            cuenta. Caso típico: venta al contado de cliente walk-in
            que no maneja cuenta — no le inventamos una.

        Filosofía: respetamos el estado del cliente. La cuenta
        corriente se crea explícitamente cuando hace falta (deuda
        registrada, pago parcial, etc), NO desde acción rápida.

        Idempotente: pedidos con `monto_pagado != null` o `pagado=True`
        se skipean.
        """
        from decimal import Decimal
        from cliente.models import CuentaCliente
        from venta.utils import total_venta as calcular_total_venta

        saldados_con_cuenta = 0
        saldados_al_contado = 0
        ya_estaban = 0
        total_cobrado = Decimal('0')
        errores = 0

        for pedido in queryset.select_related('venta__cliente'):
            if pedido.monto_pagado is not None:
                ya_estaban += 1
                continue
            venta = pedido.venta
            cliente = venta.cliente if venta else None
            if not cliente:
                errores += 1
                continue

            tiene_cuenta = CuentaCliente.objects.filter(cliente=cliente).exists()
            if tiene_cuenta:
                # Cliente con cuenta: registrar pago en cuenta corriente.
                total = Decimal(calcular_total_venta(venta) or 0)
                try:
                    pedido.set_monto_pagado(total, user=request.user)
                    total_cobrado += total
                    saldados_con_cuenta += 1
                except ValueError:
                    errores += 1
            else:
                # Sin cuenta: solo marcar pagado. No se inventa cuenta.
                if not pedido.pagado:
                    pedido.pagado = True
                    pedido.save(update_fields=['pagado'])
                saldados_al_contado += 1

        partes = []
        if saldados_con_cuenta:
            partes.append(
                f'{saldados_con_cuenta} con cuenta corriente '
                f'(${total_cobrado:,.2f} registrados como pago)'
            )
        if saldados_al_contado:
            partes.append(
                f'{saldados_al_contado} al contado '
                f'(cliente sin cuenta corriente — solo se marcaron como pagados)'
            )
        if ya_estaban:
            partes.append(f'{ya_estaban} ya estaban saldados')
        if errores:
            partes.append(f'{errores} con error')
        resumen = ' · '.join(partes) if partes else 'Sin cambios.'
        self.message_user(request, f'✓ {resumen}', level=messages.SUCCESS)

    @admin.action(description='💰 Registrar pago (preguntar cuánto cobró por cada uno)')
    def registrar_pago(self, request, queryset):
        """
        Lleva a la pantalla intermedia donde el operador ingresa, por
        pedido, cuánto cobró. La pantalla:

          - Para pedidos sin pago previo: muestra input + preview de
            saldo nuevo.
          - Para pedidos con pago ya registrado: muestra "ya registrado
            $X" y NO permite duplicar el cobro (idempotencia).

        NO genera PDFs — para imprimir las comandas usar "Generar PDFs"
        por separado. Decisión deliberada: separar "cobrar" de
        "imprimir" porque son flujos distintos (cobrar es delicado por
        la cuenta corriente, imprimir es trivial).
        """
        pedido_ids = list(queryset.values_list('id', flat=True))
        if not pedido_ids:
            self.message_user(
                request, 'No seleccionaste ningún pedido.', level=messages.WARNING,
            )
            return None
        ids_csv = ','.join(map(str, pedido_ids))
        return HttpResponseRedirect(
            reverse('registrar_pago_pedidos') + f'?pedidos_ids={ids_csv}',
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('generar_pdfs/', self.admin_site.admin_view(self.generar_pdfs_view), name='generar_pdf_pedidos'),
        ]
        return custom_urls + urls

    def generar_pdfs_view(self, request):
        ids = request.GET.get('ids').split(',')
        return generar_pdf_pedidos(request, ids)

    def descargar_pdf(self, obj):
        if obj:
            url = reverse('generar_pdf_pedido', args=[obj.id])
            return format_html('<a href="{}" target="_blank">Descargar PDF</a>', url)
        return ''

    def mostrar_articulos(self, obj):
        """
        Renderiza un bloque HTML grande con:
          1. Estado de cobro (total / cobrado / pendiente) + botón
             Registrar/Editar pago.
          2. Tabla de artículos de la venta.
          3. Link al PDF.

        Lo metemos todo en este método porque material-admin renderiza
        mal los campos readonly como readonly_fields separados (label
        flotante encima del valor). Acá devolvemos HTML libre que el
        admin no envuelve con su markup roto.
        """
        if not obj or not obj.venta:
            return 'No hay artículos'
        from decimal import Decimal
        from venta.utils import total_venta as calcular_total_venta

        # ---- Estado de cobro ----
        total = Decimal(calcular_total_venta(obj.venta) or 0)
        cobrado = obj.monto_pagado or Decimal('0')
        pendiente = total - cobrado
        if obj.monto_pagado is None:
            cobrado_html = '<span style="color: #94a3b8; font-style: italic;">(sin registrar)</span>'
        else:
            cobrado_html = f'<span style="color: #047857; font-weight: 600;">${cobrado:,.2f}</span>'
        if pendiente <= 0:
            sobrante_txt = (
                f' — sobrante ${abs(pendiente):,.2f}' if pendiente < 0 else ''
            )
            pendiente_html = (
                f'<span style="color: #047857; font-weight: 600;">'
                f'✓ Saldado{sobrante_txt}</span>'
            )
        else:
            pendiente_html = (
                f'<span style="color: #b91c1c; font-weight: 600;">'
                f'${pendiente:,.2f}</span>'
            )

        # Botón de registrar/editar pago al lado del estado.
        url_pago = reverse('registrar_pago_pedidos') + f'?pedidos_ids={obj.pk}'
        if obj.monto_pagado is None:
            boton_label = '💰 Registrar pago'
            boton_color = '#4f46e5'
        else:
            boton_label = '✏️ Editar pago'
            boton_color = '#059669'

        # ---- Tabla de artículos ----
        filas_html = ''
        for av in obj.venta.ventas.all():
            filas_html += (
                f'<tr>'
                f'<td style="padding:4px 8px;">{av.articulo.get_articulo_short_name()}</td>'
                f'<td style="padding:4px 8px; text-align:right;">{av.cantidad}</td>'
                f'<td style="padding:4px 8px; text-align:right;">${av.precio}</td>'
                f'<td style="padding:4px 8px; text-align:right;">${av.total}</td>'
                f'</tr>'
            )

        html = f'''
        <div style="margin-top:8px;">

          <div style="padding:12px; background:#f1f5f9; border-radius:8px; margin-bottom:16px;
                      display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;">
            <table style="border-collapse:collapse; font-size:14px;">
              <tr><td style="padding:2px 16px 2px 0; color:#64748b;">Total venta:</td>
                  <td style="padding:2px 0; font-weight:600;">${total:,.2f}</td></tr>
              <tr><td style="padding:2px 16px 2px 0; color:#64748b;">Cobrado:</td>
                  <td style="padding:2px 0;">{cobrado_html}</td></tr>
              <tr><td style="padding:2px 16px 2px 0; color:#64748b;">Pendiente:</td>
                  <td style="padding:2px 0;">{pendiente_html}</td></tr>
            </table>
            <a href="{url_pago}" style="padding:10px 18px; background:{boton_color};
                                       color:white; border-radius:6px; text-decoration:none;
                                       font-weight:500; white-space:nowrap;">{boton_label}</a>
          </div>

          <table style="width:100%; border-collapse:collapse;">
            <thead>
              <tr style="background:#f8fafc; border-bottom:1px solid #e2e8f0;">
                <th style="padding:6px 8px; text-align:left;">Nombre</th>
                <th style="padding:6px 8px; text-align:right;">Cantidad</th>
                <th style="padding:6px 8px; text-align:right;">Precio</th>
                <th style="padding:6px 8px; text-align:right;">Subtotal</th>
              </tr>
            </thead>
            <tbody>
              {filas_html}
              <tr style="border-top:2px solid #e2e8f0;">
                <td colspan="3" style="padding:6px 8px; text-align:right;"><strong>Total</strong></td>
                <td style="padding:6px 8px; text-align:right;"><strong style="color:#2563eb;">${total:,.2f}</strong></td>
              </tr>
            </tbody>
          </table>

          <div style="margin-top:12px;">{self.descargar_pdf(obj)}</div>
        </div>
        '''
        return format_html(html)
    mostrar_articulos.short_description = 'Detalle del pedido'

    # `mostrar_articulos` renderiza TODO el bloque grande: estado de
    # cobro + botón de registrar/editar pago + tabla de artículos + PDF.
    # Lo hacemos así porque material-admin envuelve los readonly fields
    # individuales con un label flotante que se solapa con el contenido
    # HTML. Al meterlo todo dentro de UN solo field readonly, el
    # markup queda controlado por nosotros y no hay solapamiento.
    readonly_fields = ('venta', 'mostrar_articulos')

    fieldsets = (
        (None, {
            'fields': ('venta', 'estado', 'pagado', 'mostrar_articulos')
        }),
    )

    # El botón "Registrar/Editar pago" y el bloque "Estado de cobro"
    # se renderizan ahora dentro de `mostrar_articulos` (para evitar el
    # solapamiento visual de material-admin con floating labels).

    # def get_readonly_fields(self, request, obj=None):
    #        return self.readonly_fields + ('venta',)
    #     return self.readonly_fields



# admin_site.site.register(Pedido, PedidoAdmin)


# ---------------------------------------------------------------------------
# Alertas de stock
# ---------------------------------------------------------------------------
from venta.models import AlertaStock


class AlertaStockAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    """
    Bandeja de entrada de alertas: cada vez que una venta se cargó
    con stock insuficiente, queda acá. La administración entra,
    investiga (¿llegó mercadería?, ¿hubo un error de carga?), y
    marca como revisada con una nota.
    """
    icon_name = 'notification_important'
    list_display = (
        'created_at',
        'tipo_badge',
        'articulo_nombre',
        'cantidad_pedida',
        'cantidad_faltante',
        'stock_disponible_al_momento',
        'venta_link',
        'creado_por',
        'revisada_badge',
    )
    # Por default mostramos solo las sin revisar. El operador puede
    # cambiar al filter "Sí" para ver las revisadas o "Todas".
    list_filter = ('revisada', 'tipo', 'created_at', 'articulo__categoria')
    search_fields = ('articulo__nombre', 'articulo__codigo_interno', 'notas')
    readonly_fields = (
        'tipo', 'venta', 'articulo', 'cantidad_pedida', 'stock_disponible_al_momento',
        'cantidad_faltante', 'creado_por', 'created_at',
        'revisada_at', 'revisada_por',
    )

    def tipo_badge(self, obj):
        from django.utils.html import format_html
        if obj.tipo == 'reponer':
            return format_html(
                '<span style="background:#F59E0B;color:white;padding:2px 8px;'
                'border-radius:8px;font-size:11px;font-weight:600;">REPONER</span>'
            )
        return format_html(
            '<span style="background:#EF4444;color:white;padding:2px 8px;'
            'border-radius:8px;font-size:11px;font-weight:600;">INSUFIC.</span>'
        )
    tipo_badge.short_description = 'Tipo'
    tipo_badge.admin_order_field = 'tipo'
    fields = (
        ('articulo', 'venta'),
        ('cantidad_pedida', 'stock_disponible_al_momento', 'cantidad_faltante'),
        ('creado_por', 'created_at'),
        'revisada',
        'notas',
        ('revisada_at', 'revisada_por'),
    )
    actions = ['accion_marcar_revisadas', 'accion_marcar_no_revisadas']
    date_hierarchy = 'created_at'
    list_select_related = ('articulo', 'venta', 'creado_por')

    def articulo_nombre(self, obj):
        return obj.articulo.nombre[:50]
    articulo_nombre.short_description = 'Artículo'
    articulo_nombre.admin_order_field = 'articulo__nombre'

    def venta_link(self, obj):
        if not obj.venta_id:
            return format_html('<span style="color: #999;">(venta borrada)</span>')
        return format_html(
            '<a href="/venta/{}/editar/" target="_blank">#{}</a>',
            obj.venta_id, obj.venta_id,
        )
    venta_link.short_description = 'Venta'

    def revisada_badge(self, obj):
        if obj.revisada:
            return format_html(
                '<span style="color: #2e7d32; font-weight: bold;">✓ revisada</span>'
            )
        return format_html(
            '<span style="color: #c62828; font-weight: bold;">⚠ pendiente</span>'
        )
    revisada_badge.short_description = 'Estado'
    revisada_badge.admin_order_field = 'revisada'

    def save_model(self, request, obj, form, change):
        # Si el operador marca/desmarca `revisada` en el form,
        # firmamos quién lo hizo y cuándo.
        if change:
            try:
                anterior = AlertaStock.objects.get(pk=obj.pk)
            except AlertaStock.DoesNotExist:
                anterior = None
            if anterior and anterior.revisada != obj.revisada:
                if obj.revisada:
                    obj.revisada_at = timezone.now()
                    obj.revisada_por = request.user
                else:
                    obj.revisada_at = None
                    obj.revisada_por = None
        super().save_model(request, obj, form, change)

    @admin.action(description='Marcar seleccionadas como revisadas')
    def accion_marcar_revisadas(self, request, queryset):
        ahora = timezone.now()
        n = queryset.filter(revisada=False).update(
            revisada=True,
            revisada_at=ahora,
            revisada_por=request.user,
        )
        self.message_user(request, f'{n} alertas marcadas como revisadas.', level=messages.SUCCESS)

    @admin.action(description='Marcar seleccionadas como NO revisadas (reabrir)')
    def accion_marcar_no_revisadas(self, request, queryset):
        n = queryset.filter(revisada=True).update(
            revisada=False, revisada_at=None, revisada_por=None,
        )
        self.message_user(request, f'{n} alertas reabiertas.', level=messages.WARNING)
