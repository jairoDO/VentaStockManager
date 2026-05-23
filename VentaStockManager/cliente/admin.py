from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Sum, Subquery, OuterRef, Value, Count
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils.html import format_html

from cliente.models import Cliente, CuentaCliente, MovimientoCuenta, PrecioCliente


class RegistrarPagoForm(forms.ModelForm):
    """
    Form explícito para "Registrar pago" / "Registrar saldo a favor".

    Antes intentábamos modificar labels/help_text en `get_form()` del
    ModelAdmin, pero por alguna interacción con material-admin esos
    overrides no se aplicaban en el render. Definir un ModelForm con
    los campos hardcoded es 100% confiable.

    El "modo" (pago vs saldo a favor) se setea con un método
    `set_modo()` que el admin llama desde `get_form()`. Cambia labels
    y help_text en runtime.
    """

    class Meta:
        model = MovimientoCuenta
        fields = ('cuenta', 'monto', 'descripcion')
        # Widgets explícitos con clases CSS controladas. Sin esto el
        # form usa el widget default de material-admin que es invisible
        # hasta clickear.
        widgets = {
            'monto': forms.NumberInput(attrs={
                'class': 'registrar-pago-input',
                'placeholder': 'Ej. 5000',
                'step': '0.01',
                'min': '0.01',
                'autofocus': 'autofocus',
            }),
            'descripcion': forms.Textarea(attrs={
                'class': 'registrar-pago-input',
                'rows': 2,
                'placeholder': 'Ej. Pago en efectivo del 22/05',
            }),
        }
        labels = {
            'cuenta': 'Cliente',
            'monto': 'Monto pagado',
            'descripcion': 'Nota (opcional)',
        }
        help_texts = {
            'cuenta': 'Cliente al que se le acredita el pago.',
            'monto': 'Cuánto pagó el cliente. Ingresá positivo (ej. 5000).',
            'descripcion': 'Ej. "Pago en efectivo del 22/05" o "Transferencia BBVA". Para referencia futura.',
        }

    def set_modo_saldo(self):
        """
        Cuando viene `?modo=saldo` en GET, cambiamos los textos para
        guiar al operador hacia "saldo a favor" (cliente adelantó plata
        sin tener deuda) en lugar de "pago" (cancelar deuda).
        """
        self.fields['monto'].label = 'Monto adelantado'
        self.fields['monto'].help_text = (
            'Cuánto trae el cliente como saldo a favor. '
            'Ej. 3000 (queda como crédito para próximas compras).'
        )
        self.fields['descripcion'].help_text = (
            'Ej. "Anticipo del 22/05" o "Pago previo lista X".'
        )

    def clean_monto(self):
        """Rechazar monto <= 0 con mensaje claro."""
        monto = self.cleaned_data.get('monto')
        if monto is None or monto <= 0:
            raise ValidationError(
                'El monto del pago tiene que ser mayor a 0.'
            )
        return monto


