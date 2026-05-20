"""
Vista de "Grilla de precios" — pantalla tipo planilla para editar
precios de muchos artículos a la vez.

La idea de tener este módulo separado de `views.py` es que la grilla
maneja su propio contrato JSON con el front (Alpine) y prefiero
no contaminar `views.py` (que está lleno de vistas server-render
viejas con un estilo distinto). Si en el futuro armamos más
pantallas tipo SPA dentro del admin, conviene moverlas todas a
`views_<feature>.py`.

El flujo es:

  1. GET /articulos/grilla-precios/    -> render del template con
                                          las opciones de filtros.
  2. GET /articulos/api/grilla/        -> JSON con un page de items.
  3. POST /articulos/api/grilla/guardar/ -> aplica todos los cambios
                                            en una transacción.

Para los updates massivos usamos `save(update_fields=[...])` por
fila en vez de `Articulo.objects.filter().update()`: aceptamos la
penalidad de performance (cada fila dispara save() + signals) a
cambio de que django-auditlog registre los cambios de precio,
que es lo más importante de auditar en este modelo. Pasarle
`update_fields` minimiza el UPDATE a las columnas que cambiaron y
deja que el handler del save() decida si tiene que regenerar
codigo_interno (si no le pasamos el campo en update_fields no lo
toca).
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .models import Articulo, Categoria

try:
    # `compra.Proveedor` es la fuente del listado del filtro. Lo
    # importamos lazy / con try para no romper si la app se cargara
    # antes (Django suele resolver esto bien igual, pero curarse en
    # salud no cuesta nada).
    from compra.models import Proveedor
except Exception:  # pragma: no cover - solo se daría con un import roto
    Proveedor = None  # type: ignore[assignment]


# Campos editables desde la grilla. Si en algún momento queremos
# permitir editar más cosas (p.ej. la marca), sumar acá Y agregar
# al validador correspondiente más abajo.
CAMPOS_EDITABLES = ('precio_minorista', 'precio_mayorista', 'stock', 'cantidad_por_mayor')


def _page_size() -> int:
    """Cuántos items devolvemos por página. Settable vía settings."""
    return int(getattr(settings, 'GRILLA_PRECIOS_PAGE_SIZE', 50))


@staff_member_required
def grilla_precios(request: HttpRequest) -> HttpResponse:
    """
    Render del template. Solo pasamos el listado de categorías y
    proveedores para los <select> de filtros: los items en sí se
    cargan vía la API JSON desde Alpine, así no duplicamos la
    lógica de filtrado entre Python y JS.
    """
    categorias = list(Categoria.objects.order_by('nombre').values('id', 'nombre', 'color'))
    proveedores: list[dict[str, Any]] = []
    if Proveedor is not None:
        proveedores = list(Proveedor.objects.order_by('nombre').values('id', 'nombre'))

    return render(
        request,
        'articulo/grilla_precios.html',
        {
            'categorias': categorias,
            'proveedores': proveedores,
        },
    )


def _parse_filtros(request: HttpRequest) -> dict[str, Any]:
    """
    Parsea los query params del GET y devuelve un dict con los
    filtros normalizados. Errores de tipo (texto en `categoria`)
    se traducen como "sin filtro" en vez de explotar — el operador
    no debería ver un 500 por escribir mal una URL.
    """
    raw_categoria = request.GET.get('categoria', '').strip()
    raw_proveedor = request.GET.get('proveedor', '').strip()
    q = request.GET.get('q', '').strip()
    raw_page = request.GET.get('page', '1').strip()

    def _to_int(value: str) -> int | None:
        if value == '':
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    categoria_id = _to_int(raw_categoria)
    proveedor_id = _to_int(raw_proveedor)

    try:
        page = max(1, int(raw_page))
    except (TypeError, ValueError):
        page = 1

    return {
        'categoria_id': categoria_id,
        'proveedor_id': proveedor_id,
        'q': q,
        'page': page,
    }


@staff_member_required
@require_GET
def api_grilla_listar(request: HttpRequest) -> JsonResponse:
    """
    GET /articulos/api/grilla/?categoria=X&proveedor=Y&q=texto&page=N

    Devuelve los artículos filtrados, paginados. La idea es que la
    grilla siempre traiga una porción manejable (50 por defecto):
    cargar miles de filas en el DOM se vuelve incómodo y, peor,
    cualquier cambio sin guardar se complica de visualizar.

    `categoria` y `proveedor` aceptan:
      - vacío  -> no filtrar
      - 0      -> filtrar explícitamente por "sin categoría/proveedor"
                  (FK IS NULL)
      - N > 0  -> filtrar por ese id
    """
    filtros = _parse_filtros(request)

    qs = (
        Articulo.objects.all()
        .select_related('categoria', 'proveedor')
        # Orden: categoría primero, después nombre. Eso le da al
        # operador un agrupamiento visual: todos los "Limpieza"
        # juntos, después "Golosinas", etc.
        # `nulls_last` no es 100% portable así que dejamos que el
        # default del backend resuelva los NULLs; en Postgres
        # quedan al final con asc, que es lo que queremos.
        .order_by('categoria__nombre', 'nombre')
    )

    if filtros['categoria_id'] is not None:
        if filtros['categoria_id'] == 0:
            qs = qs.filter(categoria__isnull=True)
        else:
            qs = qs.filter(categoria_id=filtros['categoria_id'])

    if filtros['proveedor_id'] is not None:
        if filtros['proveedor_id'] == 0:
            qs = qs.filter(proveedor__isnull=True)
        else:
            qs = qs.filter(proveedor_id=filtros['proveedor_id'])

    if filtros['q']:
        qs = qs.filter(
            Q(nombre__icontains=filtros['q'])
            | Q(codigo__icontains=filtros['q'])
            | Q(codigo_interno__icontains=filtros['q'])
        )

    paginator = Paginator(qs, _page_size())
    page_obj = paginator.get_page(filtros['page'])

    items = [
        {
            'id': a.id,
            'codigo': a.codigo or '',
            'codigo_interno': a.codigo_interno or '',
            'nombre': a.nombre,
            'marca': a.marca or '',
            'categoria_id': a.categoria_id,
            'categoria_nombre': a.categoria.nombre if a.categoria_id else None,
            'categoria_color': a.categoria.color if a.categoria_id else None,
            'proveedor_id': a.proveedor_id,
            'proveedor_nombre': a.proveedor.nombre if a.proveedor_id else None,
            'stock': a.stock,
            # Los precios se serializan como string para que el front no
            # pierda decimales por el round-trip a float de JS. La grilla
            # vuelve a mandarlos como string al guardar.
            'precio_minorista': str(a.precio_minorista) if a.precio_minorista is not None else '',
            'precio_mayorista': str(a.precio_mayorista) if a.precio_mayorista is not None else '',
            'cantidad_por_mayor': a.cantidad_por_mayor,
        }
        for a in page_obj.object_list
    ]

    return JsonResponse({
        'page': page_obj.number,
        'total_pages': paginator.num_pages,
        'total_items': paginator.count,
        'items': items,
    })


def _validar_y_normalizar(cambio: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """
    Toma un dict crudo del POST y lo convierte en un dict tipado
    listo para asignar al modelo. Devuelve `(normalizado, errores)`:
    si `errores` no está vacío, `normalizado` es None y la fila
    se rechaza.

    Reglas:
      - precios: Decimal >= 0 (permitimos 0 explícito para
        liquidaciones / artículos sin precio definido).
      - stock: int >= 0.
      - cantidad_por_mayor: int >= 0.
      - Sólo se aceptan los campos de CAMPOS_EDITABLES; cualquier
        otro key se ignora silenciosamente (defensa contra que el
        front mande `id_admin` o algo raro).
    """
    errores: list[dict[str, str]] = []
    normalizado: dict[str, Any] = {}

    item_id = cambio.get('id')
    if item_id is None:
        errores.append({'id': '', 'campo': 'id', 'mensaje': 'Falta el id del artículo.'})
        return None, errores

    for campo in ('precio_minorista', 'precio_mayorista'):
        if campo in cambio:
            raw = cambio[campo]
            try:
                # Permitimos string o número. El front manda string
                # para preservar decimales.
                val = Decimal(str(raw))
            except (InvalidOperation, TypeError, ValueError):
                errores.append({'id': str(item_id), 'campo': campo, 'mensaje': 'Precio no es un número válido.'})
                continue
            if val < 0:
                errores.append({'id': str(item_id), 'campo': campo, 'mensaje': 'El precio no puede ser negativo.'})
                continue
            normalizado[campo] = val

    for campo in ('stock', 'cantidad_por_mayor'):
        if campo in cambio:
            raw = cambio[campo]
            try:
                val_int = int(raw)
            except (TypeError, ValueError):
                errores.append({'id': str(item_id), 'campo': campo, 'mensaje': 'Debe ser un número entero.'})
                continue
            if val_int < 0:
                errores.append({'id': str(item_id), 'campo': campo, 'mensaje': 'No puede ser negativo.'})
                continue
            normalizado[campo] = val_int

    if not normalizado and not errores:
        # Nadie mandó campos editables. Lo ignoramos sin error: el
        # front podría haber mandado una fila "vacía" por algún
        # bug y no queremos romper el batch entero por eso.
        return None, []

    normalizado['_id'] = item_id
    return normalizado, errores


@staff_member_required
@require_POST
def api_grilla_guardar(request: HttpRequest) -> JsonResponse:
    """
    POST /articulos/api/grilla/guardar/

    Body JSON:
      {"cambios": [{"id": 4521, "precio_minorista": "1200.00", ...}, ...]}

    Estrategia: validamos TODO el batch primero. Si hay UN error,
    devolvemos 400 y no tocamos la DB. Esto evita el caso "actualicé
    50 filas y la 51 era inválida, quedé en estado inconsistente
    con la mitad guardada" — el operador ve un error claro y vuelve
    a intentar.

    Para los updates usamos `save(update_fields=[campos cambiados])`
    en lugar de un `.update()` masivo: a) auditlog hookea al pre/post
    save y queremos los registros, b) `update_fields` evita que el
    override `Articulo.save()` regenere `codigo_interno` cuando no
    debe (sólo lo hace si el campo está en el SQL que arma el ORM).

    Todo va en una transacción atómica: si revienta a mitad de
    camino, no queda nada aplicado.
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'errores': [{'mensaje': 'JSON inválido.'}]}, status=400)

    cambios_raw = payload.get('cambios') or []
    if not isinstance(cambios_raw, list):
        return JsonResponse(
            {'ok': False, 'errores': [{'mensaje': '"cambios" debe ser una lista.'}]},
            status=400,
        )

    # Validamos todo primero, acumulando errores. Si hay errores,
    # devolvemos sin tocar la DB.
    normalizados: list[dict[str, Any]] = []
    errores_totales: list[dict[str, str]] = []
    for crudo in cambios_raw:
        if not isinstance(crudo, dict):
            errores_totales.append({'mensaje': 'Cambio mal formado (no es objeto).'})
            continue
        norm, errs = _validar_y_normalizar(crudo)
        if errs:
            errores_totales.extend(errs)
            continue
        if norm is not None:
            normalizados.append(norm)

    if errores_totales:
        return JsonResponse({'ok': False, 'errores': errores_totales}, status=400)

    if not normalizados:
        # Nada que hacer — devolvemos OK pero con 0 cambios para
        # que el front no muestre un mensaje raro de éxito.
        return JsonResponse({'ok': True, 'actualizados': 0})

    # Fetch en bloque de todos los Articulos que vamos a tocar
    # (un solo query) en vez de un get() por fila.
    ids = [n['_id'] for n in normalizados]
    articulos = {a.id: a for a in Articulo.objects.filter(id__in=ids)}

    faltantes = [str(i) for i in ids if i not in articulos]
    if faltantes:
        return JsonResponse(
            {
                'ok': False,
                'errores': [{'id': fid, 'mensaje': 'Artículo no encontrado.'} for fid in faltantes],
            },
            status=400,
        )

    actualizados = 0
    with transaction.atomic():
        for n in normalizados:
            articulo = articulos[n['_id']]
            cambiados: list[str] = []
            for campo in CAMPOS_EDITABLES:
                if campo in n:
                    setattr(articulo, campo, n[campo])
                    cambiados.append(campo)
            if cambiados:
                # update_fields hace dos cosas a la vez: minimiza el
                # UPDATE a las columnas que realmente cambiaron Y
                # le indica al override de save() que no recalcule
                # codigo_interno (que mira a self.codigo_interno; si
                # no está en update_fields, no se persiste igual).
                articulo.save(update_fields=cambiados)
                actualizados += 1

    return JsonResponse({'ok': True, 'actualizados': actualizados})
