"""
Vista de "Lista de Precios" — pantalla custom para armar listas de
precios personalizadas por cliente (con descuento global opcional,
items ordenables, notas por item) y generarlas en PDF.

Igual que en `views_grilla.py`, mantenemos esta feature en su propio
módulo para no engordar `views.py` (full de vistas server-render
viejas con otro estilo).

Flujo:

  1. GET /articulos/lista-precios/
        Render del template inicial (sin datos pre-cargados; el front
        carga todo vía las APIs JSON).

  2. GET  /articulos/api/lista-precios/cliente/<id>/listas/
        Listas guardadas previamente para el cliente (para que el
        operador pueda elegir entre "cargar lista existente" o "crear
        nueva").

  3. GET  /articulos/api/lista-precios/cliente/<id>/lista/<lista_id>/
        Detalle de una lista guardada con los precios efectivos ya
        calculados (PrecioCliente + descuento de la lista).

  4. POST /articulos/api/lista-precios/guardar/
        Crea o actualiza una lista (con sus items). Transaction
        atomic; en update, borra los items viejos y vuelve a
        crearlos (más simple que diff por item).

  5. GET  /articulos/api/lista-precios/pdf/<lista_id>/
        PDF con la lista (cliente, nombre, fecha, items con su
        precio efectivo). Reusa reportlab igual que el PDF de venta.

  6. GET  /articulos/api/lista-precios/articulos/
        Listado paginado de artículos disponibles con filtros
        (categoría, proveedor, búsqueda) para el panel izquierdo
        del armado de la lista. Devuelve `precio_efectivo` ya
        calculado para el cliente seleccionado.

Todos los endpoints requieren staff (igual que la grilla — no es
una pantalla de cara al cliente).
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from cliente.models import Cliente
from .models import Articulo, Categoria, ListaPrecios, ListaPreciosItem
from .precios import cargar_precios_pactados, precio_efectivo

try:
    from compra.models import Proveedor
except Exception:  # pragma: no cover - solo si compra esta roto
    Proveedor = None  # type: ignore[assignment]


def _page_size() -> int:
    """Cuántos artículos devolvemos en el listado de disponibles."""
    return int(getattr(settings, 'LISTA_PRECIOS_PAGE_SIZE', 30))


# ---------------------------------------------------------------------------
# Render del template
# ---------------------------------------------------------------------------
@staff_member_required
def lista_precios_pantalla(request: HttpRequest) -> HttpResponse:
    """
    Render de la pantalla custom de lista de precios.

    Solo pasamos las opciones para los <select> de filtros (categorías
    y proveedores). El cliente, los artículos y la lista en sí los
    carga el front a demanda vía API.
    """
    categorias = list(
        Categoria.objects.order_by('nombre').values('id', 'nombre', 'color')
    )
    proveedores: list[dict[str, Any]] = []
    if Proveedor is not None:
        proveedores = list(
            Proveedor.objects.order_by('nombre').values('id', 'nombre')
        )

    return render(
        request,
        'articulo/lista_precios.html',
        {
            'categorias': categorias,
            'proveedores': proveedores,
        },
    )


# ---------------------------------------------------------------------------
# Helpers de serialización
# ---------------------------------------------------------------------------
def _serializar_item(item: ListaPreciosItem, cliente, descuento_lista, pactados_map) -> dict[str, Any]:
    """
    Convierte un `ListaPreciosItem` a dict JSON-friendly, con el
    precio efectivo ya calculado (PrecioCliente + descuento de
    la lista).
    """
    articulo = item.articulo
    efectivo = precio_efectivo(
        articulo,
        cliente,
        descuento_lista=descuento_lista,
        precios_pactados_map=pactados_map,
    )
    tiene_pactado = pactados_map.get(articulo.id) is not None
    return {
        'articulo_id': articulo.id,
        'articulo_codigo': articulo.codigo or articulo.codigo_interno or '',
        'articulo_nombre': articulo.nombre,
        'articulo_marca': articulo.marca or '',
        'precio_minorista': str(articulo.precio_minorista or 0),
        'precio_efectivo': str(efectivo),
        'tiene_precio_pactado': tiene_pactado,
        'orden': item.orden,
        'nota': item.nota or '',
    }


# ---------------------------------------------------------------------------
# API: listas previas de un cliente
# ---------------------------------------------------------------------------
@staff_member_required
@require_GET
def api_listas_del_cliente(request: HttpRequest, cliente_id: int) -> JsonResponse:
    """
    GET /articulos/api/lista-precios/cliente/<cliente_id>/listas/

    Devuelve las listas guardadas para ese cliente (id, nombre,
    descuento, fecha de update, count_items). Para que el front
    arme el dropdown "cargar lista existente / crear nueva".
    """
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    qs = (
        ListaPrecios.objects
        .filter(cliente=cliente)
        .order_by('-updated_at')
    )
    # `cantidad_items()` haría 1 query por lista (N+1). Usamos
    # annotate para resolverlo en un solo SQL.
    from django.db.models import Count
    qs = qs.annotate(_count_items=Count('items'))

    listas = [{
        'id': l.id,
        'nombre': l.nombre,
        'descuento_porcentaje': str(l.descuento_porcentaje),
        'descuento_motivo': l.descuento_motivo or '',
        'count_items': l._count_items,
        'updated_at': l.updated_at.isoformat(),
    } for l in qs]

    return JsonResponse({
        'cliente_id': cliente.id,
        'cliente_nombre': cliente.nombre_completo(),
        'cliente_saldo': str(cliente.saldo),
        'listas': listas,
    })


# ---------------------------------------------------------------------------
# API: detalle de una lista guardada
# ---------------------------------------------------------------------------
@staff_member_required
@require_GET
def api_detalle_lista(request: HttpRequest, cliente_id: int, lista_id: int) -> JsonResponse:
    """
    GET /articulos/api/lista-precios/cliente/<cliente_id>/lista/<lista_id>/

    Detalle de una lista con sus items y los precios efectivos
    calculados al momento (no se persisten — la lista solo guarda
    qué artículos y en qué orden; el precio se recalcula cada
    vez para que refleje los cambios de minorista/PrecioCliente).
    """
    cliente = get_object_or_404(Cliente, pk=cliente_id)
    lista = get_object_or_404(
        ListaPrecios.objects.select_related('cliente'),
        pk=lista_id,
        cliente=cliente,
    )
    items_qs = (
        lista.items
        .select_related('articulo')
        .order_by('orden', 'articulo__nombre')
    )
    items = list(items_qs)
    articulos = [i.articulo for i in items]
    # Pre-carga de precios pactados en una sola query — evita N+1
    # al iterar.
    pactados = cargar_precios_pactados(cliente, articulos)

    items_data = [
        _serializar_item(it, cliente, lista.descuento_porcentaje, pactados)
        for it in items
    ]

    return JsonResponse({
        'id': lista.id,
        'cliente_id': cliente.id,
        'cliente_nombre': cliente.nombre_completo(),
        'nombre': lista.nombre,
        'descuento_porcentaje': str(lista.descuento_porcentaje),
        'descuento_motivo': lista.descuento_motivo or '',
        'updated_at': lista.updated_at.isoformat(),
        'items': items_data,
    })


# ---------------------------------------------------------------------------
# API: listado paginado de artículos disponibles (para el panel izquierdo)
# ---------------------------------------------------------------------------
@staff_member_required
@require_GET
def api_articulos_disponibles(request: HttpRequest) -> JsonResponse:
    """
    GET /articulos/api/lista-precios/articulos/?cliente_id=X&categoria=&proveedor=&q=&page=

    Devuelve artículos paginados con filtros (igual que la grilla).
    Si viene `cliente_id`, calcula `precio_efectivo` para ese cliente
    (sin descuento de lista — el descuento se aplica en el front en
    vivo y al guardar/PDF en el backend).
    """
    cliente = None
    cliente_id_raw = request.GET.get('cliente_id', '').strip()
    if cliente_id_raw:
        try:
            cliente = Cliente.objects.get(pk=int(cliente_id_raw))
        except (Cliente.DoesNotExist, ValueError, TypeError):
            cliente = None

    raw_categoria = request.GET.get('categoria', '').strip()
    raw_proveedor = request.GET.get('proveedor', '').strip()
    q = request.GET.get('q', '').strip()
    raw_page = request.GET.get('page', '1').strip()

    def _to_int(v: str) -> int | None:
        if v == '':
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    categoria_id = _to_int(raw_categoria)
    proveedor_id = _to_int(raw_proveedor)
    try:
        page = max(1, int(raw_page))
    except (TypeError, ValueError):
        page = 1

    qs = (
        Articulo.objects.all()
        .select_related('categoria', 'proveedor')
        .order_by('categoria__nombre', 'nombre')
    )
    if categoria_id is not None:
        if categoria_id == 0:
            qs = qs.filter(categoria__isnull=True)
        else:
            qs = qs.filter(categoria_id=categoria_id)
    if proveedor_id is not None:
        if proveedor_id == 0:
            qs = qs.filter(proveedor__isnull=True)
        else:
            qs = qs.filter(proveedor_id=proveedor_id)
    if q:
        qs = qs.filter(
            Q(nombre__icontains=q)
            | Q(codigo__icontains=q)
            | Q(codigo_interno__icontains=q)
            | Q(marca__icontains=q)
        )

    paginator = Paginator(qs, _page_size())
    page_obj = paginator.get_page(page)
    articulos_page = list(page_obj.object_list)

    # Pre-cargo de precios pactados para el cliente sobre ESTA página.
    pactados: dict = {}
    if cliente is not None:
        pactados = cargar_precios_pactados(cliente, articulos_page)

    items = []
    for a in articulos_page:
        precio_eff: Decimal
        if cliente is not None:
            # No aplicamos descuento de lista acá — el front lo
            # aplica en vivo cuando el operador edita el % global.
            precio_eff = precio_efectivo(
                a, cliente,
                descuento_lista=None,
                precios_pactados_map=pactados,
            )
            tiene_pactado = pactados.get(a.id) is not None
        else:
            precio_eff = a.precio_minorista or Decimal('0')
            tiene_pactado = False
        items.append({
            'id': a.id,
            'codigo': a.codigo or a.codigo_interno or '',
            'nombre': a.nombre,
            'marca': a.marca or '',
            'categoria_id': a.categoria_id,
            'categoria_nombre': a.categoria.nombre if a.categoria_id else None,
            'categoria_color': a.categoria.color if a.categoria_id else None,
            'proveedor_id': a.proveedor_id,
            'proveedor_nombre': a.proveedor.nombre if a.proveedor_id else None,
            'precio_minorista': str(a.precio_minorista or 0),
            'precio_efectivo': str(precio_eff),
            'tiene_precio_pactado': tiene_pactado,
        })

    return JsonResponse({
        'page': page_obj.number,
        'total_pages': paginator.num_pages,
        'total_items': paginator.count,
        'items': items,
    })


# ---------------------------------------------------------------------------
# API: guardar lista (create o update)
# ---------------------------------------------------------------------------
@staff_member_required
@require_POST
def api_guardar_lista(request: HttpRequest) -> JsonResponse:
    """
    POST /articulos/api/lista-precios/guardar/

    Body:
      {
        "id": null | int,         # null = crear, int = actualizar
        "cliente_id": int,
        "nombre": "...",
        "descuento_porcentaje": "5.00",
        "descuento_motivo": "...",
        "items": [{articulo_id, orden, nota}, ...]
      }

    Si es update, borra TODOS los items viejos y crea los nuevos.
    Para una lista de pocas decenas de items, hacer diff por item
    no compensa la complejidad de código (y el unique_together
    `(lista, articulo)` impide reusar los IDs viejos sin chequeos
    finos). El M2M no tiene FKs apuntándole, así que el churn es
    contenido (solo dispara auditlog sobre ListaPreciosItem).
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'errores': [{'mensaje': 'JSON inválido.'}]}, status=400)

    errores: list[dict[str, str]] = []

    cliente_id = payload.get('cliente_id')
    if not cliente_id:
        errores.append({'campo': 'cliente_id', 'mensaje': 'Falta el cliente.'})
    try:
        cliente = Cliente.objects.get(pk=cliente_id) if cliente_id else None
    except Cliente.DoesNotExist:
        cliente = None
        errores.append({'campo': 'cliente_id', 'mensaje': 'Cliente no encontrado.'})

    nombre = (payload.get('nombre') or '').strip()
    if not nombre:
        errores.append({'campo': 'nombre', 'mensaje': 'El nombre de la lista es obligatorio.'})

    desc_raw = payload.get('descuento_porcentaje', '0')
    try:
        descuento = Decimal(str(desc_raw or '0'))
    except (InvalidOperation, TypeError):
        descuento = Decimal('0')
        errores.append({'campo': 'descuento_porcentaje', 'mensaje': 'Descuento no es un número válido.'})
    if descuento < 0 or descuento > 100:
        errores.append({'campo': 'descuento_porcentaje', 'mensaje': 'El descuento debe estar entre 0 y 100.'})

    motivo = (payload.get('descuento_motivo') or '').strip()

    raw_items = payload.get('items') or []
    if not isinstance(raw_items, list):
        errores.append({'campo': 'items', 'mensaje': 'items debe ser una lista.'})
        raw_items = []

    # Validamos items: cada uno debe traer articulo_id válido.
    items_normalizados: list[dict[str, Any]] = []
    ids_vistos: set[int] = set()
    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            errores.append({'mensaje': f'Item {idx} mal formado.'})
            continue
        try:
            articulo_id = int(raw.get('articulo_id'))
        except (TypeError, ValueError):
            errores.append({'mensaje': f'Item {idx}: articulo_id inválido.'})
            continue
        if articulo_id in ids_vistos:
            errores.append({'mensaje': f'Artículo duplicado en la lista (id={articulo_id}).'})
            continue
        ids_vistos.add(articulo_id)
        try:
            orden = int(raw.get('orden', idx))
        except (TypeError, ValueError):
            orden = idx
        nota = (raw.get('nota') or '').strip()[:120]
        items_normalizados.append({
            'articulo_id': articulo_id,
            'orden': orden,
            'nota': nota,
        })

    if items_normalizados:
        existentes = set(
            Articulo.objects.filter(
                id__in=[i['articulo_id'] for i in items_normalizados]
            ).values_list('id', flat=True)
        )
        for i in items_normalizados:
            if i['articulo_id'] not in existentes:
                errores.append({'mensaje': f'Artículo no encontrado (id={i["articulo_id"]}).'})

    if errores:
        return JsonResponse({'ok': False, 'errores': errores}, status=400)

    lista_id = payload.get('id')

    with transaction.atomic():
        if lista_id:
            try:
                lista = ListaPrecios.objects.select_for_update().get(
                    pk=lista_id, cliente=cliente,
                )
            except ListaPrecios.DoesNotExist:
                return JsonResponse(
                    {'ok': False, 'errores': [{'mensaje': 'Lista no encontrada.'}]},
                    status=404,
                )
            lista.nombre = nombre
            lista.descuento_porcentaje = descuento
            lista.descuento_motivo = motivo
            lista.save()
            # Wipe & re-create. Más simple que diff y para listas
            # típicas (<200 items) el costo es despreciable.
            lista.items.all().delete()
        else:
            lista = ListaPrecios.objects.create(
                cliente=cliente,
                nombre=nombre,
                descuento_porcentaje=descuento,
                descuento_motivo=motivo,
                creado_por=request.user if request.user.is_authenticated else None,
            )

        # bulk_create de items: 1 SQL INSERT con todas las filas.
        ListaPreciosItem.objects.bulk_create([
            ListaPreciosItem(
                lista=lista,
                articulo_id=i['articulo_id'],
                orden=i['orden'],
                nota=i['nota'],
            )
            for i in items_normalizados
        ])

    return JsonResponse({'ok': True, 'lista_id': lista.id})