class ClienteAdmin(admin.ModelAdmin):
    icon_name = "account_circle"
    model = Cliente
    search_fields = ['nombre']
    # `saldo_actual` muestra el saldo coloreado. Verde = a favor del
    # cliente, rojo = el cliente debe. Lo agregamos para que Osvaldo
    # vea de un vistazo quién tiene cuenta corriente abierta.
    # `wa_estado` muestra si el cliente está habilitado para recibir
    # campañas y si tiene número cargado.
    list_display = (
        'nombre_completo',
        'codigo_interno',
        'telefono',
        'whatsapp_number',
        'wa_estado',
        'saldo_actual',
        'link_extracto',
    )
    list_filter = ('puede_recibir_whatsapp',)
    actions = ['accion_habilitar_whatsapp', 'accion_deshabilitar_whatsapp']

    def get_queryset(self, request):
        """
        Annotate del saldo con UN solo query (Subquery + Coalesce) en
        lugar de pegarle 2 queries por cliente al renderizar
        `saldo_actual` (= 200 queries en una página de 100 clientes,
        que era lo que hacía que el primer hit tardara 50s).

        `Coalesce` mete 0 cuando el cliente no tiene movimientos
        todavía (Subquery devuelve NULL en ese caso).
        """
        subq_saldo = (
            MovimientoCuenta.objects
            .filter(cuenta__cliente=OuterRef('pk'))
            .values('cuenta__cliente')
            .annotate(s=Sum('monto'))
            .values('s')
        )
        return (
            super().get_queryset(request)
            .annotate(saldo_anotado=Coalesce(Subquery(subq_saldo), Value(Decimal('0'))))
        )

    def link_extracto(self, obj):
        """Link rápido a la pantalla de extracto del cliente."""
        # La URL real es /clientes/<id>/extracto/ (con `s`) porque
        # cliente.urls cuelga de `clientes/` en el root URLconf.
        return format_html(
            '<a href="/clientes/{}/extracto/" '
            'class="button" style="padding: 2px 8px; background: #2196f3; '
            'color: white; border-radius: 3px; text-decoration: none;">'
            '📊 Extracto</a>',
            obj.id,
        )
    link_extracto.short_description = 'Extracto'

    def get_readonly_fields(self, request, obj=None):
        if not request.user.is_superuser:
            return ['direccion']
        return []

    def saldo_actual(self, obj):
        # Usamos el campo anotado por `get_queryset` en vez de
        # `obj.saldo` (que hace 2 queries por fila). En el change
        # view individual el annotate no aplica → fallback al
        # property `.saldo` que sí va a la DB pero solo una vez.
        saldo = getattr(obj, 'saldo_anotado', None)
        if saldo is None:
            saldo = obj.saldo
        if saldo == 0:
            return format_html('<span style="color: #888;">$0,00</span>')
        if saldo > 0:
            # A favor del cliente — el negocio le debe. Verde.
            return format_html(
                '<span style="color: #2e7d32; font-weight: bold;">+${}</span>',
                f'{saldo:,.2f}',
            )
        # Saldo negativo: cliente debe. Rojo.
        return format_html(
            '<span style="color: #c62828; font-weight: bold;">-${}</span>',
            f'{abs(saldo):,.2f}',
        )
    saldo_actual.short_description = 'Saldo'
    saldo_actual.admin_order_field = 'saldo_anotado'

    def wa_estado(self, obj):
        """Badge visual del estado de WhatsApp del cliente."""
        if not obj.puede_recibir_whatsapp:
            # Tiene número o no, está deshabilitado igual.
            return format_html(
                '<span style="color: #888; font-size: 11px;">⛔ Deshabilitado</span>'
            )
        if not obj.whatsapp_number:
            # Quiere recibir pero no tiene número cargado.
            return format_html(
                '<span style="color: #c62828; font-size: 11px;">⚠ Sin número</span>'
            )
        # Habilitado y con número: va a recibir campañas.
        return format_html(
            '<span style="color: #2e7d32; font-size: 11px; font-weight: bold;">✓ Habilitado</span>'
        )
    wa_estado.short_description = 'WhatsApp'
    wa_estado.admin_order_field = 'puede_recibir_whatsapp'

    @admin.action(description='Habilitar WhatsApp en clientes seleccionados')
    def accion_habilitar_whatsapp(self, request, queryset):
        n = queryset.update(puede_recibir_whatsapp=True)
        self.message_user(
            request,
            f'{n} clientes habilitados para recibir WhatsApp. '
            f'Ahora aparecerán en las audiencias de campañas.',
            level=messages.SUCCESS,
        )

    @admin.action(description='Deshabilitar WhatsApp en clientes seleccionados')
    def accion_deshabilitar_whatsapp(self, request, queryset):
        n = queryset.update(puede_recibir_whatsapp=False)
        self.message_user(
            request,
            f'{n} clientes deshabilitados. No recibirán más campañas hasta '
            f'que los vuelvan a habilitar.',
            level=messages.WARNING,
        )


