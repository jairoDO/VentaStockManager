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
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, ProtectedError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .models import Articulo, Categoria, Rubro

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
#
# categoria_id y proveedor_id se agregaron en 2026-05 — el operador
# pidió poder recategorizar inline (cuando las reglas auto-asignan mal)
# sin tener que abrir cada artículo o hacer bulk acciones desde el
# admin clásico.
CAMPOS_EDITABLES = (
    'nombre',
    'precio_minorista', 'precio_mayorista', 'stock', 'cantidad_por_mayor',
    'categoria_id', 'proveedor_id',
)


# La grilla edita masivamente precios y crea artículos — operaciones
# de administración pura. El vendedor NO debería entrar (su rol es
# carga de ventas; los precios los maneja el dueño/admin). Si tu rol
# evoluciona, cambia este predicado.
def _solo_superuser(u) -> bool:
    return bool(u.is_authenticated and u.is_superuser)


superuser_required = user_passes_test(_solo_superuser, login_url='/admin/login/')


def _page_size() -> int:
    """Cuántos items devolvemos por página. Settable vía settings."""
    return int(getattr(settings, 'GRILLA_PRECIOS_PAGE_SIZE', 50))


@superuser_required
def grilla_precios(request: HttpRequest) -> HttpResponse:
    """
    Render del template. Solo pasamos el listado de categorías y
    proveedores para los <select> de filtros: los items en sí se
    cargan vía la API JSON desde Alpine, así no duplicamos la
    lógica de filtrado entre Python y JS.
    """
    # Las categorías incluyen `rubro_id` para que el front pueda
    # filtrar el dropdown de categoría a las del rubro elegido.
    categorias = list(
        Categoria.objects.order_by('nombre').values('id', 'nombre', 'color', 'rubro_id')
    )
    rubros = list(
        Rubro.objects.order_by('orden', 'nombre').values('id', 'nombre', 'color')
    )
    proveedores: list[dict[str, Any]] = []
    if Proveedor is not None:
        proveedores = list(Proveedor.objects.order_by('nombre').values('id', 'nombre'))

    return render(
        request,
        'articulo/grilla_precios.html',
        {
            'categorias': categorias,
            'rubros': rubros,
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
    raw_rubro = request.GET.get('rubro', '').strip()
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
    rubro_id = _to_int(raw_rubro)

    try:
        page = max(1, int(raw_page))
    except (TypeError, ValueError):
        page = 1

    return {
        'categoria_id': categoria_id,
        'proveedor_id': proveedor_id,
        'rubro_id': rubro_id,
        'q': q,
        'page': page,
    }


@superuser_required
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

    # Filtro por rubro: incluye TODAS las categorías que pertenecen al
    # rubro. Si además hay filtro de categoría, la categoría es el más
    # restrictivo (el rubro queda redundante pero no rompe).
    if filtros['rubro_id'] is not None:
        if filtros['rubro_id'] == 0:
            # rubro=0 → artículos cuya categoría no tiene rubro asignado.
            qs = qs.filter(categoria__rubro__isnull=True)
        else:
            qs = qs.filter(categoria__rubro_id=filtros['rubro_id'])
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

    # Nombre: si viene, lo limpiamos y validamos no-vacío. Lo aceptamos
    # editar inline en la grilla porque a veces el operador necesita
    # corregir tipeos / cambiar descripción sin entrar al admin clásico.
    if 'nombre' in cambio:
        nombre = (cambio.get('nombre') or '').strip()
        if not nombre:
            errores.append({'id': str(item_id), 'campo': 'nombre',
                            'mensaje': 'El nombre no puede quedar vacío.'})
        else:
            normalizado['nombre'] = nombre[:255]

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

    # FKs opcionales: aceptamos None / '' / '0' como "quitar la FK".
    # No chequeamos que el ID exista en DB — confiamos en el ON DELETE
    # SET NULL del FK + en que el select del front solo ofrece IDs
    # válidos. Si alguien forja un id inexistente, el save() falla
    # con IntegrityError y queda en logs (es atacante, no bug).
    for campo in ('categoria_id', 'proveedor_id'):
        if campo in cambio:
            raw = cambio[campo]
            if raw in (None, '', 0, '0'):
                normalizado[campo] = None
                continue
            try:
                normalizado[campo] = int(raw)
            except (TypeError, ValueError):
                errores.append({'id': str(item_id), 'campo': campo, 'mensaje': 'ID inválido.'})

    if not normalizado and not errores:
        # Nadie mandó campos editables. Lo ignoramos sin error: el
        # front podría haber mandado una fila "vacía" por algún
        # bug y no queremos romper el batch entero por eso.
        return None, []

    normalizado['_id'] = item_id
    return normalizado, errores


# Vencimiento por defecto cuando el operador crea un artículo inline
# sin especificarlo. Mantenemos el mismo "hoy + 90 días" que usa el
# sync de Google Sheets, para que el comportamiento sea consistente
# entre fuentes de creación.
_VENCIMIENTO_DEFAULT_DIAS = 90


def _validar_y_normalizar_nuevo(nuevo: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """
    Valida un dict crudo del POST que representa un artículo a CREAR.
    Devuelve `(normalizado, errores)`.

    Reglas:
      - `nombre`: requerido, no vacío.
      - `codigo` o `codigo_interno`: si ninguno viene, está OK — el
        save() del modelo genera `codigo_interno` automáticamente.
      - `precio_minorista`: Decimal >= 0 (default 0 si no viene).
      - `precio_mayorista`: Decimal >= 0 (default 0 si no viene).
      - `stock`: int >= 0 (default 0).
      - `cantidad_por_mayor`: int >= 0 (default 100, idem modelo).
      - `vencimiento`: ISO YYYY-MM-DD. Si no viene → hoy + 90 días
        (consistente con sync de Sheets).
      - `categoria_id`, `proveedor_id`: opcionales (null OK).

    El `_temp_id` se preserva en el dict normalizado para que la
    respuesta pueda emparejar las filas creadas con su placeholder
    del front.
    """
    errores: list[dict[str, str]] = []
    normalizado: dict[str, Any] = {}

    temp_id = nuevo.get('_temp_id') or nuevo.get('temp_id') or ''
    normalizado['_temp_id'] = str(temp_id)

    nombre = (nuevo.get('nombre') or '').strip()
    if not nombre:
        errores.append({'temp_id': str(temp_id), 'campo': 'nombre', 'mensaje': 'El nombre es obligatorio.'})
    else:
        normalizado['nombre'] = nombre[:255]

    # codigo / codigo_interno son opcionales — si no vienen, el save()
    # del modelo genera codigo_interno automáticamente. Si vienen, los
    # respetamos tal cual (no chequeamos unicidad acá; el DB no tiene
    # constraint y duplicados se permiten desde sync histórico).
    normalizado['codigo'] = (nuevo.get('codigo') or '').strip()[:255]
    codigo_interno_raw = (nuevo.get('codigo_interno') or '').strip()
    if codigo_interno_raw:
        normalizado['codigo_interno'] = codigo_interno_raw[:50]
    # else: dejamos que save() lo autogenere.

    normalizado['marca'] = (nuevo.get('marca') or 'Generico').strip()[:255]

    for campo, default in (('precio_minorista', '0'), ('precio_mayorista', '0')):
        raw = nuevo.get(campo, default)
        # Aceptamos string vacío como "no informado" → 0.
        if raw == '' or raw is None:
            normalizado[campo] = Decimal('0')
            continue
        try:
            val = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            errores.append({'temp_id': str(temp_id), 'campo': campo, 'mensaje': 'Precio no válido.'})
            continue
        if val < 0:
            errores.append({'temp_id': str(temp_id), 'campo': campo, 'mensaje': 'El precio no puede ser negativo.'})
            continue
        normalizado[campo] = val

    for campo, default in (('stock', 0), ('cantidad_por_mayor', 100)):
        raw = nuevo.get(campo, default)
        if raw == '' or raw is None:
            normalizado[campo] = default
            continue
        try:
            val_int = int(raw)
        except (TypeError, ValueError):
            errores.append({'temp_id': str(temp_id), 'campo': campo, 'mensaje': 'Debe ser un entero.'})
            continue
        if val_int < 0:
            errores.append({'temp_id': str(temp_id), 'campo': campo, 'mensaje': 'No puede ser negativo.'})
            continue
        normalizado[campo] = val_int

    # FK opcionales.
    for campo in ('categoria_id', 'proveedor_id'):
        raw = nuevo.get(campo)
        if raw in (None, '', 0, '0'):
            normalizado[campo] = None
            continue
        try:
            normalizado[campo] = int(raw)
        except (TypeError, ValueError):
            errores.append({'temp_id': str(temp_id), 'campo': campo, 'mensaje': 'ID inválido.'})

    # Vencimiento: campo NOT NULL del modelo. Si no viene, default a
    # hoy + 90 días (consistencia con sync de Sheets).
    venc_raw = (nuevo.get('vencimiento') or '').strip()
    if not venc_raw:
        normalizado['vencimiento'] = date.today() + timedelta(days=_VENCIMIENTO_DEFAULT_DIAS)
    else:
        try:
            # Aceptamos solo formato ISO YYYY-MM-DD (lo que mandan los
            # <input type=date>). Otros formatos los rechazamos en
            # vez de adivinar.
            normalizado['vencimiento'] = datetime.strptime(venc_raw, '%Y-%m-%d').date()
        except ValueError:
            errores.append({
                'temp_id': str(temp_id),
                'campo': 'vencimiento',
                'mensaje': 'Formato inválido (esperado YYYY-MM-DD).',
            })

    if errores:
        return None, errores
    return normalizado, []


@superuser_required
@require_POST
def api_grilla_guardar(request: HttpRequest) -> JsonResponse:
    """
    POST /articulos/api/grilla/guardar/

    Body JSON:
      {
        "cambios": [{"id": 4521, "precio_minorista": "1200.00", ...}, ...],
        "nuevos": [{"_temp_id": "tmp_1", "nombre": "...", ...}, ...]
      }

    Maneja dos operaciones en el mismo POST:
      - `cambios`: updates de filas existentes (igual que antes).
      - `nuevos`: creación inline de artículos desde la grilla.
        Cada uno trae un `_temp_id` que devolvemos en la respuesta
        emparejado con el `id` real, para que el front reemplace
        las filas placeholder con el ID definitivo.

    Estrategia: validamos TODO el batch primero (cambios + nuevos).
    Si hay UN error, devolvemos 400 y no tocamos la DB. Esto evita
    el caso "actualicé 50 filas y la 51 era inválida, quedé en
    estado inconsistente con la mitad guardada".

    Para los updates usamos `save(update_fields=[campos cambiados])`.
    Para los creates usamos `Articulo.objects.create(...)` (no
    `bulk_create`) para que el override de `Articulo.save()` se
    dispare y auto-genere `codigo_interno` si no vino, y para que
    auditlog registre la creación.

    Todo va en una sola transacción atómica.
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'errores': [{'mensaje': 'JSON inválido.'}]}, status=400)

    cambios_raw = payload.get('cambios') or []
    nuevos_raw = payload.get('nuevos') or []
    if not isinstance(cambios_raw, list):
        return JsonResponse(
            {'ok': False, 'errores': [{'mensaje': '"cambios" debe ser una lista.'}]},
            status=400,
        )
    if not isinstance(nuevos_raw, list):
        return JsonResponse(
            {'ok': False, 'errores': [{'mensaje': '"nuevos" debe ser una lista.'}]},
            status=400,
        )

    # ---- Validación de cambios (existentes) ----
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

    # ---- Validación de nuevos ----
    nuevos_normalizados: list[dict[str, Any]] = []
    for crudo in nuevos_raw:
        if not isinstance(crudo, dict):
            errores_totales.append({'mensaje': 'Nuevo mal formado (no es objeto).'})
            continue
        norm, errs = _validar_y_normalizar_nuevo(crudo)
        if errs:
            errores_totales.extend(errs)
            continue
        if norm is not None:
            nuevos_normalizados.append(norm)

    if errores_totales:
        return JsonResponse({'ok': False, 'errores': errores_totales}, status=400)

    if not normalizados and not nuevos_normalizados:
        return JsonResponse({'ok': True, 'actualizados': 0, 'creados': []})

    # Fetch en bloque de los artículos a updatear.
    ids = [n['_id'] for n in normalizados]
    articulos = {a.id: a for a in Articulo.objects.filter(id__in=ids)} if ids else {}

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
    creados: list[dict[str, Any]] = []
    with transaction.atomic():
        for n in normalizados:
            articulo = articulos[n['_id']]
            cambiados: list[str] = []
            for campo in CAMPOS_EDITABLES:
                if campo in n:
                    setattr(articulo, campo, n[campo])
                    cambiados.append(campo)
            if cambiados:
                articulo.save(update_fields=cambiados)
                actualizados += 1

        # Creación de nuevos. Usamos `create` (no bulk_create) para
        # que el override de save() dispare la auto-generación de
        # codigo_interno y para que auditlog registre la creación.
        for n in nuevos_normalizados:
            kwargs = {
                'nombre': n['nombre'],
                'codigo': n['codigo'],
                'marca': n['marca'],
                'stock': n['stock'],
                'precio_minorista': n['precio_minorista'],
                'precio_mayorista': n['precio_mayorista'],
                'cantidad_por_mayor': n['cantidad_por_mayor'],
                'vencimiento': n['vencimiento'],
                'categoria_id': n['categoria_id'],
                'proveedor_id': n['proveedor_id'],
            }
            if 'codigo_interno' in n:
                kwargs['codigo_interno'] = n['codigo_interno']
            articulo = Articulo.objects.create(**kwargs)
            creados.append({
                '_temp_id': n['_temp_id'],
                'id': articulo.id,
                'codigo_interno': articulo.codigo_interno or '',
            })

    return JsonResponse({
        'ok': True,
        'actualizados': actualizados,
        'creados': creados,
    })


@superuser_required
@require_POST
def api_grilla_eliminar(request: HttpRequest) -> JsonResponse:
    """
    POST /articulos/api/grilla/eliminar/

    Body JSON: {"ids": [1, 2, 3]}

    Borra los artículos uno por uno (NO en bulk delete). El motivo es
    que algunos van a fallar con `ProtectedError` por tener
    `ArticuloVenta` asociado (FK PROTECT — preserva historial), y
    queremos reportar específicamente cuáles fueron para que el
    operador sepa qué pasó. Un bulk `.delete()` abortaría todo si
    UNO fallara.

    Responde:
      {
        "ok": True,
        "borrados": [<ids>],
        "fallidos": [{"id": N, "nombre": "...", "razon": "..."}, ...]
      }
    """
    try:
        payload = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    raw_ids = payload.get('ids') or []
    if not isinstance(raw_ids, list):
        return JsonResponse({'ok': False, 'error': 'Se esperaba ids: lista'}, status=400)

    # Coerción defensiva.
    ids: list[int] = []
    for x in raw_ids:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue

    if not ids:
        return JsonResponse({'ok': True, 'borrados': [], 'fallidos': []})

    # Limitamos a 500 por seguridad. Si el operador necesita más,
    # mejor que lo haga en tandas — un delete masivo gigante puede
    # bloquear la DB y disparar timeouts.
    if len(ids) > 500:
        return JsonResponse({
            'ok': False,
            'error': 'Demasiados artículos en un solo request (max 500). '
                     'Eliminá en tandas más chicas.',
        }, status=400)

    # Flag opcional: forzar eliminación borrando primero las referencias
    # PROTECTed (ArticuloVenta, AlertaStock, ListaPreciosItem).
    # Solo lo usa el operador cuando QUIERE eliminar un artículo que tiene
    # histórico (típico en cleanup post-cutover con duplicados del dump).
    force = bool(payload.get('force', False))

    borrados: list[dict] = []
    fallidos: list[dict] = []

    # NO usamos `transaction.atomic` envolviendo todo: queremos que los
    # borrados exitosos persistan aunque algunos fallen. Cada delete()
    # ya es atómico per-row.
    qs = Articulo.objects.filter(id__in=ids)
    arts_by_id = {a.id: a for a in qs}

    # Si force=True, importamos los modelos referenciantes para limpiar
    # antes de cada delete. Import local para no agregar dependencia
    # circular al cargar el módulo.
    if force:
        from venta.models import ArticuloVenta, AlertaStock
        from articulo.models import ListaPreciosItem

    for aid in ids:
        art = arts_by_id.get(aid)
        if art is None:
            fallidos.append({
                'id': aid, 'nombre': '(no encontrado)',
                'razon': 'No existe en la DB.',
            })
            continue
        nombre = art.nombre  # capturamos antes del delete

        try:
            cascadas = {}
            if force:
                # Borrar PROTECTed refs ANTES del delete del artículo.
                # Estos counts van al reporte para que el operador sepa
                # qué histórico se perdió.
                n_av = ArticuloVenta.objects.filter(articulo=art).count()
                if n_av:
                    ArticuloVenta.objects.filter(articulo=art).delete()
                    cascadas['lineas_venta'] = n_av
                n_as = AlertaStock.objects.filter(articulo=art).count()
                if n_as:
                    AlertaStock.objects.filter(articulo=art).delete()
                    cascadas['alertas_stock'] = n_as
                n_lpi = ListaPreciosItem.objects.filter(articulo=art).count()
                if n_lpi:
                    ListaPreciosItem.objects.filter(articulo=art).delete()
                    cascadas['lista_precios_items'] = n_lpi
            art.delete()
            borrados.append({
                'id': aid, 'nombre': nombre, 'cascadas': cascadas,
            })
        except ProtectedError:
            fallidos.append({
                'id': aid, 'nombre': nombre,
                'razon': (
                    'Tiene ventas/alertas/listas asociadas. Volvé a probar '
                    'marcando "Forzar eliminación" para borrar también el '
                    'histórico (irreversible).'
                ),
            })
        except Exception as e:
            fallidos.append({
                'id': aid, 'nombre': nombre,
                'razon': f'Error inesperado: {e}',
            })

    return JsonResponse({
        'ok': True,
        'borrados': borrados,
        'fallidos': fallidos,
        'forzado': force,
    })


@superuser_required
@require_POST
def api_grilla_fusionar_duplicados(request: HttpRequest) -> JsonResponse:
    """
    POST /articulos/api/grilla/fusionar/

    Body JSON: {"ids": [1, 2, 3]}

    Para cada artículo en `ids`:
      1. Busca OTRO articulo con el MISMO `codigo` que NO esté en `ids`
         (el "sobreviviente"). Si hay varios candidatos, el más viejo gana
         (lowest id) — asume que el original es el que tiene más histórico.
      2. Reasigna ArticuloVenta, AlertaStock, ListaPreciosItem del artículo
         a borrar → al sobreviviente.
      3. Borra el artículo (ya sin PROTECTed refs).

    Si no hay sobreviviente (no hay duplicado, o todos los duplicados están
    en la lista de borrar) → el artículo no se borra, se reporta como fallido.

    Si el artículo no tiene `codigo` → fallido (no podemos matchear).

    Response:
      {
        "ok": true,
        "borrados": [{"id", "nombre", "survivor": {"id", "nombre"}, "reasignado": {...}}],
        "fallidos": [{"id", "nombre", "razon"}]
      }
    """
    try:
        payload = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    raw_ids = payload.get('ids') or []
    ids: list[int] = []
    for x in raw_ids:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue

    if not ids:
        return JsonResponse({'ok': True, 'borrados': [], 'fallidos': []})

    if len(ids) > 500:
        return JsonResponse({
            'ok': False,
            'error': 'Demasiados artículos en un solo request (max 500).',
        }, status=400)

    from venta.models import ArticuloVenta, AlertaStock
    from articulo.models import ListaPreciosItem

    borrados: list[dict] = []
    fallidos: list[dict] = []

    qs = Articulo.objects.filter(id__in=ids)
    arts_by_id = {a.id: a for a in qs}

    for aid in ids:
        art = arts_by_id.get(aid)
        if art is None:
            fallidos.append({
                'id': aid, 'nombre': '(no encontrado)',
                'razon': 'No existe en la DB.',
            })
            continue

        nombre = art.nombre
        codigo = (art.codigo or '').strip()
        if not codigo:
            fallidos.append({
                'id': aid, 'nombre': nombre,
                'razon': 'El artículo no tiene código — no se puede buscar duplicado.',
            })
            continue

        # Sobreviviente: otro art con mismo codigo, NO en la lista de
        # borrados, más viejo primero.
        survivor = (
            Articulo.objects
            .filter(codigo=codigo)
            .exclude(pk=art.pk)
            .exclude(pk__in=ids)
            .order_by('pk')
            .first()
        )
        if survivor is None:
            fallidos.append({
                'id': aid, 'nombre': nombre,
                'razon': (
                    f'No hay otro artículo con código "{codigo}" para fusionar. '
                    f'Si querés borrarlo sin reasignar, usá "Forzar eliminación".'
                ),
            })
            continue

        try:
            # Reasignar TODAS las refs PROTECTed al sobreviviente.
            # `.update()` masivo es atómico y NO dispara signals — perfecto
            # para esta operación de mantenimiento.
            n_av = ArticuloVenta.objects.filter(articulo=art).update(articulo=survivor)
            n_as = AlertaStock.objects.filter(articulo=art).update(articulo=survivor)
            n_lpi = ListaPreciosItem.objects.filter(articulo=art).update(articulo=survivor)
            art.delete()
            borrados.append({
                'id': aid, 'nombre': nombre,
                'survivor': {'id': survivor.id, 'nombre': survivor.nombre},
                'reasignado': {
                    'lineas_venta': n_av,
                    'alertas_stock': n_as,
                    'lista_precios_items': n_lpi,
                },
            })
        except ProtectedError:
            fallidos.append({
                'id': aid, 'nombre': nombre,
                'razon': (
                    'El artículo tiene refs PROTECTed que no son ArticuloVenta/'
                    'AlertaStock/ListaPreciosItem. Reportá esto — falta cubrir un modelo.'
                ),
            })
        except Exception as e:
            fallidos.append({
                'id': aid, 'nombre': nombre,
                'razon': f'Error inesperado: {e}',
            })

    return JsonResponse({
        'ok': True,
        'borrados': borrados,
        'fallidos': fallidos,
    })