# ---------------------------------------------------------------------------
# API: PDF de una lista
# ---------------------------------------------------------------------------
@staff_member_required
@require_GET
def api_pdf_lista(request: HttpRequest, lista_id: int) -> HttpResponse:
    """
    GET /articulos/api/lista-precios/pdf/<lista_id>/

    Genera un PDF simple con la lista. La estructura es deliberadamente
    más sobria que la del PDF de venta — la lista de precios se
    imprime / se comparte por WhatsApp y queremos algo legible y
    autodescriptivo.

    Estructura:
      - Cabecera con cliente + nombre de la lista + fecha.
      - Tabla: Código | Artículo | Precio (efectivo, con desc lista
        aplicado si corresponde).
      - Si lista tiene `descuento_porcentaje > 0`: nota al pie con
        el descuento aplicado y el motivo si lo hay.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    lista = get_object_or_404(
        ListaPrecios.objects.select_related('cliente'),
        pk=lista_id,
    )
    cliente = lista.cliente
    items_qs = (
        lista.items
        .select_related('articulo')
        .order_by('orden', 'articulo__nombre')
    )
    items = list(items_qs)
    articulos = [i.articulo for i in items]
    pactados = cargar_precios_pactados(cliente, articulos)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=f'Lista de precios — {cliente.nombre_completo()}',
    )
    styles = getSampleStyleSheet()
    elements = []

    titulo_style = ParagraphStyle(
        name='titulo',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=4,
    )
    sub_style = ParagraphStyle(
        name='sub',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.grey,
        spaceAfter=8,
    )
    elements.append(Paragraph(f'Lista de precios: {lista.nombre}', titulo_style))
    elements.append(Paragraph(
        f'Cliente: <b>{cliente.nombre_completo()}</b> · '
        f'Fecha: {datetime.now().strftime("%d/%m/%Y")}',
        sub_style,
    ))

    # Cabecera + filas.
    # Si NINGUN item tiene nota, no mostramos la columna para no
    # malgastar ancho. Lo mismo si TODOS tienen nota: la tabla la
    # incluye y se sirve más expresiva.
    hay_notas = any((it.nota or '').strip() for it in items)
    if hay_notas:
        header = ['Código', 'Artículo', 'Nota', 'Precio']
    else:
        header = ['Código', 'Artículo', 'Precio']
    data = [header]

    for it in items:
        articulo = it.articulo
        precio = precio_efectivo(
            articulo, cliente,
            descuento_lista=lista.descuento_porcentaje,
            precios_pactados_map=pactados,
        )
        nombre = articulo.nombre
        if articulo.marca and articulo.marca != 'Generico':
            nombre = f'{articulo.marca} — {nombre}'
        # Marca con (*) los precios pactados (consistencia con el
        # PDF de venta).
        if pactados.get(articulo.id) is not None:
            nombre = f'(*) {nombre}'
        fila = [
            articulo.codigo or articulo.codigo_interno or '',
            nombre,
        ]
        if hay_notas:
            fila.append(it.nota or '')
        fila.append(f'${precio:.2f}')
        data.append(fila)

    if not items:
        # Tabla vacía con una nota — evitamos un PDF mudo.
        data.append(['—', '(sin items en esta lista)', '—'] if not hay_notas else ['—', '(sin items)', '', '—'])

    # Anchos de columna. Ajustes a ojo para A4 con márgenes 1.5cm.
    if hay_notas:
        col_widths = [2.5 * cm, 7.5 * cm, 4.5 * cm, 3.5 * cm]
    else:
        col_widths = [3 * cm, 11 * cm, 4 * cm]
    tabla = Table(data, colWidths=col_widths, repeatRows=1)
    tabla.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (-1, 1), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(tabla)

    # Nota al pie: descuento y precios pactados.
    nota_style = ParagraphStyle(
        name='pie',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.grey,
        spaceBefore=6,
    )
    pies = []
    if lista.descuento_porcentaje and lista.descuento_porcentaje > 0:
        txt = f'Precios con descuento del {lista.descuento_porcentaje:g}% aplicado'
        if lista.descuento_motivo:
            txt += f' ({lista.descuento_motivo})'
        pies.append(txt + '.')
    if any(pactados.get(a.id) is not None for a in articulos):
        pies.append('(*) Precio acordado con el cliente.')

    if pies:
        elements.append(Spacer(1, 0.3 * cm))
        for p in pies:
            elements.append(Paragraph(p, nota_style))

    doc.build(elements)

    pdf_bytes = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    # `inline` para que el browser intente mostrarlo (el operador
    # luego elige descargar/imprimir). Slug del nombre para que el
    # filename sea predecible.
    safe_name = ''.join(c if c.isalnum() else '_' for c in lista.nombre)[:40] or 'lista'
    response['Content-Disposition'] = f'inline; filename="lista_{safe_name}_{lista.id}.pdf"'
    return response