class MovimientoCuentaInline(admin.TabularInline):
    """
    Listado de movimientos READ-ONLY dentro del admin de CuentaCliente.

    Antes este inline era editable: el operador podía cambiar el tipo,
    el monto, borrar movimientos enteros con un click. Eso es PELIGROSO
    porque los movimientos son la fuente de verdad del saldo —
    cualquier edit silencioso desbalancea la cuenta.

    Ahora el inline es 100% read-only y compacto. Para registrar un
    PAGO el operador clickea el botón "Registrar pago" que aparece
    arriba (definido como readonly_field en CuentaClienteAdmin), que
    lo lleva al form simplificado de /admin/cliente/movimientocuenta/add/.
    """
    model = MovimientoCuenta
    extra = 0
    max_num = 0  # No mostrar el botón "Agregar movimiento de cuenta adicional"
    can_delete = False  # No checkbox "Eliminar?" en cada fila
    fields = ('fecha_compacta', 'tipo_legible', 'monto_display', 'venta_link', 'descripcion')
    readonly_fields = fields  # Absolutamente todo read-only
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Necesario False para que NO aparezcan los dropdowns/inputs.
        # Solo el detail standalone admin (también read-only) permite
        # ver el detalle de un movimiento si hace falta.
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def fecha_compacta(self, obj):
        return obj.created_at.strftime('%d/%m/%y %H:%M')
    fecha_compacta.short_description = 'Fecha'

    def tipo_legible(self, obj):
        from django.utils.html import format_html
        icono = {
            'pago': '💰',
            'venta_a_cuenta': '🛒',
            'aplicacion_saldo': '⬇️',
            'excedente_venta': '⬆️',
            'ajuste': '⚙️',
        }.get(obj.tipo, '•')
        return format_html('{} {}', icono, obj.get_tipo_display())
    tipo_legible.short_description = 'Tipo'

    def monto_display(self, obj):
        from django.utils.html import format_html
        if obj.monto > 0:
            return format_html(
                '<span style="color:#2e7d32; font-weight:bold;">+${}</span>',
                f'{obj.monto:,.2f}',
            )
        if obj.monto < 0:
            return format_html(
                '<span style="color:#c62828; font-weight:bold;">-${}</span>',
                f'{abs(obj.monto):,.2f}',
            )
        return format_html('<span style="color:#888;">$0,00</span>')
    monto_display.short_description = 'Monto'

    def venta_link(self, obj):
        from django.utils.html import format_html
        if not obj.venta_id:
            return format_html('<span style="color:#888;">—</span>')
        return format_html(
            '<a href="/admin/venta/venta/{}/change/" style="color:#2563eb;">'
            'Venta #{}</a>',
            obj.venta_id, obj.venta_id,
        )
    venta_link.short_description = 'Venta'


