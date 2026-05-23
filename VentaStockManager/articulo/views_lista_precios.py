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
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from cliente.models import Cliente
from .models import Articulo, Categoria, ListaPrecios, ListaPreciosItem, Rubro
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
    # Las categorías llevan `rubro_id` para que el front pueda filtrar
    # el dropdown de categoría a las que pertenecen al rubro elegido.
    categorias = list(
        Categoria.objects.order_by('nombre').values('id', 'nombre', 'color', 'rubro_id')
    )
    # Rubros ordenados por `orden` (manual del operador) y después nombre.
    rubros = list(
        Rubro.objects.order_by('orden', 'nombre').values('id', 'nombre', 'color')
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
            'rubros': rubros,
            'proveedores': proveedores,
        },
    )


# ---------------------------------------------------------------------------
# Helpers de serialización
# ---------------------------------------------------------------------------
def _serializar_item(item: ListaPreciosItem, cliente, descuento_lista, pactados_map, tipo_ajuste: str = 'descuento') -> dict[str, Any]:
    """
    Convierte un `ListaPreciosItem` a dict JSON-friendly, con el
    precio efectivo ya calculado (PrecioCliente + ajuste de la lista,
    sea descuento o aumento según `tipo_ajuste`).
    """
    articulo = item.articulo
    efectivo = precio_efectivo(
        articulo,
        cliente,
        descuento_lista=descuento_lista,
        precios_pactados_map=pactados_map,
        tipo_ajuste=tipo_ajuste,
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
        'tipo_ajuste': l.tipo_ajuste,
        'descuento_motivo': l.descuento_motivo or '',
        'count_items': l._count_items,
        'updated_at': l.updated_at.isoformat(),
        # `link_activo` se calcula en Python (property). No vale la
        # pena annotate-arlo: el queryset es chico (listas por cliente)
        # y el check de `share_expira_at > now()` queda redundante con
        # el property.
        'link_activo': l.link_activo,
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
        _serializar_item(it, cliente, lista.descuento_porcentaje, pactados, lista.tipo_ajuste)
        for it in items
    ]

    # Resolver el modo efectivo de envío para este cliente. El front
    # lo usa para mostrar en el modal "voy a mandar en modo X" sin
    # preguntar al operador (a menos que él quiera cambiarlo).
    # Misma cascada que la API de difundir: cliente.preferencia → global.
    from configuracion.models import get_config
    cfg = get_config()

    return JsonResponse({
        'id': lista.id,
        'cliente_id': cliente.id,
        'cliente_nombre': cliente.nombre_completo(),
        'cliente_saldo': str(cliente.saldo),
        'cliente_whatsapp': cliente.whatsapp_number or '',
        'cliente_puede_recibir_whatsapp': cliente.puede_recibir_whatsapp,
        'cliente_formato_preferido': cliente.formato_preferido_lista_precios or '',
        'formato_default_global': cfg.formato_default_lista_precios,
        'nombre': lista.nombre,
        'descuento_porcentaje': str(lista.descuento_porcentaje),
        'tipo_ajuste': lista.tipo_ajuste,
        'descuento_motivo': lista.descuento_motivo or '',
        'updated_at': lista.updated_at.isoformat(),
        # Estado del link público — el front lo usa para decidir si
        # muestra "Compartir" o "Mostrar link existente / desactivar".
        'link_activo': lista.link_activo,
        'share_token': str(lista.share_token) if lista.share_token else '',
        'share_expira_at': lista.share_expira_at.isoformat() if lista.share_expira_at else '',
        'share_url': (
            request.build_absolute_uri(
                reverse('lista_precios_publica_web', args=[lista.share_token])
            )
            if lista.link_activo else ''
        ),
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
    raw_rubro = request.GET.get('rubro', '').strip()
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
    rubro_id = _to_int(raw_rubro)
    try:
        page = max(1, int(raw_page))
    except (TypeError, ValueError):
        page = 1

    qs = (
        Articulo.objects.all()
        .select_related('categoria', 'proveedor')
        .order_by('categoria__nombre', 'nombre')
    )
    # Filtro por rubro: incluye TODAS las categorías que pertenecen al
    # rubro elegido. El front además filtra el dropdown de categoría
    # a esas mismas, pero acá enforce-amos en backend igual (defensa
    # en profundidad y soporte para llamadas directas a la API).
    if rubro_id is not None:
        if rubro_id == 0:
            # rubro=0 → artículos en categorías sin rubro asignado.
            qs = qs.filter(categoria__rubro__isnull=True)
        else:
            qs = qs.filter(categoria__rubro_id=rubro_id)
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

    # Modo "devolver solo los IDs de TODOS los matches, sin paginar".
    # Lo usa el botón "Agregar TODOS los N que matchean" del front
    # para poder sumar a la lista sin tener que navegar página por
    # página. Capeamos a 1000 por seguridad — si Osvaldo necesita
    # más que eso en una sola lista, algo está raro y conviene que
    # filtre primero (categoría, búsqueda).
    if request.GET.get('todos') == '1':
        ids = list(qs.values_list('id', flat=True)[:1000])
        return JsonResponse({
            'ids': ids,
            'total': len(ids),
            'capped': len(ids) >= 1000,
        })

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
        errores.append({'campo': 'descuento_porcentaje', 'mensaje': 'El % no es un número válido.'})
    if descuento < 0 or descuento > 100:
        errores.append({'campo': 'descuento_porcentaje', 'mensaje': 'El % debe estar entre 0 y 100.'})

    # tipo_ajuste: 'descuento' (default) o 'aumento'. Validamos contra
    # los choices del modelo para no aceptar valores arbitrarios.
    tipo_ajuste = (payload.get('tipo_ajuste') or 'descuento').strip()
    if tipo_ajuste not in ('descuento', 'aumento'):
        errores.append({'campo': 'tipo_ajuste', 'mensaje': 'tipo_ajuste debe ser "descuento" o "aumento".'})
        tipo_ajuste = 'descuento'

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
            lista.tipo_ajuste = tipo_ajuste
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
                tipo_ajuste=tipo_ajuste,
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
# PDF: helper compartido entre la vista interna (staff) y la pública (token)
# ---------------------------------------------------------------------------
def _render_pdf_lista(lista: ListaPrecios) -> HttpResponse:
    """
    Renderiza el PDF de una lista de precios y devuelve un HttpResponse
    listo para mandar al browser. Lo factorizamos a un helper para
    que la versión interna (`api_pdf_lista`, con staff_member_required)
    y la pública (`vista_pdf_publica`, con token UUID) compartan
    exactamente el mismo render — así nunca se diverge el formato
    entre lo que ve Osvaldo y lo que ve el cliente.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

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
            tipo_ajuste=lista.tipo_ajuste,
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
        # tipo_ajuste decide la etiqueta: "descuento" (resta) o "aumento" (suma).
        etiqueta_ajuste = 'aumento' if lista.tipo_ajuste == 'aumento' else 'descuento'
        txt = f'Precios con {etiqueta_ajuste} del {lista.descuento_porcentaje:g}% aplicado'
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


# ---------------------------------------------------------------------------
# API: detalle de una lista por ID directo (sin cliente_id)
# ---------------------------------------------------------------------------
@staff_member_required
@require_GET
def api_detalle_lista_directo(request: HttpRequest, lista_id: int) -> JsonResponse:
    """
    GET /articulos/api/lista-precios/<lista_id>/detalle-directo/

    Atajo del `api_detalle_lista` cuando el caller solo conoce el
    `lista_id` y no el `cliente_id`. Lo usa la pantalla custom
    cuando viene precargada con `?lista_id=N` (ej. desde el redirect
    del ListaPreciosAdmin), porque ahí no tiene cliente_id a mano.

    Internamente delega a `api_detalle_lista` con el cliente correcto.
    """
    lista = get_object_or_404(ListaPrecios, pk=lista_id)
    return api_detalle_lista(request, cliente_id=lista.cliente_id, lista_id=lista_id)


# ---------------------------------------------------------------------------
# API: PDF de una lista (interno, staff)
# ---------------------------------------------------------------------------
@staff_member_required
@require_GET
def api_pdf_lista(request: HttpRequest, lista_id: int) -> HttpResponse:
    """
    GET /articulos/api/lista-precios/pdf/<lista_id>/

    Versión interna del PDF — protegida por `staff_member_required`.
    El render lo delega al helper compartido (`_render_pdf_lista`) que
    también usa la vista pública por token. Esto evita que ambas
    versiones se desincronicen si en algún momento cambiamos el
    formato del PDF.
    """
    lista = get_object_or_404(
        ListaPrecios.objects.select_related('cliente'),
        pk=lista_id,
    )
    return _render_pdf_lista(lista)


# ---------------------------------------------------------------------------
# Helper: resolver lista por token público o devolver None
# ---------------------------------------------------------------------------
def _lista_por_token_o_none(token) -> ListaPrecios | None:
    """
    Busca la lista por `share_token` y devuelve la instancia si:
      - existe,
      - tiene token no NULL (defensivo: el filtro `share_token=token`
        ya excluye los NULL),
      - NO expiró (o expira_at es NULL = link sin vencimiento).

    Devuelve None en cualquier otro caso para que las vistas
    públicas puedan responder 404 con un template específico (en
    vez de un 404 genérico tipo "Page not found" que el cliente
    no entiende).

    Importante: NO usamos `get_object_or_404` porque queremos
    distinguir "token inexistente" de "token expirado", y eso
    requiere chequear `share_expira_at` después de tener la
    instancia.
    """
    if not token:
        return None
    try:
        lista = (
            ListaPrecios.objects
            .select_related('cliente')
            .get(share_token=token)
        )
    except ListaPrecios.DoesNotExist:
        return None
    if not lista.link_activo:
        return None
    return lista


# ---------------------------------------------------------------------------
# Vista pública: render HTML de la lista (sin login)
# ---------------------------------------------------------------------------
def vista_publica_lista(request: HttpRequest, token) -> HttpResponse:
    """
    GET /p/lista-precios/<uuid:token>/

    Pantalla mobile-first, sin chrome de admin, que el cliente final
    abre desde un link mandado por WhatsApp / email. NO requiere
    login.

    Comportamiento:
      - Token válido y vigente → renderiza la lista con precios.
      - Token NO existe O expiró O fue revocado → render del template
        de "link expirado" con HTTP 404. Usamos 404 (no 410 Gone)
        porque para el cliente el resultado es indistinguible: el
        link no le sirve. Y los crawlers se portan mejor con 404.

    Nunca devolvemos detalle de POR QUÉ falla — un atacante con
    fuerza bruta de UUIDs no se entera de si el token es válido
    pero expiró vs si no existe.
    """
    lista = _lista_por_token_o_none(token)
    if lista is None:
        # Buscamos si existió alguna vez (token presente en DB) para
        # mostrar el mensaje "venció el {fecha}" cuando aplique. Si
        # nunca existió, mostramos el mensaje genérico. Esto le da
        # contexto al cliente sin filtrar info sensible (la fecha de
        # vencimiento es la misma que el operador le compartió).
        venció_at = None
        try:
            referencia = ListaPrecios.objects.get(share_token=token)
            venció_at = referencia.share_expira_at
        except (ListaPrecios.DoesNotExist, ValueError, TypeError):
            pass
        return render(
            request,
            'articulo/lista_precios_publica_expirado.html',
            {'venció_at': venció_at},
            status=404,
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

    # Pre-calculamos los precios efectivos para no llamar la función
    # desde el template (Django templates no admiten kwargs).
    items_render = []
    for it in items:
        articulo = it.articulo
        precio = precio_efectivo(
            articulo, cliente,
            descuento_lista=lista.descuento_porcentaje,
            precios_pactados_map=pactados,
            tipo_ajuste=lista.tipo_ajuste,
        )
        items_render.append({
            'codigo': articulo.codigo or articulo.codigo_interno or '',
            'nombre': articulo.nombre,
            'marca': articulo.marca or '',
            'nota': it.nota or '',
            'precio': precio,
            'pactado': pactados.get(articulo.id) is not None,
        })

    return render(
        request,
        'articulo/lista_precios_publica.html',
        {
            'lista': lista,
            'cliente': cliente,
            'items': items_render,
            # Hardcodeado por ahora — cuando tengamos varios negocios
            # esto vendrá de ConfiguracionGeneral o de un settings.
            'negocio_nombre': getattr(settings, 'NEGOCIO_NOMBRE', 'Golosinas Insa'),
        },
    )


# ---------------------------------------------------------------------------
# Vista pública: PDF (sin login)
# ---------------------------------------------------------------------------
def vista_pdf_publica(request: HttpRequest, token) -> HttpResponse:
    """
    GET /p/lista-precios/<uuid:token>/pdf/

    Misma validación de token que `vista_publica_lista`. Reusa el
    helper `_render_pdf_lista` para que el PDF público sea idéntico
    al interno.

    Si el token no es válido (no existe / expiró), devolvemos un
    HTML 404 (no un PDF 404), porque el cliente probablemente está
    bajando esto desde WhatsApp y un texto explicativo le sirve
    más que un PDF vacío.
    """
    lista = _lista_por_token_o_none(token)
    if lista is None:
        return render(
            request,
            'articulo/lista_precios_publica_expirado.html',
            {'venció_at': None},
            status=404,
        )
    return _render_pdf_lista(lista)


# ---------------------------------------------------------------------------
# API: compartir lista (generar/renovar link público)
# ---------------------------------------------------------------------------
@staff_member_required
@require_POST
def api_compartir_lista(request: HttpRequest, lista_id: int) -> JsonResponse:
    """
    POST /articulos/api/lista-precios/<id>/compartir/

    Body opcional: `{"dias": 14}` (override del default de config).

    Genera o renueva el link público. Devuelve la URL absoluta para
    que el front la copie al portapapeles sin tener que armarla.
    """
    lista = get_object_or_404(ListaPrecios, pk=lista_id)

    dias = None
    if request.body:
        try:
            payload = json.loads(request.body.decode('utf-8'))
            if isinstance(payload, dict) and 'dias' in payload and payload['dias'] is not None:
                dias = int(payload['dias'])
                if dias < 0:
                    return JsonResponse(
                        {'ok': False, 'errores': [{'mensaje': 'dias debe ser >= 0.'}]},
                        status=400,
                    )
        except (ValueError, TypeError):
            # JSON inválido o `dias` no es entero: ignoramos y usamos
            # el default. No es worth devolver un 400 — el flujo común
            # es "POST sin body" desde el botón.
            dias = None

    info = lista.compartir(dias=dias)
    share_url = request.build_absolute_uri(
        reverse('lista_precios_publica_web', args=[info['share_token']])
    )
    return JsonResponse({
        'ok': True,
        'share_token': str(info['share_token']),
        'share_url': share_url,
        'expira_at': info['share_expira_at'].isoformat() if info['share_expira_at'] else '',
    })


# ---------------------------------------------------------------------------
# API: desactivar link público
# ---------------------------------------------------------------------------
@staff_member_required
@require_POST
def api_desactivar_link_lista(request: HttpRequest, lista_id: int) -> JsonResponse:
    """
    POST /articulos/api/lista-precios/<id>/desactivar-link/

    Revoca el link público. Idempotente: si ya estaba revocado,
    devuelve `{ok: true}` igual.
    """
    lista = get_object_or_404(ListaPrecios, pk=lista_id)
    lista.desactivar_link()
    return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
# Pantalla de difusión manual
# ---------------------------------------------------------------------------
@staff_member_required
def lista_precios_difundir(request: HttpRequest, lista_id: int) -> HttpResponse:
    """
    Pantalla `/articulos/lista-precios/<id>/difundir/`.

    Sirve para mandar el LINK de una lista de precios a varios
    clientes uno por uno via wa.me (sin pasar por el wa-bot).

    Pre-requisitos:
      - La lista tiene que tener `share_token` activo. Si no, no hay
        link que mandar — redirigimos al editor con un mensaje claro.

    El template carga los clientes vía la API `..difundir/clientes/`.
    """
    lista = get_object_or_404(
        ListaPrecios.objects.select_related('cliente'),
        pk=lista_id,
    )
    # No hace falta link activo para entrar a la pantalla — el modo
    # "texto" (lista en el body del mensaje) no necesita link.
    # Si el operador después elige link/pdf/ambos y el link no está
    # activo, la API de envío lo genera automáticamente (o el front lo
    # avisa). Mantenemos el share_url vacío si no hay link y el front
    # decide qué hacer.
    share_url = ''
    if lista.link_activo and lista.share_token:
        share_url = request.build_absolute_uri(
            reverse('lista_precios_publica_web', args=[lista.share_token])
        )
    contexto = {
        'lista': lista,
        'share_url': share_url,
        'expira_at_iso': lista.share_expira_at.isoformat() if lista.share_expira_at else '',
    }
    return render(request, 'articulo/lista_precios_difundir.html', contexto)


@staff_member_required
@require_GET
def api_lista_precios_difundir_clientes(request: HttpRequest, lista_id: int) -> JsonResponse:
    """
    GET /articulos/api/lista-precios/<id>/difundir/clientes/

    Devuelve la audiencia para difundir: clientes con whatsapp_number
    cargado. Filtros opcionales:
      - q: busca por nombre/apellido
      - solo_compraron_ultimos_dias: filtro por actividad reciente
      - solo_con_saldo_a_favor / solo_con_saldo_deudor
      - solo_puede_recibir_whatsapp (default true, respeta el opt-in)

    Respuesta:
      {clientes: [{id, nombre, whatsapp_number, puede_recibir_whatsapp,
                   saldo, ultima_compra}]}

    Devuelve TODOS los clientes que matchean (sin paginación) porque
    el operador típicamente quiere ver la lista entera para decidir
    a quién enviar. Si crece mucho (>500), agregamos paginación.
    """
    get_object_or_404(ListaPrecios, pk=lista_id)  # valida que existe

    from cliente.models import Cliente
    from django.db.models import Sum, Max, Q
    from datetime import timedelta
    from django.utils import timezone as tz

    qs = Cliente.objects.exclude(whatsapp_number='').exclude(whatsapp_number=None)

    # Por default respetamos el opt-in (`puede_recibir_whatsapp=True`),
    # pero permitimos override por query param para casos puntuales
    # (ej. mandarle una lista a un cliente que no quiso campañas de
    # promo pero sí su lista personal).
    respetar_optin = request.GET.get('solo_puede_recibir_whatsapp', '1') == '1'
    if respetar_optin:
        qs = qs.filter(puede_recibir_whatsapp=True)

    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(apellido__icontains=q))

    try:
        dias = int(request.GET.get('solo_compraron_ultimos_dias') or '0')
    except ValueError:
        dias = 0
    if dias > 0:
        desde = tz.now().date() - timedelta(days=dias)
        qs = qs.filter(ventas__fecha_compra__gte=desde).distinct()

    a_favor = request.GET.get('solo_con_saldo_a_favor') == '1'
    deudor = request.GET.get('solo_con_saldo_deudor') == '1'
    if a_favor or deudor:
        qs = qs.annotate(saldo_calc=Sum('cuenta__movimientos__monto'))
        if a_favor:
            qs = qs.filter(saldo_calc__gt=0)
        if deudor:
            qs = qs.filter(saldo_calc__lt=0)

    qs = qs.annotate(
        ultima_compra=Max('ventas__fecha_compra'),
        saldo_total=Sum('cuenta__movimientos__monto'),
    ).order_by('nombre', 'apellido')

    clientes = [
        {
            'id': c.id,
            'nombre': c.nombre_completo(),
            'whatsapp_number': c.whatsapp_number,
            'puede_recibir_whatsapp': c.puede_recibir_whatsapp,
            'saldo': str(c.saldo_total or 0),
            'ultima_compra': c.ultima_compra.isoformat() if c.ultima_compra else None,
            # Preferencia de formato per-cliente. La UI la muestra como
            # un mini-badge para que el operador sepa qué modo aplicará
            # a este destinatario por default.
            'formato_preferido': c.formato_preferido_lista_precios or '',
        }
        for c in qs
    ]

    # Default global del modo, para que la UI lo muestre como
    # pre-selección del selector "Modo de envío".
    from configuracion.models import get_config
    cfg = get_config()
    return JsonResponse({
        'clientes': clientes,
        'total': len(clientes),
        'formato_default_global': cfg.formato_default_lista_precios,
    })


# ---------------------------------------------------------------------------
# Difundir v2: envío automático vía wa-bot (no wa.me manual)
# ---------------------------------------------------------------------------
import logging  # noqa: E402  (importado acá para los warnings de fallback)


@staff_member_required
@require_POST
def api_lista_precios_difundir_enviar(request: HttpRequest, lista_id: int) -> JsonResponse:
    """
    POST /articulos/api/lista-precios/<id>/difundir/enviar/

    Body:
      {
        "cliente_ids": [1, 2, 3, ...],
        "modo_override": "" | "link" | "pdf" | "ambos",
        "forzar": false
      }

    Crea N `DifusionListaPreciosEnvio` pendientes (resolviendo modo en
    cascada per-cliente) y encola la task `procesar_difusion(lista_id)`
    en django-q2. El worker manda uno por uno con rate limit.
    """
    lista = get_object_or_404(
        ListaPrecios.objects.select_related('cliente'),
        pk=lista_id,
    )

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido.'}, status=400)

    cliente_ids = payload.get('cliente_ids') or []
    if not isinstance(cliente_ids, list) or not cliente_ids:
        return JsonResponse(
            {'ok': False, 'error': 'cliente_ids debe ser una lista no vacía.'},
            status=400,
        )
    cliente_ids = [int(x) for x in cliente_ids if str(x).isdigit()]

    modo_override = (payload.get('modo_override') or '').strip()
    if modo_override not in ('', 'link', 'pdf', 'ambos'):
        return JsonResponse(
            {'ok': False, 'error': 'modo_override inválido.'},
            status=400,
        )

    # Bloqueo defensivo: si hay envíos pendientes recientes (worker
    # procesando), avisamos al front para que no encole en paralelo.
    # El usuario igual puede forzar mandando otra vez con forzar=true.
    from .models import DifusionListaPreciosEnvio
    pendientes_actuales = DifusionListaPreciosEnvio.objects.filter(
        lista=lista,
        status__in=(
            DifusionListaPreciosEnvio.STATUS_PENDIENTE,
            DifusionListaPreciosEnvio.STATUS_ENVIANDO,
        ),
    ).count()
    if pendientes_actuales > 0 and not payload.get('forzar'):
        return JsonResponse({
            'ok': False,
            'error': (
                f'Hay {pendientes_actuales} envíos en curso para esta lista. '
                f'Esperá a que terminen o reintentá con "forzar".'
            ),
            'pendientes_actuales': pendientes_actuales,
        }, status=409)

    from .tasks_difusion import crear_envios_pendientes_difusion
    encolados = crear_envios_pendientes_difusion(
        lista, cliente_ids, modo_override, request.user,
    )

    if encolados == 0:
        return JsonResponse({
            'ok': False,
            'error': 'Ningún cliente válido (sin whatsapp_number).',
            'encolados': 0,
        }, status=400)

    # Encolar el worker. Si django-q2 no está disponible (raro), caemos
    # a ejecución sincrónica (bloquea la request pero al menos manda).
    try:
        from django_q.tasks import async_task
        async_task('articulo.tasks_difusion.procesar_difusion', lista.id)
    except Exception as exc:
        log = logging.getLogger(__name__)
        log.warning('async_task no disponible, ejecuto inline: %s', exc)
        from .tasks_difusion import procesar_difusion
        procesar_difusion(lista.id)

    return JsonResponse({
        'ok': True,
        'encolados': encolados,
        'mensaje': (
            f'Se encolaron {encolados} envíos. La barra de progreso '
            f'muestra el avance.'
        ),
    })


@staff_member_required
@require_GET
def api_lista_precios_difundir_progreso(request: HttpRequest, lista_id: int) -> JsonResponse:
    """
    GET /articulos/api/lista-precios/<id>/difundir/progreso/

    Devuelve un snapshot del estado de los envíos de esta lista. La UI
    lo polea cada 2s mientras hay pendientes para actualizar la barra
    de progreso en vivo.
    """
    get_object_or_404(ListaPrecios, pk=lista_id)
    from .models import DifusionListaPreciosEnvio
    from django.db.models import Count

    counts_qs = (
        DifusionListaPreciosEnvio.objects
        .filter(lista_id=lista_id)
        .values('status')
        .annotate(n=Count('id'))
    )
    counts = {row['status']: row['n'] for row in counts_qs}

    recientes_qs = (
        DifusionListaPreciosEnvio.objects
        .filter(lista_id=lista_id)
        .select_related('cliente')
        .order_by('-created_at')[:50]
    )
    recientes = [{
        'cliente_id': e.cliente_id,
        'cliente_nombre': e.cliente.nombre_completo(),
        'modo': e.modo,
        'status': e.status,
        'error_msg': e.error_msg[:140] if e.error_msg else '',
        'sent_at': e.sent_at.isoformat() if e.sent_at else None,
    } for e in recientes_qs]

    return JsonResponse({
        'total': sum(counts.values()),
        'pendientes': counts.get('pendiente', 0),
        'enviando': counts.get('enviando', 0),
        'enviados': counts.get('enviado', 0),
        'fallidos': counts.get('fallido', 0),
        'recientes': recientes,
    })
