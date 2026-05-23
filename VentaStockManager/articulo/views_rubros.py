"""
Pantalla custom Alpine para gestionar Rubros y asignar categorías
a cada rubro de un click.

Por qué fuera del admin clásico:
  - El admin de Rubro con inline de Categorías "funciona" pero es
    incómodo: hay que abrir cada rubro, agregar/editar categorías
    una a una, y no podés ver de un saque cuáles están sin rubro.
  - Acá: una sola pantalla muestra todos los rubros + un panel de
    "categorías sin asignar" y permite asignar varias categorías
    a un rubro con checkboxes — flujo natural para el setup
    inicial donde tenés 34 categorías para distribuir en 10 rubros.

Estructura:
  GET  /rubros/                         → lista + form de crear
  POST /rubros/crear/                   → JSON, crea Rubro + asigna categorías
  POST /rubros/<id>/editar/             → JSON, edita nombre/color/desc
  POST /rubros/<id>/eliminar/           → JSON, borra (categorías quedan sin rubro)
  POST /rubros/<id>/asignar-categorias/ → JSON, sincroniza qué categorías pertenecen

Auth: solo superuser. Vendedores no editan estructura del catálogo.
"""
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from .models import Rubro, Categoria


def _solo_superuser(u) -> bool:
    return bool(u.is_authenticated and u.is_superuser)


@user_passes_test(_solo_superuser, login_url='/admin/login/')
def gestion_rubros(request: HttpRequest) -> HttpResponse:
    """Pantalla principal: lista de rubros + categorías + form de creación."""
    from django.db.models import Count

    rubros_qs = (
        Rubro.objects.annotate(
            _n_categorias=Count('categorias', distinct=True),
            _n_articulos=Count('categorias__articulos', distinct=True),
        )
        .order_by('orden', 'nombre')
    )
    rubros = [
        {
            'id': r.id,
            'nombre': r.nombre,
            'descripcion': r.descripcion,
            'color': r.color,
            'orden': r.orden,
            'n_categorias': r._n_categorias,
            'n_articulos': r._n_articulos,
        }
        for r in rubros_qs
    ]

    # TODAS las categorías con su rubro_id actual. El front las renderiza
    # como checkboxes en el modal de "Asignar categorías".
    categorias_qs = (
        Categoria.objects.select_related('rubro')
        .annotate(_n_articulos=Count('articulos', distinct=True))
        .order_by('nombre')
    )
    categorias = [
        {
            'id': c.id,
            'nombre': c.nombre,
            'color': c.color,
            'rubro_id': c.rubro_id,
            'rubro_nombre': c.rubro.nombre if c.rubro else None,
            'n_articulos': c._n_articulos,
        }
        for c in categorias_qs
    ]

    sin_rubro = [c for c in categorias if c['rubro_id'] is None]

    return render(request, 'articulo/gestion_rubros.html', {
        'rubros': rubros,
        'categorias': categorias,
        'count_sin_rubro': len(sin_rubro),
    })