class CuentaClienteAdmin(admin.ModelAdmin):
    """
    Admin de cuentas corrientes. Lista todas las cuentas con saldo
    actual, y al entrar muestra los movimientos como lista read-only.

    Para registrar un PAGO: click en el botón "💰 Registrar pago" que
    aparece arriba del detalle. Te lleva al form simplificado de
    /admin/cliente/movimientocuenta/add/ con la cuenta preseleccionada.
    """
    icon_name = "account_balance_wallet"
    list_display = ('cliente_nombre', 'saldo_display', 'created_at')
    search_fields = ('cliente__nombre',)
    # `acciones` es un readonly_field method que renderiza el botón
    # "Registrar pago" como link HTML al add form del movimiento.
    # Lo ponemos antes del inline para que sea lo PRIMERO que ve el
    # operador cuando entra a la cuenta del cliente.
    readonly_fields = ('cliente', 'created_at', 'saldo_display', 'acciones')
    inlines = [MovimientoCuentaInline]

    def acciones(self, obj):
        """
        Dos botones lado a lado:
          - "💰 Registrar pago" → cliente paga deuda existente
          - "💵 Registrar saldo a favor" → cliente adelanta plata

        Funcionalmente ambos son TIPO_PAGO con monto positivo en el
        modelo. La diferencia es la UX: el operador piensa distinto
        y el form los guía con labels y help_text apropiados a cada
        caso (ver get_form en MovimientoCuentaAdmin con ?modo=saldo).
        """
        if not obj or not obj.pk:
            return '—'
        return format_html(
            '<a href="/admin/cliente/movimientocuenta/add/?cuenta={}" '
            'style="display:inline-block; padding:8px 16px; margin-right:8px; '
            'background:#059669; color:white; border-radius:6px; '
            'text-decoration:none; font-weight:500;">'
            '💰 Registrar pago'
            '</a>'
            '<a href="/admin/cliente/movimientocuenta/add/?cuenta={}&modo=saldo" '
            'style="display:inline-block; padding:8px 16px; '
            'background:#2563eb; color:white; border-radius:6px; '
            'text-decoration:none; font-weight:500;">'
            '💵 Registrar saldo a favor'
            '</a>',
            obj.pk, obj.pk,
        )
    acciones.short_description = 'Registrar movimiento'

    def get_queryset(self, request):
        """
        Annotate del saldo con UN solo query agregado, igual que en
        ClienteAdmin. Sin esto el list_display llama a `obj.saldo`
        que es una property y dispara aggregate por fila.
        """
        return (
            super().get_queryset(request)
            .select_related('cliente')
            .annotate(
                saldo_anotado=Coalesce(
                    Sum('movimientos__monto'),
                    Value(Decimal('0')),
                )
            )
        )

    def cliente_nombre(self, obj):
        return obj.cliente.nombre_completo()
    cliente_nombre.short_description = 'Cliente'
    cliente_nombre.admin_order_field = 'cliente__nombre'

    def saldo_display(self, obj):
        # Si la fila viene del list_display, tiene el annotate. En
        # change view individual usamos el property como fallback.
        saldo = getattr(obj, 'saldo_anotado', None)
        if saldo is None:
            saldo = obj.saldo
        color = '#888' if saldo == 0 else ('#2e7d32' if saldo > 0 else '#c62828')
        signo = '' if saldo <= 0 else '+'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}${}</span>',
            color, signo, f'{abs(saldo):,.2f}' if saldo < 0 else f'{saldo:,.2f}',
        )
    saldo_display.short_description = 'Saldo'
    saldo_display.admin_order_field = 'saldo_anotado'


class _ClienteListFilter(admin.SimpleListFilter):
    """
    Filtro por cliente para el listado de movimientos. No usamos el
    autocomplete default del admin porque list_filter con FK directo
    a Cliente listaría los 1100+ clientes en un dropdown gigante,
    cosa que el browser arrastra mal. En su lugar:
      - Mostramos los TOP 30 clientes por cantidad de movimientos
      - El operador igual puede buscar por nombre en search_fields
    """
    title = 'Cliente'
    parameter_name = 'cliente'

    def lookups(self, request, model_admin):
        from django.db.models import Count
        # Top 30 clientes por actividad. Suficiente para el caso
        # típico ("ver movimientos de un cliente frecuente").
        qs = (
            Cliente.objects
            .annotate(n_movs=Count('cuenta__movimientos'))
            .filter(n_movs__gt=0)
            .order_by('-n_movs')[:30]
        )
        return [(c.id, f'{c.nombre_completo()} ({c.n_movs})') for c in qs]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(cuenta__cliente_id=self.value())
        return queryset


