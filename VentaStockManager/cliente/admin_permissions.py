"""
Mixin reusable para restringir admins a solo superusuarios.

Por qué un mixin en lugar de copiar los 4 has_*_permission en cada
clase: 12+ admins necesitan la misma restricción. Tenerlo en un
mixin garantiza que (a) la lógica sea idéntica, y (b) si más
adelante queremos cambiar la regla (ej. usar Groups en vez de
is_superuser) sea UN solo punto a tocar.

Uso:

    from cliente.admin_permissions import SuperuserOnlyAdminMixin

    class MiAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
        ...

Esconde el modelo del menú lateral del admin Y bloquea acceso por URL
directa. Si un staff intenta abrir /admin/X/model/ va a recibir 403.

Modelo de roles del proyecto:
  - Superuser: dueño / admin completo. Ve todo.
  - Staff no-superuser ("vendedor"): carga ventas, edita clientes y
    categorías, consulta artículos. NO ve cuentas corrientes,
    movimientos, listas de precios, campañas, panel WhatsApp,
    configuración general, auditlog, ni django-q.
  - Sin staff: no entra al admin.
"""
from __future__ import annotations


class SuperuserOnlyAdminMixin:
    """
    Mixin para admins que solo deben ver los superusuarios.

    Bloquea CUATRO puntos de acceso:
      - has_module_permission: esconde el modelo del index del admin.
      - has_view_permission: bloquea GET al changelist/change view.
      - has_add_permission: bloquea creación.
      - has_change_permission: bloquea edición.
      - has_delete_permission: bloquea borrado.

    Sin module_permission solo, el admin clásico igual muestra el modelo
    en el menú aunque después el view tire 403 — los demás son defensa
    en profundidad.
    """

    def has_module_permission(self, request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_view_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_add_permission(self, request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_change_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)


class StaffFullAccessAdminMixin:
    """
    Mixin para admins que el VENDEDOR usa con permisos completos
    (ver/crear/editar). Aplica a: Venta, Pedido, Cliente, Categoria —
    el core del flujo diario del operador.

    Sin este mixin, Django requiere `Permission` objects explícitos
    asignados al user (django.contrib.auth.Permission), pero el
    proyecto no usa ese sistema: todos los admins se gobiernan por
    flags booleanos (is_staff / is_superuser). Sin permisos
    asignados, un vendedor simplemente NO ve el módulo.

    Este mixin desactiva el check de Permission objects para staff.
    Más liberal que el default de Django pero consistente con el
    modelo de roles del resto del proyecto.

    Delete sigue restringido a superuser: borrar ventas/pedidos
    rompe historial — el vendedor debería archivarlas en su lugar.
    """

    def has_module_permission(self, request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_staff)

    def has_view_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_staff)

    def has_add_permission(self, request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_staff)

    def has_change_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_staff)

    def has_delete_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)


class StaffReadOnlyAdminMixin:
    """
    Versión genérica de ArticulosReadOnlyForNonSuperuser para cualquier
    modelo donde el vendedor puede ver pero no modificar.

    Uso típico: catálogo de metadatos del admin (Rubros, Categorías,
    ListaPrecios, etc.). El vendedor necesita consultarlos para entender
    qué hay disponible al cargar una venta, pero NO debería poder
    cambiar la estructura del negocio (renombrar un rubro, borrar una
    categoría con histórico, etc.).

    Permisos:
      - view/module: staff (cualquier user con acceso al admin)
      - add/change/delete: solo superuser
    """

    def has_module_permission(self, request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_staff)

    def has_view_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_staff)

    def has_add_permission(self, request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_change_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)


class ArticulosReadOnlyForNonSuperuser:
    """
    Mixin específico para ArticuloAdmin: el vendedor PUEDE consultar
    artículos (ver lista, ver precios) pero NO PUEDE modificarlos
    masivamente desde el admin. Para subir/bajar precios usa la grilla
    visual (también restringida a superuser si querés — ver views).
    """

    def has_module_permission(self, request) -> bool:
        # Todos los staff ven el módulo en el index.
        return bool(request.user.is_authenticated and request.user.is_staff)

    def has_view_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_staff)

    def has_add_permission(self, request) -> bool:
        # Superuser puede crear artículos nuevos desde el admin clásico
        # (los vendedores usan la grilla y pueden crear ahí, eso lo
        # controlamos en la view de la grilla aparte).
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_change_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)
