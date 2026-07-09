"""
Informe diario por vendedor.

Endpoint disparado por dos bulk actions del PedidoAdmin:

  - Tamaño A4 (portrait, márgenes fijos, layout estándar).
  - Tamaño según `FacturaConfiguration` (mismo papel/fuente que los
    comprobantes individuales que ya se imprimen).

El operador filtra pedidos por vendedor + fecha en el admin, los
selecciona con checkbox y dispara una de las dos acciones. El PDF
sale con:

  - Cabecera: Vendedor + Fecha + cantidad de pedidos + total del día.
  - Tabla-planilla con una fila por pedido y columnas configurables
    (ver `ConfiguracionGeneral.informe_diario_incluir_*`).
  - Pie: totales agregados (pagados vs pendientes).

Los pedidos se agrupan por vendedor. Si el operador seleccionó
pedidos de vendedores distintos (caso raro pero legal), el PDF tiene
una sección por vendedor con salto de página entre ellos — así el
informe sigue siendo útil "por vendedor" aunque el filtro haya sido
laxo.

Estilo: mismos colores/fuentes que `factura_config` para que la
planilla se vea coherente con los comprobantes individuales.
"""
from collections import defaultdict
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from configuracion.models import get_config
from factura_config.models import FacturaConfiguration

from .models import Pedido


TAMANO_A4 = 'a4'
TAMANO_CONFIG = 'config'


def _parse_pedido_ids(request):
    raw = request.GET.get('pedidos_ids', '').strip()
    if not raw:
        return []
    return [int(x) for x in raw.split(',') if x.strip().isdigit()]


def _pedidos_por_vendedor(pedidos):
    """Agrupa pedidos por vendedor preservando el orden por fecha_compra."""
    grupos = defaultdict(list)
    for p in pedidos:
        grupos[p.venta.vendedor_id].append(p)
    return grupos


def _resolver_tamano(request):
    """A4 fijo o el que sale de FacturaConfiguration."""
    tamano = request.GET.get('tamano', TAMANO_A4)
    if tamano == TAMANO_CONFIG:
        cfg = FacturaConfiguration.objects.first() or FacturaConfiguration()
        # `page_width` viene en cm; asumimos hoja portrait A4-ish.
        # Si el cliente cambió el ancho a algo tipo "10cm" (comanda),
        # respetamos eso; alto siempre 29.7 (A4 real, la lib no expone
        # page_height en el modelo actual).
        return (cfg.page_width * cm, 29.7 * cm), cfg
    return A4, FacturaConfiguration.objects.first() or FacturaConfiguration()


def _color(valor, default='#000000'):
    """
    Los colores en `FacturaConfiguration` a veces vienen como nombre
    ('black', 'grey') y otras como hex ('#111827'). `toColor` maneja
    ambos; `HexColor` no — por eso no la usamos.
    """
    try:
        return colors.toColor(valor or default)
    except Exception:
        return colors.toColor(default)


def _build_styles(cfg, page_width):
    """
    Estilos de párrafo del informe. Se ajustan al ancho de página:
    en papel angosto (< 15 cm — típico de comanda) usamos fuentes
    más chicas para evitar que el título rompa la caja del PDF.
    """
    styles = getSampleStyleSheet()
    base_content = cfg.font_size_content or 9
    base_header = cfg.font_size_header or 9

    # Trigger de "papel angosto": bajo 15 cm asumimos comanda-ish y
    # reducimos el header 2 puntos, así entra sin overflow.
    angosto = page_width < (15 * cm)
    header_size = base_header + (2 if angosto else 4)
    meta_size = base_content
    if angosto:
        meta_size = max(7, base_content - 1)

    body = ParagraphStyle(
        'body',
        parent=styles['Normal'],
        fontName=cfg.content_font or 'Helvetica',
        fontSize=base_content,
        textColor=_color(cfg.content_color, '#000000'),
        leading=base_content + 2,
    )
    # Header CENTRADO — así el título queda alineado en el eje del
    # papel independientemente del ancho. Antes iba left-align y
    # cuando el vendedor tenía nombre largo desbordaba en comanda.
    header = ParagraphStyle(
        'header',
        parent=styles['Heading2'],
        fontName=cfg.header_font or 'Helvetica-Bold',
        fontSize=header_size,
        textColor=_color(cfg.header_color, '#111827'),
        alignment=TA_CENTER,
        spaceAfter=4,
        leading=header_size + 2,
    )
    # Sub-header (vendedor) más chico, también centrado.
    subheader = ParagraphStyle(
        'subheader',
        parent=header,
        fontSize=max(9, header_size - 3),
        spaceAfter=2,
    )
    # Meta (fecha, cantidad, total del día) centrado y más chico.
    meta = ParagraphStyle(
        'meta',
        parent=body,
        fontSize=meta_size,
        alignment=TA_CENTER,
        leading=meta_size + 2,
    )
    total = ParagraphStyle(
        'total',
        parent=styles['Normal'],
        fontName=cfg.total_font or 'Helvetica-Bold',
        fontSize=cfg.font_size_total or 12,
        textColor=_color(cfg.header_color, '#111827'),
    )
    # Estilo compacto para la lista de artículos en celda de tabla:
    # sale como bullet-list con line-height apretado.
    articulos = ParagraphStyle(
        'articulos',
        parent=body,
        fontSize=max(7, base_content - 1),
        leading=max(9, base_content + 1),
    )
    # Estilo dedicado para la fila de encabezado de la tabla. Un
    # Paragraph dentro de una celda IGNORA el TEXTCOLOR del TableStyle
    # y usa el textColor propio — por eso necesitamos un estilo con
    # texto blanco explícito, sino queda negro sobre negro cuando el
    # header_color de la FacturaConfiguration es oscuro.
    header_cell = ParagraphStyle(
        'header_cell',
        parent=body,
        fontName=cfg.header_font or 'Helvetica-Bold',
        textColor=colors.white,
    )
    return body, header, subheader, meta, total, articulos, header_cell


