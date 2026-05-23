"""
Pantalla custom Alpine para que el superusuario gestione usuarios
(crear vendedores, crear otros superusuarios, desactivar).

Por qué fuera del admin clásico:
  - El admin de auth.User es genérico y confuso: tiene 20+ campos,
    permisos granulares, grupos. El operador NO debería ver eso.
  - Acá: 3 campos (username, password, nombre completo) + un toggle
    "es superusuario" → User + Vendedor creados automáticamente con
    los flags correctos.
  - Reduce errores: el admin no puede "olvidarse" de marcar is_staff
    o de crear el Vendedor asociado.

Estructura:
  GET  /usuarios/                     → lista de usuarios + form de crear
  POST /usuarios/crear/                → crea User + Vendedor
  POST /usuarios/<id>/desactivar/      → soft-deactivate (is_active=False)
  POST /usuarios/<id>/cambiar-tipo/    → toggle superusuario/vendedor

Auth: requiere `is_superuser`. Vendedores NO ven esta pantalla
(rebote a /admin/ con mensaje).
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods, require_POST

from vendedor.models import Vendedor


def _solo_superuser(u) -> bool:
    """Predicado para user_passes_test."""
    return bool(u.is_authenticated and u.is_superuser)


@user_passes_test(_solo_superuser, login_url='/admin/login/')
def lista_usuarios(request: HttpRequest) -> HttpResponse:
    """
    Pantalla principal: lista todos los usuarios staff + form para
    crear uno nuevo.

    "Staff" porque a los usuarios sin staff no nos importa mostrarlos
    acá (típicamente serían usuarios de algún flujo público que no
    existe en este proyecto, pero por las dudas).
    """
    usuarios_qs = (
        User.objects
        .filter(is_staff=True)
        .order_by('-is_superuser', 'username')  # superusers primero
        # Vendedor.usuario es OneToOneField → reverse usable con select_related.
        # El accessor en el User es `user.vendedor` (singular, no _set).
        .select_related('vendedor')
    )
    usuarios = []
    for u in usuarios_qs:
        # OneToOne reverse: lanza DoesNotExist si no hay Vendedor asociado.
        # Cubrimos ambos casos: el de superuser sin Vendedor y el muy raro
        # de un staff que se creó por fuera de esta pantalla.
        try:
            vendedor_asociado = u.vendedor
        except Vendedor.DoesNotExist:
            vendedor_asociado = None
        usuarios.append({
            'id': u.id,
            'username': u.username,
            'first_name': u.first_name,
            'last_name': u.last_name,
            'email': u.email,
            'is_superuser': u.is_superuser,
            'is_active': u.is_active,
            'last_login': u.last_login,
            'date_joined': u.date_joined,
            'vendedor_nombre': (
                f'{vendedor_asociado.nombre} {vendedor_asociado.apellido}'
                if vendedor_asociado else ''
            ),
            'telefono': vendedor_asociado.telefono if vendedor_asociado else '',
            'es_yo_mismo': u.id == request.user.id,
        })

    return render(request, 'cliente/gestion_usuarios.html', {
        'usuarios': usuarios,
    })


@user_passes_test(_solo_superuser, login_url='/admin/login/')
@require_POST
def crear_usuario(request: HttpRequest) -> HttpResponse:
    """
    Crea un User nuevo con flags correctos según el tipo elegido:

    - tipo=vendedor: User(is_staff=True, is_superuser=False)
      + crea Vendedor asociado (para que aparezca como vendedor por
        default en venta nueva)

    - tipo=superuser: User(is_staff=True, is_superuser=True)
      + NO crea Vendedor (los admins no cargan ventas normalmente,
        pero si hace falta lo crean a mano después)

    Validaciones:
      - username único (Django lo enforce con UniqueConstraint)
      - password mínimo 8 chars
      - nombre obligatorio (para crear Vendedor con datos)
    """
    username = (request.POST.get('username') or '').strip()
    password = (request.POST.get('password') or '').strip()
    nombre = (request.POST.get('nombre') or '').strip()
    apellido = (request.POST.get('apellido') or '').strip()
    email = (request.POST.get('email') or '').strip()
    # Teléfono: lo guardamos en Vendedor.telefono. Sirve para futuras
    # integraciones con el bot de WhatsApp (avisos al vendedor sobre
    # pedidos asignados, recordatorios, etc.) y como contacto del operador.
    telefono = (request.POST.get('telefono') or '').strip()
    tipo = (request.POST.get('tipo') or 'vendedor').lower()

    if tipo not in ('vendedor', 'superuser'):
        tipo = 'vendedor'

    # Validaciones simples.
    errors = []
    if not username:
        errors.append('El usuario es obligatorio.')
    if len(password) < 8:
        errors.append('La contraseña tiene que tener al menos 8 caracteres.')
    if not nombre:
        errors.append('El nombre es obligatorio.')
    if username and User.objects.filter(username=username).exists():
        errors.append(f'Ya existe un usuario con el nombre "{username}".')

    if errors:
        for e in errors:
            messages.error(request, e)
        return HttpResponseRedirect('/usuarios/')

    # Crear todo en una transacción: si falla la creación del Vendedor
    # asociado, no queda un User huérfano.
    with transaction.atomic():
        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=nombre,
            last_name=apellido,
            email=email,
            is_staff=True,
            is_superuser=(tipo == 'superuser'),
        )

        # Crear Vendedor asociado solo para vendedores. Los superusers
        # no lo necesitan (en general administran, no cargan ventas).
        if tipo == 'vendedor':
            Vendedor.objects.create(
                nombre=nombre,
                apellido=apellido or 'Vendedor',
                telefono=telefono or None,
                usuario=user,
            )

    tipo_legible = 'Superusuario' if tipo == 'superuser' else 'Vendedor'
    messages.success(
        request,
        f'✓ {tipo_legible} "{username}" creado. '
        f'Ya puede entrar con el usuario y contraseña que le diste.',
    )
    return HttpResponseRedirect('/usuarios/')


@user_passes_test(_solo_superuser, login_url='/admin/login/')
@require_POST
def cambiar_tipo(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Toggle entre superuser y vendedor (cambia el flag is_superuser).
    NO permite que el admin se quite a sí mismo el superuser (anti
    self-lockout).
    """
    user = get_object_or_404(User, pk=user_id)

    if user.id == request.user.id:
        messages.error(
            request,
            'No podés cambiar tu propio tipo. Pedile a otro superusuario '
            'que lo haga si necesitás cambiar tu rol.',
        )
        return HttpResponseRedirect('/usuarios/')

    user.is_superuser = not user.is_superuser
    user.save(update_fields=['is_superuser'])

    nuevo_tipo = 'Superusuario' if user.is_superuser else 'Vendedor'
    messages.success(
        request,
        f'✓ "{user.username}" ahora es {nuevo_tipo}.',
    )
    return HttpResponseRedirect('/usuarios/')


