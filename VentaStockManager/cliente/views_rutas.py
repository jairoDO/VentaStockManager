from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.http import QueryDict
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from cliente.models import Cliente, RutaSemanalCliente
from cliente.rutas import estadisticas_visitas, recalcular_rutas_sugeridas
from vendedor.models import Vendedor


def _es_superusuario(user):
    return user.is_authenticated and user.is_superuser


def _filtrar_clientes(params):
    qs = Cliente.objects.select_related(
        'vendedor_asignado__usuario',
        'ruta_semanal__vendedor__usuario',
    ).order_by('nombre', 'apellido', 'pk')
    vendedor = params.get('vendedor', '')
    dia = params.get('dia', '')
    estado = params.get('estado', '')
    busqueda = params.get('q', '').strip()
    if vendedor.isdigit():
        qs = qs.filter(ruta_semanal__vendedor_id=vendedor)
    if dia.isdigit() and 0 <= int(dia) <= 6:
        qs = qs.filter(ruta_semanal__dia_semana=int(dia))
    if estado == 'sin_dia':
        qs = qs.filter(ruta_semanal__isnull=True)
    elif estado == 'sugeridos':
        qs = qs.filter(ruta_semanal__sugerida=True)
    elif estado == 'confirmados':
        qs = qs.filter(ruta_semanal__sugerida=False)
    if busqueda:
        qs = qs.filter(
            Q(nombre__icontains=busqueda)
            | Q(apellido__icontains=busqueda)
            | Q(direccion__icontains=busqueda)
            | Q(codigo_interno__icontains=busqueda)
        )
    return qs


@user_passes_test(_es_superusuario, login_url='login')
@require_http_methods(['GET', 'POST'])
def gestionar_rutas_semanales(request):
    """Calcula, revisa y mueve clientes entre días y vendedores."""
    if request.method == 'POST':
        accion = request.POST.get('accion')
        volver_a = request.POST.get('volver_a') or ''
        if accion == 'recalcular':
            resultado = recalcular_rutas_sugeridas()
            messages.success(
                request,
                f"Cálculo terminado: {resultado['creadas']} nuevas, "
                f"{resultado['actualizadas']} actualizadas y "
                f"{resultado['manuales_preservadas']} manuales preservadas.",
            )
        elif accion == 'asignar':
            params = QueryDict(volver_a)
            if request.POST.get('todos_filtrados') == '1':
                clientes = _filtrar_clientes(params)
            else:
                ids = [pk for pk in request.POST.getlist('cliente_ids') if pk.isdigit()]
                clientes = Cliente.objects.filter(pk__in=ids)
            vendedor_id = request.POST.get('vendedor_destino', '')
            dia = request.POST.get('dia_destino', '')
            if not vendedor_id.isdigit() or not dia.isdigit() or not (0 <= int(dia) <= 6):
                messages.error(request, 'Elegí el vendedor y el día de destino.')
            elif not clientes.exists():
                messages.warning(request, 'Seleccioná al menos un cliente.')
            else:
                cantidad = 0
                for cliente_id in clientes.values_list('id', flat=True).iterator():
                    RutaSemanalCliente.objects.update_or_create(
                        cliente_id=cliente_id,
                        defaults={
                            'vendedor_id': int(vendedor_id),
                            'dia_semana': int(dia),
                            'sugerida': False,
                            'confianza': 100,
                            'ventas_analizadas': 0,
                        },
                    )
                    cantidad += 1
                messages.success(request, f'{cantidad} cliente(s) asignados a la ruta elegida.')
        return redirect(f'{request.path}?{volver_a}' if volver_a else request.path)

    qs = _filtrar_clientes(request.GET)
    pagina = Paginator(qs, 50).get_page(request.GET.get('page'))
    vendedores = Vendedor.objects.select_related('usuario').order_by('nombre', 'apellido')
    estadisticas = estadisticas_visitas()
    cantidades = {
        (fila['vendedor_id'], fila['dia_semana']): fila['total']
        for fila in RutaSemanalCliente.objects.values(
            'vendedor_id', 'dia_semana',
        ).annotate(total=Count('id'))
    }
    resumen = []
    for vendedor in vendedores:
        dias = []
        for dia, nombre in RutaSemanalCliente.DIAS:
            metrica = estadisticas.get((vendedor.id, dia), {})
            dias.append({
                'numero': dia,
                'nombre': nombre,
                'asignados': cantidades.get((vendedor.id, dia), 0),
                **metrica,
            })
        resumen.append({'vendedor': vendedor, 'dias': dias})

    filtros_sin_pagina = request.GET.copy()
    filtros_sin_pagina.pop('page', None)
    vendedor_id = request.GET.get('vendedor', '')
    vendedor_seleccionado = (
        vendedores.filter(pk=vendedor_id).first()
        if vendedor_id.isdigit() else None
    )
    dia_valor = request.GET.get('dia', '')
    dia_seleccionado = (
        dict(RutaSemanalCliente.DIAS).get(int(dia_valor))
        if dia_valor.isdigit() else None
    )
    return render(request, 'cliente/rutas_semanales.html', {
        'pagina': pagina,
        'vendedores': vendedores,
        'dias': RutaSemanalCliente.DIAS,
        'resumen': resumen,
        'vendedor': request.GET.get('vendedor', ''),
        'dia': request.GET.get('dia', ''),
        'estado': request.GET.get('estado', ''),
        'q': request.GET.get('q', ''),
        'vendedor_seleccionado': vendedor_seleccionado,
        'dia_seleccionado': dia_seleccionado,
        'query_sin_pagina': filtros_sin_pagina.urlencode(),
        'volver_a': request.GET.urlencode(),
    })