def _fila_pedido(pedido, cfg_flags, estilos):
    body, _, _, _, _, articulos_style, _ = estilos
    fila = []
    if cfg_flags['cliente']:
        fila.append(Paragraph(
            pedido.venta.cliente.nombre_completo() if pedido.venta.cliente else '—',
            body,
        ))
    if cfg_flags['direccion']:
        direccion = getattr(pedido.venta.cliente, 'direccion', '') or '—'
        fila.append(Paragraph(direccion, body))
    if cfg_flags['articulos']:
        items = pedido.venta.ventas.all()
        if items:
            texto = '<br/>'.join(
                f'• {it.cantidad} × {it.articulo.nombre}'
                for it in items
                if it.articulo
            )
        else:
            texto = '—'
        fila.append(Paragraph(texto, articulos_style))
    if cfg_flags['total']:
        total = Decimal(str(pedido.venta.precio_total or 0))
        fila.append(Paragraph(f'${total:,.2f}', body))
    if cfg_flags['cobro']:
        marca = '✔ Pagado' if pedido.pagado else '● Pendiente'
        fila.append(Paragraph(marca, body))
    return fila


def _columnas(cfg_flags):
    cols = []
    if cfg_flags['cliente']:
        cols.append(('Cliente', 4.5 * cm))
    if cfg_flags['direccion']:
        cols.append(('Dirección', 4.0 * cm))
    if cfg_flags['articulos']:
        cols.append(('Artículos', 7.0 * cm))
    if cfg_flags['total']:
        cols.append(('Total', 2.0 * cm))
    if cfg_flags['cobro']:
        cols.append(('Estado', 2.2 * cm))
    return cols


def _seccion_vendedor(pedidos, cfg_flags, estilos, cfg_pdf, titulo):
    """Devuelve los flowables (elements) de la sección de UN vendedor."""
    body, header, subheader, meta, _, _, header_cell = estilos
    vendedor = pedidos[0].venta.vendedor
    fecha = pedidos[0].venta.fecha_compra

    header_color = _color(cfg_pdf.header_color, '#111827')
    border = _color(cfg_pdf.table_border_color, '#cbd5e1')

    els = []
    # Título centrado (configurable desde ConfiguracionGeneral).
    els.append(Paragraph(titulo, header))
    # Vendedor abajo del título, más chico, también centrado — evita
    # que un nombre largo desborde cuando el título ya es grande.
    els.append(Paragraph(
        vendedor.display_name() if vendedor else '—',
        subheader,
    ))
    # Meta: fecha + cantidad + total del día, todo centrado.
    els.append(Paragraph(
        f'Fecha: {fecha} · Pedidos: {len(pedidos)}',
        meta,
    ))

    pagados = [p for p in pedidos if p.pagado]
    pendientes = [p for p in pedidos if not p.pagado]
    total_pagado = sum(Decimal(str(p.venta.precio_total or 0)) for p in pagados)
    total_pendiente = sum(Decimal(str(p.venta.precio_total or 0)) for p in pendientes)

    if cfg_flags['total_dia']:
        total_dia = sum(Decimal(str(p.venta.precio_total or 0)) for p in pedidos)
        els.append(Paragraph(
            f'Total del día: <b>${total_dia:,.2f}</b>',
            meta,
        ))
    # Línea de subtotales de cobro — INDEPENDIENTE de la columna
    # "Estado" de la tabla, controlada por su propio flag.
    if cfg_flags['totales_cobro']:
        els.append(Paragraph(
            f'Cobrado: ${total_pagado:,.2f} · Pendiente: ${total_pendiente:,.2f}',
            meta,
        ))
    els.append(Spacer(1, 0.4 * cm))

    columnas = _columnas(cfg_flags)
    # Uso `header_cell` (texto blanco) — no `body` — porque el
    # BACKGROUND del header row es oscuro y con body el texto
    # quedaba negro sobre negro (invisible).
    headers_row = [Paragraph(f'<b>{col_titulo}</b>', header_cell) for col_titulo, _ in columnas]
    widths = [w for _, w in columnas]
    rows = [headers_row]
    for p in pedidos:
        rows.append(_fila_pedido(p, cfg_flags, estilos))

    tabla = Table(rows, colWidths=widths, repeatRows=1)
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), header_color),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), cfg_pdf.table_border_width or 0.5, border),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    els.append(tabla)
    return els


