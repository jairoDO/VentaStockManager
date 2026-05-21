"""
Endpoints API para el widget de preview de ReglaCategoria.

Dos endpoints, ambos staff-only:

  - `api_reglas_preview(GET ?keywords=a,b)`
      Devuelve qué artículos matchearían si se aplicara una regla
      con esas palabras clave. NO modifica nada — solo cuenta y
      muestra. Lo usa el widget `ListaPalabrasWidget` para el panel
      "👁 N artículos matchean" debajo de los inputs.

  - `api_reglas_aplicar_ahora(POST {keywords, categoria_id})`
      Asigna AHORA (sin esperar el cron de aplicar_reglas_categoria)
      la categoría a todos los artículos sin categoría que matchean
      las palabras clave. Idempotente: NO toca artículos que ya
      tienen otra categoría (mismo criterio que el management
      command, para no pisar trabajo manual).

Decisión de diseño: el preview lee `keywords` directo del query
string en vez de leer ReglaCategoria.objects.get(id=N).palabras_clave,
porque el operador puede estar editando un objeto SIN guardar
todavía (ej. inline de Categoría) y queremos preview en vivo.
"""
from __future__ import annotations

import json

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from .models import Articulo, Categoria


def _parse_keywords(raw: str) -> list[str]:
    """
    Acepta keywords separadas por coma o newline (lo que mande el
    widget). Limpia espacios y descarta vacías. Capeamos a 50
    keywords para no armar un OR gigante en el SQL.
    """
    if not raw:
        return []
    # Tolerancia: comas o saltos de línea.
    pieces = []
    for chunk in raw.replace('\n', ',').split(','):
        kw = chunk.strip()
        if kw:
            pieces.append(kw)
    # Capeamos para no explotar el SQL si el operador pegó un csv enorme.
    return pieces[:50]


def _build_filter(keywords: list[str]) -> Q:
    """
    Arma el filtro OR: nombre contains kw1 OR nombre contains kw2 ...
    Case-insensitive (icontains) — match con la misma semántica que el
    management command `aplicar_reglas_categoria`.
    """
    q = Q()
    for kw in keywords:
        q |= Q(nombre__icontains=kw)
    return q


@staff_member_required
@require_GET
def api_reglas_preview(request: HttpRequest) -> JsonResponse:
    """
    GET /articulos/api/reglas/preview/?keywords=alfajor,chupetin

    Devuelve:
      {
        "total": int,                 # cuántos matchean (sin filtros)
        "sin_categoria": int,         # cuántos no tienen ninguna asignada
        "con_otra_categoria": int,    # cuántos ya tienen una (que NO sería tocada)
        "samples": [str, ...],        # hasta 20 nombres de muestra
        "keywords": [str, ...],       # echo de las keywords parseadas
      }

    El front muestra el `total` como número grande, y bajo demanda
    el `samples` (para que el operador valide que matchea lo que esperaba).
    """
    keywords = _parse_keywords(request.GET.get('keywords', ''))
    if not keywords:
        return JsonResponse({
            'total': 0,
            'sin_categoria': 0,
            'con_otra_categoria': 0,
            'samples': [],
            'keywords': [],
        })

    qs = Articulo.objects.filter(_build_filter(keywords))
    total = qs.count()
    sin_categoria = qs.filter(categoria__isnull=True).count()
    con_otra = total - sin_categoria

    # Sample: priorizamos los SIN categoría (son los que la regla
    # realmente impactaría al aplicarse). Si sobran cupos en los 20,
    # mostramos también los con-otra.
    sample_sin = list(
        qs.filter(categoria__isnull=True)
        .values_list('nombre', flat=True)[:20]
    )
    cupo_restante = 20 - len(sample_sin)
    sample_con = []
    if cupo_restante > 0:
        sample_con = list(
            qs.filter(categoria__isnull=False)
            .values_list('nombre', flat=True)[:cupo_restante]
        )
    samples = sample_sin + sample_con

    return JsonResponse({
        'total': total,
        'sin_categoria': sin_categoria,
        'con_otra_categoria': con_otra,
        'samples': samples,
        'keywords': keywords,
    })


@staff_member_required
@require_POST
def api_reglas_aplicar_ahora(request: HttpRequest) -> JsonResponse:
    """
    POST /articulos/api/reglas/aplicar-ahora/
    body: {"keywords": "a,b", "categoria_id": 5}

    Asigna `categoria_id` a TODOS los artículos sin categoría que
    matchean las keywords. NO toca artículos que ya tienen una
    categoría (mismo criterio defensivo que el management command —
    si alguien clasificó algo a mano, esa decisión gana).

    Devuelve:
      {"ok": true, "asignados": N}

    Si falta categoria_id o keywords, 400. Si la categoría no existe, 404.
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'mensaje': 'JSON inválido.'}, status=400)

    keywords_raw = payload.get('keywords') or ''
    keywords = _parse_keywords(keywords_raw if isinstance(keywords_raw, str) else ','.join(keywords_raw))
    if not keywords:
        return JsonResponse(
            {'ok': False, 'mensaje': 'Faltan palabras clave.'},
            status=400,
        )

    categoria_id = payload.get('categoria_id')
    if not categoria_id:
        return JsonResponse(
            {'ok': False, 'mensaje': 'Falta categoria_id (guardá la categoría primero).'},
            status=400,
        )

    categoria = get_object_or_404(Categoria, pk=categoria_id)

    # Aplicamos solo a los sin-categoría que matchean. Una sola UPDATE
    # masiva: rápido, atómico, no triggera signals N veces.
    qs = (
        Articulo.objects
        .filter(_build_filter(keywords))
        .filter(categoria__isnull=True)
    )
    asignados = qs.update(categoria=categoria)

    return JsonResponse({
        'ok': True,
        'asignados': asignados,
        'categoria_nombre': categoria.nombre,
    })
