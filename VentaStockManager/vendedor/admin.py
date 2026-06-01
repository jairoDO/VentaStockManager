from django.contrib import admin
# Register your models here.
from vendedor.models import Vendedor
from django.urls import reverse
from django.utils.html import format_html

from cliente.admin_permissions import StaffFullAccessAdminMixin


class VendedorAdmin(StaffFullAccessAdminMixin, admin.ModelAdmin):
    """
    Admin del Vendedor. Mostramos el username del usuario asociado +
    nombre y apellido para que el operador pueda ver/editar los nombres
    que aparecen en el PDF del pedido (que ahora sale como
    "username (nombre apellido)" — ver Vendedor.display_name).

    Antes este admin se registraba SOLO en admin.site (el default de
    Django) y NO en nuestro `admin_site` custom (MaterialAdminSite),
    así que no aparecía en /admin/. Ahora se registra en ambos.
    """
    icon_name = "phone_android"
    model = Vendedor
    search_fields = ('nombre', 'apellido', 'usuario__username')
    # Autocomplete del usuario para no scrollear cientos de users.
    raw_id_fields = ('usuario',)

    def username(self, obj):
        return obj.usuario.username if obj.usuario else '-'
    username.short_description = 'Username (login)'
    username.admin_order_field = 'usuario__username'

    def display_name_col(self, obj):
        return obj.display_name()
    display_name_col.short_description = 'Cómo aparece en PDF'

    def ventas_por_vendedor(self, obj):
        """
        Proporciona un enlace para ver las ventas por vendedor.
        """
        if obj and obj.id is not None:
            url = reverse('ventas_por_vendedor', args=[obj.id])
            return format_html('<a href="{}">Ver Ventas</a>', url)
        return format_html('<a href="">Ver Ventas</a>')

    def ventas_recientes_por_vendedor(self, obj):
        url = reverse('ventas_recientes_por_vendedor', args=[obj.id])
        return format_html('<a href="{}">Ver Ventas Recientes</a>', url)

    def ventas_mensual_por_vendedor(self, obj):
        url = reverse('ventas_mensual_por_vendedor', args=[obj.id])
        return format_html('<a href="{}">Ver Ventas Mensuales</a>', url)

    list_display = [
        'username', 'nombre', 'apellido', 'display_name_col',
        'ventas_por_vendedor', 'ventas_recientes_por_vendedor',
        'ventas_mensual_por_vendedor',
    ]
    list_select_related = ('usuario',)

    ventas_por_vendedor.short_description = 'Ventas por Vendedor'
    ventas_recientes_por_vendedor.short_description = 'Ventas Recientes por Vendedor'
    ventas_mensual_por_vendedor.short_description = 'Ventas Mensuales por Vendedor'

    readonly_fields = ['ventas_por_vendedor', 'display_name_col']

    fields = (
        'usuario',
        ('nombre', 'apellido'),
        'telefono',
        'display_name_col',
        'ventas_por_vendedor',
    )


# Registramos en el admin.site default por compatibilidad histórica.
# La registración en el admin_site custom (MaterialAdminSite) se hace
# en VentaStockManager/admin.py para que aparezca en /admin/.
admin.site.register(Vendedor, VendedorAdmin)