@login_required
def generar_informe_diario_vendedor(request):
    """
    Renderiza el informe diario por vendedor a PDF.

    Query params:
      pedidos_ids: CSV de IDs de Pedido.
      tamano:      'a4' o 'config' (default: 'a4').
    """
    pedido_ids = _parse_pedido_ids(request)
    if not pedido_ids:
        return HttpResponseBadRequest('Faltan pedidos_ids en la query string.')

    pedidos = list(
        Pedido.objects
        .filter(id__in=pedido_ids)
        .select_related('venta', 'venta__cliente', 'venta__vendedor')
        .prefetch_related('venta__ventas__articulo')
        .order_by('venta__vendedor__usuario__username', 'venta__fecha_compra', 'id')
    )
    if not pedidos:
        return HttpResponseBadRequest('No se encontraron pedidos con esos IDs.')

    config = get_config()
    cfg_flags = {
        'cliente':       config.informe_diario_incluir_cliente,
        'direccion':     config.informe_diario_incluir_direccion,
        'articulos':     config.informe_diario_incluir_articulos,
        'total':         config.informe_diario_incluir_total,
        'cobro':         config.informe_diario_incluir_cobro,
        'totales_cobro': config.informe_diario_incluir_totales_cobro,
        'total_dia':     config.informe_diario_incluir_total_dia,
    }
    # Guardar contra config vacía en las COLUMNAS (todo apagado). El
    # operador podría haber desactivado todas las columnas por accidente
    # — dejamos al menos cliente+total. `totales_cobro` es de la cabecera,
    # queda como esté.
    if not any(cfg_flags[k] for k in ('cliente', 'direccion', 'articulos', 'total', 'cobro')):
        cfg_flags['cliente'] = True
        cfg_flags['total'] = True

    pagesize, cfg_pdf = _resolver_tamano(request)
    estilos = _build_styles(cfg_pdf, pagesize[0])
    titulo = config.informe_diario_titulo or 'Informe diario por vendedor'

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        topMargin=(cfg_pdf.margin_top or 0.8) * cm,
        bottomMargin=(cfg_pdf.margin_bottom or 0.8) * cm,
        leftMargin=(cfg_pdf.margin_left or 1) * cm,
        rightMargin=(cfg_pdf.margin_right or 1) * cm,
        title='Informe diario por vendedor',
    )

    elements = []
    grupos = _pedidos_por_vendedor(pedidos)
    vendedores_ordenados = sorted(
        grupos.keys(),
        key=lambda vid: grupos[vid][0].venta.vendedor.usuario.username if grupos[vid][0].venta.vendedor else '',
    )
    for i, vendedor_id in enumerate(vendedores_ordenados):
        if i > 0:
            elements.append(PageBreak())
        elements.extend(_seccion_vendedor(
            grupos[vendedor_id], cfg_flags, estilos, cfg_pdf, titulo,
        ))

    doc.build(elements)

    pdf = buffer.getvalue()
    buffer.close()

    resp = HttpResponse(pdf, content_type='application/pdf')
    tamano_label = request.GET.get('tamano', TAMANO_A4)
    resp['Content-Disposition'] = (
        f'inline; filename="informe_diario_{tamano_label}.pdf"'
    )
    return resp