@user_passes_test(_solo_superuser, login_url='/admin/login/')
@require_POST
def desactivar_usuario(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Soft-deactivate: pone is_active=False. El user no puede loguearse
    pero su historial (ventas que cargó, etc.) se mantiene intacto.

    Anti self-lockout: no podés desactivarte a vos mismo.
    """
    user = get_object_or_404(User, pk=user_id)

    if user.id == request.user.id:
        messages.error(request, 'No podés desactivarte a vos mismo.')
        return HttpResponseRedirect('/usuarios/')

    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])

    estado = 'activado' if user.is_active else 'desactivado'
    messages.success(request, f'✓ "{user.username}" {estado}.')
    return HttpResponseRedirect('/usuarios/')


@user_passes_test(_solo_superuser, login_url='/admin/login/')
@require_POST
def resetear_password(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Setear una contraseña nueva para un user (útil cuando se la olvida).
    El admin la pone manualmente y se la pasa al vendedor.
    """
    user = get_object_or_404(User, pk=user_id)
    nueva_password = (request.POST.get('nueva_password') or '').strip()

    if len(nueva_password) < 8:
        messages.error(request, 'La contraseña tiene que tener al menos 8 caracteres.')
        return HttpResponseRedirect('/usuarios/')

    user.set_password(nueva_password)
    user.save(update_fields=['password'])
    messages.success(
        request,
        f'✓ Contraseña de "{user.username}" actualizada. '
        'Pasale la nueva al usuario por canal seguro.',
    )
    return HttpResponseRedirect('/usuarios/')
