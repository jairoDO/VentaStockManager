from decimal import Decimal

from django.contrib import admin, messages
from django.db.models import Sum, Subquery, OuterRef, Value, Count
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils.html import format_html

from cliente.models import Cliente, CuentaCliente, MovimientoCuenta, PrecioCliente


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
            '<a href="/clientes/{}/extracto/" target="_blank" '
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
    """Listado de movimientos dentro del admin de CuentaCliente."""
    model = MovimientoCuenta
    extra = 0
    fields = ('created_at', 'tipo', 'monto', 'venta', 'descripcion', 'creado_por')
    readonly_fields = ('created_at', 'creado_por')
    ordering = ('-created_at',)
    show_change_link = True


class CuentaClienteAdmin(admin.ModelAdmin):
    """
    Admin de cuentas corrientes. Lista todas las cuentas con saldo
    actual, y al entrar muestra los movimientos. La forma normal de
    "registrar un pago" es:
      1. Entrar a /admin/cliente/cuentacliente/<id>/change/
      2. En el inline de movimientos, "Add another" con tipo=Pago y
         monto positivo.
      3. Guardar.
    """
    icon_name = "account_balance_wallet"
    list_display = ('cliente_nombre', 'saldo_display', 'created_at')
    search_fields = ('cliente__nombre',)
    readonly_fields = ('cliente', 'created_at', 'saldo_display')
    inlines = [MovimientoCuentaInline]

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

    # Read-only por defecto. Mantenemos `view` para que se pueda entrar
    # a ver el detalle de un movimiento; bloqueamos add/change/delete.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # ni siquiera el superuser puede modificar a mano

    def has_delete_permission(self, request, obj=None):
        return False

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
