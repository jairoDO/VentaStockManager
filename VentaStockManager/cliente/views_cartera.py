from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from cliente.models import Cliente
from vendedor.models import Vendedor


def _es_superusuario(user):
    return user.is_authenticated and user.is_superuser


@user_passes_test(_es_superusuario, login_url='login')
@require_http_methods(['GET', 'POST'])
def gestionar_cartera_clientes(request):
    """Pantalla masiva para confirmar sugerencias y reasignar carteras."""
    if request.method == 'POST':
        ids = [int(pk) for pk in request.POST.getlist('cliente_ids') if pk.isdigit()]
        accion = request.POST.get('accion')
        seleccionados = Cliente.objects.filter(pk__in=ids)

        if not ids:
            messages.warning(request, 'Seleccioná al menos un cliente.')
        elif accion == 'confirmar_sugerencias':
            confirmados = 0
            with transaction.atomic():
                for cliente in seleccionados.select_for_update():
                    if not cliente.vendedor_sugerido_id:
                        continue
                    cliente.vendedor_asignado_id = cliente.vendedor_sugerido_id
                    cliente.vendedor_sugerido = None
                    cliente.asignacion_vendedor_confirmada = True
                    cliente.save(update_fields=[
                        'vendedor_asignado', 'vendedor_sugerido',
                        'asignacion_vendedor_confirmada',
                    ])
                    confirmados += 1
            messages.success(
                request,
                f'{confirmados} sugerencia(s) confirmadas.',
            )
        elif accion == 'asignar_vendedor':
            vendedor_id = request.POST.get('vendedor_destino')
            vendedor = None
            if vendedor_id and str(vendedor_id).isdigit():
                vendedor = Vendedor.objects.filter(pk=vendedor_id).first()
            if vendedor is None:
                messages.error(request, 'Elegí un vendedor válido.')
            else:
                cantidad = seleccionados.update(
                    vendedor_asignado=vendedor,
                    vendedor_sugerido=None,
                    asignacion_vendedor_confirmada=True,
                )
                messages.success(
                    request,
                    f'{cantidad} cliente(s) asignados a {vendedor.display_name()}.',
                )
        else:
            messages.error(request, 'La acción elegida no es válida.')

        query = request.POST.get('volver_a') or ''
        return redirect(f'{request.path}?{query}' if query else request.path)

    qs = Cliente.objects.select_related(
        'creado_por',
        'vendedor_asignado__usuario',
        'vendedor_sugerido__usuario',
    ).order_by('nombre', 'apellido', 'pk')

    texto = (request.GET.get('q') or '').strip()
    estado = request.GET.get('estado', 'pendientes')
    sugerido = request.GET.get('sugerido', '')
    asignado = request.GET.get('asignado', '')

    if texto:
        qs = qs.filter(
            Q(nombre__icontains=texto)
            | Q(apellido__icontains=texto)
            | Q(direccion__icontains=texto)
        )
    if estado == 'pendientes':
        qs = qs.filter(
            asignacion_vendedor_confirmada=False,
            vendedor_sugerido__isnull=False,
        )
    elif estado == 'asignados':
        qs = qs.filter(vendedor_asignado__isnull=False)
    elif estado == 'sin_asignar':
        qs = qs.filter(vendedor_asignado__isnull=True)
    if sugerido.isdigit():
        qs = qs.filter(vendedor_sugerido_id=sugerido)
    if asignado.isdigit():
        qs = qs.filter(vendedor_asignado_id=asignado)

    paginator = Paginator(qs, 50)
    pagina = paginator.get_page(request.GET.get('page'))
    vendedores = Vendedor.objects.select_related('usuario').order_by('nombre', 'apellido')

    filtros_sin_pagina = request.GET.copy()
    filtros_sin_pagina.pop('page', None)
    return render(request, 'cliente/cartera_clientes.html', {
        'pagina': pagina,
        'vendedores': vendedores,
        'q': texto,
        'estado': estado,
        'sugerido': sugerido,
        'asignado': asignado,
        'query_sin_pagina': filtros_sin_pagina.urlencode(),
        'volver_a': request.GET.urlencode(),
    })