@user_passes_test(_solo_superuser, login_url='/admin/login/')
@require_POST
def api_crear_rubro(request: HttpRequest) -> JsonResponse:
    """
    Crea un Rubro y opcionalmente asigna categorías existentes en
    el mismo POST (eficiente: el operador no tiene que crear primero
    y asignar después).

    Body JSON:
      {
        "nombre": "Golosinas",
        "color": "#EC4899",
        "descripcion": "...",
        "categoria_ids": [3, 5, 7]   ← opcional
      }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    nombre = (data.get('nombre') or '').strip()
    color = (data.get('color') or '#9CA3AF').strip()
    descripcion = (data.get('descripcion') or '').strip()
    categoria_ids = data.get('categoria_ids') or []

    if not nombre:
        return JsonResponse({'ok': False, 'error': 'El nombre es obligatorio.'}, status=400)
    if Rubro.objects.filter(nombre__iexact=nombre).exists():
        return JsonResponse({
            'ok': False, 'error': f'Ya existe un rubro llamado "{nombre}".',
        }, status=400)

    with transaction.atomic():
        rubro = Rubro.objects.create(
            nombre=nombre, color=color, descripcion=descripcion,
        )
        if categoria_ids:
            # IMPORTANTE: bulk update — un solo UPDATE en lugar de N saves.
            # Si una categoría ya tenía OTRO rubro, queda re-asignada al nuevo.
            Categoria.objects.filter(id__in=categoria_ids).update(rubro=rubro)

    return JsonResponse({
        'ok': True,
        'rubro': {'id': rubro.id, 'nombre': rubro.nombre, 'color': rubro.color},
        'message': f'✓ Rubro "{nombre}" creado.',
    })


@user_passes_test(_solo_superuser, login_url='/admin/login/')
@require_POST
def api_editar_rubro(request: HttpRequest, rubro_id: int) -> JsonResponse:
    """Edita nombre/color/descripcion/orden de un rubro existente."""
    rubro = get_object_or_404(Rubro, pk=rubro_id)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return JsonResponse({'ok': False, 'error': 'El nombre es obligatorio.'}, status=400)
    if Rubro.objects.filter(nombre__iexact=nombre).exclude(pk=rubro.pk).exists():
        return JsonResponse({
            'ok': False, 'error': f'Ya existe otro rubro con el nombre "{nombre}".',
        }, status=400)

    rubro.nombre = nombre
    rubro.color = (data.get('color') or rubro.color).strip()
    rubro.descripcion = (data.get('descripcion') or '').strip()
    if 'orden' in data:
        try:
            rubro.orden = int(data['orden'])
        except (TypeError, ValueError):
            pass
    rubro.save()
    return JsonResponse({'ok': True, 'message': f'✓ Rubro "{nombre}" actualizado.'})


@user_passes_test(_solo_superuser, login_url='/admin/login/')
@require_POST
def api_eliminar_rubro(request: HttpRequest, rubro_id: int) -> JsonResponse:
    """
    Borra un rubro. Las categorías que lo tenían quedan con rubro=NULL
    (por el on_delete=SET_NULL del modelo). NO borra artículos.
    """
    rubro = get_object_or_404(Rubro, pk=rubro_id)
    nombre = rubro.nombre
    rubro.delete()
    return JsonResponse({
        'ok': True, 'message': f'✓ Rubro "{nombre}" eliminado. Las categorías quedaron sin rubro.',
    })


@user_passes_test(_solo_superuser, login_url='/admin/login/')
@require_POST
def api_asignar_categorias(request: HttpRequest, rubro_id: int) -> JsonResponse:
    """
    Sincroniza qué categorías pertenecen al rubro.

    Body JSON: {"categoria_ids": [3, 5, 7, 9]}

    Lógica:
      - Las categorías de la lista quedan con rubro=este (re-asignando
        si pertenecían a otro).
      - Las categorías que ANTES estaban en este rubro pero NO están en
        la lista quedan con rubro=NULL (las "sacamos" del rubro).
    """
    rubro = get_object_or_404(Rubro, pk=rubro_id)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    nuevos_ids = set(int(x) for x in data.get('categoria_ids', []))

    with transaction.atomic():
        # Asignar las nuevas (incluye categorías que ya estaban + re-asigna las que venían de otro rubro).
        if nuevos_ids:
            Categoria.objects.filter(id__in=nuevos_ids).update(rubro=rubro)
        # Desasignar las que ANTES estaban en este rubro pero ya no.
        Categoria.objects.filter(rubro=rubro).exclude(id__in=nuevos_ids).update(rubro=None)

    return JsonResponse({
        'ok': True,
        'message': f'✓ Categorías del rubro "{rubro.nombre}" actualizadas.',
        'n_categorias': len(nuevos_ids),
    })