class MovimientoCuentaAdmin(admin.ModelAdmin):
    """
    Listado plano de movimientos — SOLO LECTURA desde el admin.

    Por qué solo lectura: los movimientos son la fuente de verdad del
    saldo. Si el operador edita o borra uno a mano, el saldo se
    desfasa y nadie se entera. Los movimientos los crea SIEMPRE el
    sistema cuando hay venta/pago/devolución. Para registrar un pago
    manual, andá a la pantalla de cuenta corriente del cliente
    (botón "Registrar pago" — próxima feature).

    Útil para auditoría: "¿qué movimientos hubo en este cliente
    entre marzo y abril?". Pero NO para edición.
    """
    icon_name = "swap_horiz"
    # Compacto: fecha corta (sin hora gigante), cliente, tipo legible,
    # monto con color, link a la venta origen. El operador escanea la
    # tabla rápido sin tener que hacer scroll horizontal.
    list_display = (
        'fecha_compacta',
        'cuenta_cliente',
        'tipo_legible',
        'monto_display',
        'venta_link',
    )
    list_filter = (_ClienteListFilter, 'tipo', 'created_at')
    search_fields = ('cuenta__cliente__nombre', 'cuenta__cliente__apellido', 'descripcion')
    readonly_fields = ('created_at', 'creado_por', 'cuenta', 'tipo', 'monto', 'venta', 'descripcion')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_per_page = 30  # Más densidad: el listado es de revisión rápida

    # Permisos:
    #   - ADD permitido: el operador puede registrar pagos a mano
    #     desde acá (cliente paga en efectivo / transferencia / etc.
    #     sin estar asociado a una venta puntual).
    #   - CHANGE prohibido: una vez creado el movimiento, no se edita
    #     a mano. Si hay un error, se carga otro movimiento opuesto
    #     (TIPO_AJUSTE con signo contrario). Esto preserva el audit
    #     trail — auditlog registra creates pero un edit silencioso
    #     puede desbalancear el saldo y confundir.
    #   - DELETE prohibido: idem. Si querés "anular" un pago, cargá
    #     un AJUSTE negativo.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    # ---------- Add form simplificado ----------
    # Usamos un ModelForm EXPLÍCITO (RegistrarPagoForm) en lugar del
    # form default + get_form. El form default + modificar
    # form.base_fields[...] en get_form() no surtía efecto en producción
    # por alguna interacción con material-admin. Definir un ModelForm
    # con `labels`, `help_texts` y `widgets` en su Meta es 100% confiable.
    form = RegistrarPagoForm
    fields = ('cuenta', 'monto', 'descripcion')  # solo en add
    raw_id_fields = ('cuenta',)

    class Media:
        # CSS específico que fuerza inputs visibles en este form. Sin
        # esto, material-admin renderiza los inputs con borde transparente
        # y floating labels → en el form de "Registrar pago" el operador
        # ve solo los labels ("Cuenta:", "Monto:") sin nada donde escribir.
        # El CSS arregla bordes, padding, focus state y oculta el floating
        # label que confunde.
        css = {
            'all': ('admin/cliente/movimiento_form.css',),
        }

    def get_form(self, request, obj=None, **kwargs):
        """
        Soportamos dos "modos" via query param:

          /add/?cuenta=N             → modo PAGO (default)
          /add/?cuenta=N&modo=saldo  → modo SALDO A FAVOR

        En ambos casos guardamos un MovimientoCuenta de tipo PAGO con
        monto positivo. La diferencia es solo conceptual/UX — el operador
        piensa distinto y el form lo guía con palabras claras.

        Los labels/help_texts BASE (modo pago) vienen del Meta de
        `RegistrarPagoForm`. Cuando es modo=saldo, llamamos al método
        `set_modo_saldo()` del form para overridear esos textos.
        """
        FormClass = super().get_form(request, obj, **kwargs)
        if obj is None and request.GET.get('modo') == 'saldo':
            # Subclase on-the-fly que invoca set_modo_saldo en __init__.
            # Más simple que pasarlo como param y mantiene el form
            # contenido en si mismo.
            class FormSaldoModo(FormClass):
                def __init__(self, *args, **kw):
                    super().__init__(*args, **kw)
                    self.set_modo_saldo()
            return FormSaldoModo
        return FormClass

    def save_model(self, request, obj, form, change):
        """
        Al guardar desde el add form, completamos los campos que no
        pedimos al usuario:
          - tipo = PAGO (siempre, este admin es solo para pagos)
          - creado_por = el user actual (para auditoría)

        La validación de monto > 0 ya la hace `RegistrarPagoForm.clean_monto`,
        así que acá solo confiamos en que es positivo.
        """
        from cliente.models import MovimientoCuenta
        if not change:
            obj.tipo = MovimientoCuenta.TIPO_PAGO
            obj.creado_por = request.user
        super().save_model(request, obj, form, change)

    def fecha_compacta(self, obj):
        """Solo fecha + hora corta (sin segundos ni timezone)."""
        return obj.created_at.strftime('%d/%m/%y %H:%M')
    fecha_compacta.short_description = 'Fecha'
    fecha_compacta.admin_order_field = 'created_at'

    def cuenta_cliente(self, obj):
        return obj.cuenta.cliente.nombre_completo()
    cuenta_cliente.short_description = 'Cliente'
    cuenta_cliente.admin_order_field = 'cuenta__cliente__nombre'

    def tipo_legible(self, obj):
        """`get_tipo_display()` con un ícono según el tipo."""
        label = obj.get_tipo_display()
        # Iconos suaves para distinguir de un vistazo qué tipo de
        # movimiento es. Mejor que solo el texto.
        icono = {
            'PAGO': '💰',
            'VENTA_A_CUENTA': '🛒',
            'APLICACION_SALDO': '⬇️',
            'EXCEDENTE': '⬆️',
            'AJUSTE': '⚙️',
        }.get(obj.tipo, '•')
        return format_html('{} {}', icono, label)
    tipo_legible.short_description = 'Tipo'
    tipo_legible.admin_order_field = 'tipo'

    def monto_display(self, obj):
        """Monto con color: verde si suma al cliente, rojo si resta."""
        if obj.monto > 0:
            return format_html(
                '<span style="color: #2e7d32; font-weight: bold;">+${}</span>',
                f'{obj.monto:,.2f}',
            )
        if obj.monto < 0:
            return format_html(
                '<span style="color: #c62828; font-weight: bold;">-${}</span>',
                f'{abs(obj.monto):,.2f}',
            )
        return format_html('<span style="color: #888;">$0,00</span>')
    monto_display.short_description = 'Monto'
    monto_display.admin_order_field = 'monto'

    def venta_link(self, obj):
        """
        Link directo a la venta origen del movimiento (si tiene una).
        Reemplaza el campo `venta` plano que solo mostraba el ID.
        Crítico para "ver qué venta generó este movimiento" sin tener
        que buscar a mano.
        """
        if not obj.venta_id:
            return format_html('<span style="color:#888;">—</span>')
        return format_html(
            '<a href="/admin/venta/venta/{}/change/" style="color:#2563eb;">'
            'Venta #{}</a>',
            obj.venta_id, obj.venta_id,
        )
    venta_link.short_description = 'Venta'
    venta_link.admin_order_field = 'venta'


