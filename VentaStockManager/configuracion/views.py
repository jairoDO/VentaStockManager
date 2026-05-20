"""
Vistas de la app configuración.

`panel_tareas`: UI custom para ejecutar tareas asíncronas a mano,
sin esperar el schedule. Renderiza el catálogo definido en
`tareas_manuales.py` con su estado de última ejecución.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django_q.tasks import async_task

from configuracion.tareas_manuales import CATALOGO_TAREAS, buscar_tarea


def _ultimo_resultado(func_path: str) -> dict | None:
    """
    Busca el último Task de django-q2 para esta función. Devuelve un
    dict con `started`, `success`, `result` o None si nunca corrió.

    Consultamos `Success` y `Failure` (las dos tablas concretas de
    tasks completadas). django-q2 las particiona para hacer queries
    más rápidas; acá tomamos la más reciente de ambas.
    """
    # Lazy import: si django_q no está disponible (caso raro en
    # tests) no queremos que la vista entera explote.
    try:
        from django_q.models import Success, Failure
    except ImportError:
        return None

    success = Success.objects.filter(func=func_path).order_by('-stopped').first()
    failure = Failure.objects.filter(func=func_path).order_by('-stopped').first()

    candidatos = [t for t in (success, failure) if t is not None]
    if not candidatos:
        return None
    ultimo = max(candidatos, key=lambda t: t.stopped)
    return {
        'stopped': ultimo.stopped,
        # `success` == True si es de la tabla Success.
        # django-q2 marca eso en el modelo.
        'success': getattr(ultimo, 'success', None),
        'result_resumen': str(ultimo.result or '')[:200] if ultimo.result else '',
    }


@staff_member_required
@require_http_methods(['GET', 'POST'])
def panel_tareas(request):
    """
    GET: lista las tareas del catálogo con su último resultado.
    POST: encola la tarea indicada por `tarea_id` y redirige al GET
    con un mensaje de éxito/error.
    """
    if request.method == 'POST':
        tarea_id = request.POST.get('tarea_id', '').strip()
        tarea = buscar_tarea(tarea_id)
        if tarea is None:
            messages.error(request, f'Tarea desconocida: {tarea_id!r}')
        else:
            try:
                task_uuid = async_task(tarea['func_path'])
                messages.success(
                    request,
                    f'"{tarea["titulo"]}" encolada (id={task_uuid}). '
                    f'El worker la va a procesar en segundos.',
                )
            except Exception as exc:
                # Por ejemplo: qcluster apagado y la cola ORM-broker
                # no responde. Mostramos el error en vez de un 500.
                messages.error(
                    request,
                    f'No se pudo encolar la tarea: {exc}',
                )
        return HttpResponseRedirect(reverse('configuracion_panel_tareas'))

    # GET: armar las filas con metadata + último resultado.
    filas = []
    for tarea in CATALOGO_TAREAS:
        filas.append({
            **tarea,
            'ultimo': _ultimo_resultado(tarea['func_path']),
        })

    return render(request, 'configuracion/panel_tareas.html', {
        'filas': filas,
    })