class PrecioClienteAdmin(admin.ModelAdmin):
    """
    Lista plana de precios pactados. Útil para:
      - Ver todos los acuerdos vigentes
      - Borrar a mano cuando un cliente "deja de tener" precio especial
      - Cargar precios pactados desde acá si no surgen de una venta
        (caso: el operador acuerda precio por teléfono sin venta aún)
    """
    icon_name = "local_offer"
    list_display = (
        'cliente_nombre',
        'articulo_nombre',
        'precio_unitario',
        'venta_origen',
        'creado_por',
        'updated_at',
    )
    list_filter = ('updated_at',)
    search_fields = (
        'cliente__nombre',
        'cliente__apellido',
        'articulo__nombre',
        'articulo__codigo',
        'articulo__codigo_interno',
    )
    readonly_fields = ('created_at', 'updated_at', 'creado_por', 'venta_origen')
    raw_id_fields = ('cliente', 'articulo')
    autocomplete_fields = ('cliente',)
    ordering = ('-updated_at',)

    def cliente_nombre(self, obj):
        return obj.cliente.nombre_completo()
    cliente_nombre.short_description = 'Cliente'
    cliente_nombre.admin_order_field = 'cliente__nombre'

    def articulo_nombre(self, obj):
        return f'{obj.articulo.codigo_interno or ""} {obj.articulo.nombre}'.strip()
    articulo_nombre.short_description = 'Artículo'

    def save_model(self, request, obj, form, change):
        if not change and obj.creado_por is None:
            obj.creado_por = request.user
        super().save_model(request, obj, form, change)
