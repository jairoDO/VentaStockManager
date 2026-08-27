from django.shortcuts import render, redirect
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

from venta.models import Venta, ArticuloVenta, Pedido
from articulo.models import Articulo
from vendedor.models import Vendedor
from venta.forms import PedidoEstadoForm
from django.contrib.auth.decorators import login_required
from dal import autocomplete
from django.db import models
from django.shortcuts import render, get_object_or_404
from reportlab.lib.pagesizes import letter
from django.http import HttpResponse
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, PageBreak, Paragraph, Frame, PageTemplate, HRFlowable
from reportlab.pdfbase.pdfmetrics import stringWidth

from io import BytesIO
from reportlab.lib import colors
from django.urls import reverse_lazy
from django.views.generic.edit import CreateView, UpdateView
from factura_config.models import FacturaConfiguration

def custom_404_view(request, exception):
    return render(request, '404.html', status=404)
from django.utils import timezone
from datetime import timedelta

class ArticuloAutocomplete(autocomplete.Select2QuerySetView):
    def get_queryset(self):
        if not self.request.user.is_authenticated:  
            return Articulo.objects.none()
        if self.q:
            articulos = Articulo.objects.filter(
                models.Q(nombre__icontains=self.q) |
                models.Q(codigo__icontains=self.q) |
                models.Q(codigo_interno__icontains=self.q)
            )
        else:
            articulos = Articulo.objects.all()
        return articulos
    
    # def get_result_label(self, item):
    #     # Define cómo se mostrará cada opción en el menú desplegable
    #     return f"{item.articulo}"
    
    # def create_option(self, term, valor, articulo):
    #     # Crea una opción personalizada para el término especificado
    #     return {
    #         'id': valor,
    #         'text': term,
    #         'nombre': articulo.nombre,
    #         'codigo': articulo.codigo,
    #         'precio_mayorista': articulo.precio_mayorista,
    #         'precio_minorista': articulo.precio_minorista,
    #     }
# Create your views here.
def venta_detalle(request, venta_id):
    """ esta vista toma in venta_id, busca la base de datos  y rendariza el template de la venta
    
    """
    # buscamos la venta por el id
    venta = Venta.objects.get(id=venta_id)
    context = {'venta': venta, 'titulo_de_pagina' : 'detalle de venta'}

    # rendarizamo el temaplate
    return render(request, 'venta_detalle.html', context)

def ventas_por_vendedor(request, id_vendedor):
    # Recupera el vendedor por su ID
    vendedor = get_object_or_404(Vendedor, pk=id_vendedor)
    
    # Recupera las ventas asociadas al vendedor
    ventas = Venta.objects.filter(vendedor=vendedor)
    total_ventas = sum(venta.total for venta in ventas)

    
    # Puedes hacer más procesamiento aquí si es necesario
    context = {
        'vendedor': vendedor,
        'ventas': ventas,
        'total_ventas': total_ventas,
        'titulo_de_pagina': 'Ventas'
    }
    return render(request, 'ventas_por_vendedor.html', context)



from django.http import HttpResponseRedirect
from django.urls import reverse
from django.contrib.admin.views.decorators import staff_member_required

@staff_member_required
def redirect_to_ventas(_):
    return HttpResponseRedirect(reverse('admin:venta_venta_changelist'))

@login_required
def ventas_recientes_por_vendedor(request, id_vendedor):
    # Recupera el vendedor por su ID
    vendedor = Vendedor.objects.get(pk=id_vendedor)
    
    # Establece la fecha de hace 7 días
    fecha_inicio = timezone.now() - timedelta(days=7)
    
    # Recupera las ventas asociadas al vendedor en los últimos 7 días
    ventas_recientes = Venta.objects.filter(vendedor=vendedor, fecha_compra__gte=fecha_inicio)
    
    # Calcula el total de ventas
    total_ventas = sum(venta.total for venta in ventas_recientes)
    
    # Renderiza el template con el contexto de las ventas recientes por vendedor
    context = {
        'vendedor': vendedor,
        'ventas': ventas_recientes,
        'total_ventas': total_ventas,
        'titulo_de_pagina': 'Ventas Recientes por Vendedor'
    }
    return render(request, 'ventas_por_vendedor.html', context)

def ventas_mensual_por_vendedor(request, id_vendedor):
    # Recupera el vendedor por su ID
    vendedor = Vendedor.objects.get(pk=id_vendedor)
    
    # Establece la fecha de hace 7 días
    fecha_inicio = timezone.now() - timedelta(days=30)
    
    # Recupera las ventas asociadas al vendedor en los últimos 7 días
    ventas_mensual = Venta.objects.filter(vendedor=vendedor, fecha_compra__gte=fecha_inicio)
    
    # Calcula el total de ventas
    total_ventas = sum(venta.total for venta in ventas_mensual)
    
    # Renderiza el template con el contexto de las ventas recientes por vendedor
    context = {
        'vendedor': vendedor,
        'ventas': ventas_mensual,
        'total_ventas': total_ventas,
        'titulo_de_pagina': 'Ventas Mensual por Vendedor'
    }
    return render(request, 'ventas_por_vendedor.html', context)
@login_required
def calcular_ganancia_articulos(request):
    # Obtener todos los artículos
    articulos = Articulo.objects.all()

    # Crear un diccionario para almacenar la ganancia de cada artículo
    ganancia_por_articulo = {}

    # Iterar sobre cada artículo
    for articulo in articulos:
        # Obtener la información de compra del artículo
        precio_compra = articulo.precio_compra

        # Obtener todas las ventas de este artículo
        ventas = ArticuloVenta.objects.filter(articulo=articulo)

        # Calcular la ganancia total por este artículo
        ganancia_total = sum(venta.articulo.precio_venta - precio_compra for venta in ventas)

        # Guardar la ganancia total en el diccionario
        ganancia_por_articulo[articulo.nombre] = ganancia_total

    # Pasar el diccionario de ganancias a la plantilla
    context = {'ganancia_por_articulo': ganancia_por_articulo}
    return render(request, 'ganancia_por_articulos.html', context)



def comprovante_de_venta(request, venta_id):
    venta = Venta.objects.get(id=venta_id)
    cliente = venta.cliente  # Ajusta este campo según la estructura real de tu modelo Venta
    vendedor = venta.vendedor.fullname()# Ajusta este campo según la estructura real de tu modelo Venta
    articulos_venta = ArticuloVenta.objects.filter(venta=venta)  # Filtrar los artículos vendidos asociados a esta venta

    return render(request, 'comprovante_de_venta.html', {'venta': venta, 'cliente': cliente, 'vendedor': vendedor, 'articulos_venta': articulos_venta})


def ver_pedido(request, pedido_id):
    pedido = Pedido.objects.get(id=pedido_id)
    cliente = pedido.venta.cliente  # Ajusta este campo según la estructura real de tu modelo Venta
    vendedor = pedido.venta.vendedor.fullname()# Ajusta este campo según la estructura real de tu modelo Venta
    articulos_venta =  pedido.venta.articulos_vendidos.all()  # Filtrar los artículos vendidos asociados a esta venta
    if request.method == 'POST':
        form = PedidoEstadoForm(request.POST)
        if form.is_valid():
            pedido.pagado =  form.cleaned_data['pagado']
            pedido.estado = form.cleaned_data['estado']
            pedido.save()
              
            return redirect('admin:venta_pedido_changelist')
    
    return render(request, 'ver_pedido.html', {
        'venta': pedido.venta,
        'cliente': cliente, 
        'vendedor': vendedor, 
        'articulos_venta': articulos_venta,
        'pedido': pedido,
        'form': PedidoEstadoForm(initial={"estado": pedido.estado, 'pagado':pedido.pagado})})
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, PageTemplate, Frame, Spacer, PageBreak
from io import BytesIO
from django.http import HttpResponse

def generar_pdf_pedidos_(request, pedido_ids=None):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os
    if not pedido_ids:
        pedido_ids = request.GET.get('pedidos_ids', '').split(',')
    
    # Ensure pedido_ids is not empty
    if not pedido_ids or pedido_ids == ['']:
        return HttpResponse("No pedido IDs provided.", status=400)
    
    pedidos = Pedido.objects.filter(id__in=pedido_ids)

    # Get configuration
    config = FacturaConfiguration.objects.first()
    if not config:
        config = FacturaConfiguration()
    
    # Register custom font if provided
    custom_font_name = None
    if config.custom_font:
        font_path = config.custom_font.path
        font_name = os.path.splitext(os.path.basename(font_path))[0]
        try:
            pdfmetrics.registerFont(TTFont(font_name, font_path))
            custom_font_name = font_name
        except:
            pass  # Fallback to default font if registration fails

    # Convert color strings to reportlab colors
    color_map = {
        'black': colors.black,
        'blue': colors.blue,
        'red': colors.red,
        'green': colors.green,
        'gray': colors.gray,
    }

    header_color = color_map.get(config.header_color, colors.black)
    content_color = color_map.get(config.content_color, colors.black)
    border_color = color_map.get(config.table_border_color, colors.black)

    buffer = BytesIO()
    page_size = (config.page_width * cm, config.page_height * cm)
    pdf = SimpleDocTemplate(
        buffer, 
        pagesize=page_size,
        topMargin=config.margin_top * cm,
        bottomMargin=config.margin_bottom * cm,
        leftMargin=config.margin_left * cm,
        rightMargin=config.margin_right * cm
    )
    
    elements = []
    styles = getSampleStyleSheet()
    frame_width = (page_size[0] - (config.margin_left + config.margin_right) * cm) / 2
    frame_height = (page_size[1] - (config.margin_top + config.margin_bottom) * cm) / 2
    frames = []
    padding = 1 * mm

    # Define frames
    for i in range(2):
        for j in range(2):
            frames.append(Frame(
                config.margin_left * cm + i * frame_width,
                page_size[1] - config.margin_top * cm - (j + 1) * frame_height,
                frame_width - 10 * mm,
                frame_height - 10 * mm,
                id=f'frame_{i}_{j}'
            ))

    pdf.addPageTemplates([PageTemplate(id='FourFrames', frames=frames)])
    quarter_count = 0

    for pedido in pedidos:
        elements.append(HRFlowable(width="100%", thickness=config.table_border_width, color=border_color))
        elements.append(Spacer(1, padding))

        # Cliente info styling
        styleN = styles['Normal']
        styleN.fontSize = config.font_size_content
        styleN.fontName = custom_font_name or config.content_font
        styleN.textColor = content_color

        # Información del cliente
        cliente_info = f"Cliente: {pedido.venta.cliente.nombre_completo()}"
        direccion = f" Dirección: {pedido.venta.cliente.direccion} "
        fecha_compra = f"Fecha de Compra: {pedido.venta.fecha_compra.strftime('%Y-%m-%d')} "
        fecha_entrega = f"Fecha de Entrega: {pedido.venta.fecha_entrega.strftime('%Y-%m-%d')}\n"
        
        for info in [cliente_info, direccion, fecha_compra, fecha_entrega]:
            elements.append(Paragraph(info, styleN))
            elements.append(Spacer(1, padding))

        # Import del helper que respeta descuentos.
        from venta.utils import subtotal_linea, total_venta as calcular_total_venta

        data_articulos = [['Artículo', '#', 'Precio', 'Subtotal']]

        for articulo_venta in pedido.venta.ventas.all():
            nombre_articulo = articulo_venta.articulo.get_articulo_short_name()
            # Si hay descuento por línea, lo anotamos al lado del nombre.
            # En este PDF no agregamos columna nueva para no romper el
            # ancho fijo — mejor anotar inline con "(-X%)".
            desc_linea = articulo_venta.descuento_porcentaje or 0
            if desc_linea > 0:
                nombre_articulo = f'{nombre_articulo} (-{desc_linea:g}%)'

            nombre_articulo_corto = ""
            max_width = config.column_width_article * cm  # Maximum width in points
            font_name = config.content_font
            font_size = config.font_size_content

            # Split the article name into words
            words = nombre_articulo.split()
            current_line = ""

            for word in words:
                # Calculate the width of the current line with the new word
                line_with_word = f"{current_line} {word}".strip()
                line_width = stringWidth(line_with_word, font_name, font_size)

                if line_width <= max_width:
                    # If the line with the new word fits, add the word to the current line
                    current_line = line_with_word
                else:
                    # If the line with the new word doesn't fit, add the current line to the result
                    nombre_articulo_corto += current_line + "\n"
                    current_line = word  # Start a new line with the current word

            # Add the last line to the result
            nombre_articulo_corto += current_line

            data_articulos.append([
                nombre_articulo_corto,
                articulo_venta.cantidad,
                articulo_venta.precio,
                # Subtotal con descuento de línea aplicado, no el
                # bruto que mostraba antes (era el bug).
                f'{subtotal_linea(articulo_venta):.2f}',
            ])

        while len(data_articulos) < 13:
            data_articulos.append(['', '', '', ''])

        # Tabla de artículos con nuevas configuraciones
        tabla_articulos = Table(data_articulos, colWidths=[
            config.column_width_article * cm,
            config.column_width_quantity * cm,
            config.column_width_price * cm,
            config.column_width_total * cm
        ])

        content_style = TableStyle([
            ('GRID', (0, 0), (-1, -1), config.table_border_width, border_color),
            ('FONTNAME', (0, 0), (-1, -1), custom_font_name or config.content_font),
            ('FONTSIZE', (0, 0), (-1, -1), config.font_size_content),
            ('TEXTCOLOR', (0, 0), (-1, -1), content_color),
        ])
        tabla_articulos.setStyle(content_style)

        # Tabla del total. El total ahora respeta descuentos (línea
        # y global). Si hay descuento global, mostramos también el
        # subtotal y el monto del descuento para que sea claro.
        # Si además se aplicó saldo a favor del cliente, lo restamos
        # como línea extra y mostramos "Total a pagar" abajo.
        from venta.utils import subtotal_venta_sin_desc_global
        from cliente.models import MovimientoCuenta
        total_final = calcular_total_venta(pedido.venta)
        desc_global = pedido.venta.descuento_porcentaje or 0

        # Saldo a favor aplicado en esta venta (si lo hubo). Lo busco
        # como MovimientoCuenta(tipo='aplicacion_saldo', venta=esta).
        # El monto está en NEGATIVO (resta saldo de la cuenta), por eso
        # uso abs() para mostrarlo como número positivo en el PDF.
        saldo_aplicado = MovimientoCuenta.objects.filter(
            venta=pedido.venta,
            tipo=MovimientoCuenta.TIPO_APLICACION_SALDO,
        ).aggregate(total=models.Sum('monto'))['total'] or 0
        saldo_aplicado_abs = abs(saldo_aplicado)

        data_total = []
        if desc_global > 0:
            subtotal_bruto = subtotal_venta_sin_desc_global(pedido.venta)
            data_total.append(['Subtotal:', '', '', f'{subtotal_bruto:.2f}'])
            data_total.append([f'Descuento {desc_global:g}%:', '', '', f'-{(subtotal_bruto - total_final):.2f}'])
            data_total.append(['Total venta:', '', '', f'{total_final:.2f}'])
        else:
            data_total.append(['Total venta:', '', '', f'{total_final:.2f}'])

        if saldo_aplicado_abs > 0:
            # Mostramos la línea de saldo aplicado + total a pagar.
            data_total.append(['Saldo a favor aplicado:', '', '', f'-{saldo_aplicado_abs:.2f}'])
            data_total.append(['Total a pagar:', '', '', f'{(total_final - saldo_aplicado_abs):.2f}'])
        tabla_total = Table(data_total, colWidths=[
            config.column_width_article * cm,
            config.column_width_quantity * cm,
            config.column_width_price * cm,
            config.column_width_total * cm
        ])
        
        total_style = TableStyle([
            ('GRID', (0, 0), (-1, -1), config.table_border_width, border_color),
            ('FONTNAME', (0, 0), (-1, -1), custom_font_name or config.header_font),
            ('FONTSIZE', (0, 0), (-1, -1), config.font_size_total),
            ('TEXTCOLOR', (0, 0), (-1, -1), header_color),
            ('ALIGN', (3, 0), (3, 0), 'RIGHT'),
        ])
        tabla_total.setStyle(total_style)

        elements.append(tabla_articulos)
        elements.append(tabla_total)
        elements.append(Spacer(1, padding))
        
        quarter_count += 1
        
        if quarter_count % 4 == 0:
            elements.append(PageBreak())
            quarter_count = 0

    pdf.build(elements)

    pdf_buffer = buffer.getvalue()
    buffer.close()
    response = HttpResponse(pdf_buffer, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="pedidos.pdf"'
    return response

def split_text_to_fit_width(text, max_width, font_name, font_size):
    """Splits text into lines that fit within the specified width."""
    words = text.split()
    current_line = ""
    result = ""

    for word in words:
        line_with_word = f"{current_line} {word}".strip()
        line_width = stringWidth(line_with_word, font_name, font_size)

        if line_width <= max_width:
            current_line = line_with_word
        else:
            result += current_line + "\n"
            current_line = word

    result += current_line
    return result
# the older
def generar_pdf_pedidos(request, pedido_ids=None):
    from reportlab.lib.pagesizes import landscape
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet
    from io import BytesIO
    from django.http import HttpResponse
    from .models import Pedido
    if pedido_ids is None:
        pedido_ids = request.GET.get('pedidos_ids').split(',')
        
    
    buffer = BytesIO()
    elements = []
    styles = getSampleStyleSheet()
    padding = 0.5 * cm
    compact_padding = 0.18 * cm
    pedidos_count = len(pedido_ids)
    cantidad_articulos = []
    alturas_pedidos = []
    config = FacturaConfiguration.objects.first()
    if not config:
        config = FacturaConfiguration()
    for index, pedido_id in enumerate(pedido_ids):
        inicio_elementos_pedido = len(elements)
        pedido = Pedido.objects.get(id=pedido_id)
        cantidad_articulos.append(pedido.venta.ventas.count())
        # Vendedor: usamos `display_name()` para mostrar
        # "username (nombre apellido)". Igual lógica que el listado
        # del PedidoAdmin (consistencia garantizada). El username es
        # la fuente de verdad para el operador; el nombre real entre
        # paréntesis es para que el cliente identifique a la persona.
        vendedor = pedido.venta.vendedor
        nombre_vendedor = vendedor.display_name() if vendedor else '-'

        data_cliente = [
            ['Compra:', pedido.venta.fecha_compra],
            ['Entrega:', pedido.venta.fecha_entrega],
            ['Dirección:', pedido.venta.cliente.direccion],
            ['Cliente:', pedido.venta.cliente.nombre_completo()],
            ['Vendedor:', nombre_vendedor],
        ]

        # Tabla del cliente
        tabla_cliente = Table(data_cliente, colWidths=[4* cm, 4 * cm, 4 * cm,  4 * cm])
        estilo_tabla_cliente = TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, config.header_color),
            ('FONTNAME', (0, 0), (-1, -1), config.header_font),
            ('FONTSIZE', (0, 0), (-1, -1), config.font_size_header)
        ])
        tabla_cliente.setStyle(estilo_tabla_cliente)

        # Tabla de artículos. Para detectar precios pactados, hacemos
        # un solo query por cliente con todos los artículos de esta
        # venta (evita N+1 si la venta tiene varios items).
        from cliente.models import PrecioCliente
        articulos_venta = list(pedido.venta.ventas.select_related('articulo').all())
        if articulos_venta and pedido.venta.cliente_id:
            pactados_ids = {
                p.articulo_id: p for p in PrecioCliente.objects.filter(
                    cliente_id=pedido.venta.cliente_id,
                    articulo_id__in=[av.articulo_id for av in articulos_venta],
                )
            }
        else:
            pactados_ids = {}

        # Decidimos si mostrar la columna "Desc %" mirando si ALGUNA
        # línea tiene descuento. Si nadie usa descuento, ahorramos
        # ancho de columna y queda más limpio (compatible con
        # PDFs históricos donde no había descuentos).
        from venta.utils import (
            subtotal_linea,
            subtotal_venta_sin_desc_global,
            total_venta as calcular_total_venta,
        )
        hay_descuentos_linea = any(
            (av.descuento_porcentaje or 0) > 0 for av in articulos_venta
        )
        descuento_global = pedido.venta.descuento_porcentaje or 0
        motivo_descuento = (pedido.venta.descuento_motivo or '').strip()

        # Header dinámico según si hay descuentos por línea.
        if hay_descuentos_linea:
            data_articulos = [['Articulos', 'Cant', 'Precio/U', 'Desc%', 'Subtotal']]
        else:
            data_articulos = [['Articulos', 'Cant', 'Precio/U', 'Subtotal']]
        doble_space = 0
        for articulo_venta in articulos_venta:
            nombre_articulo = articulo_venta.articulo.get_articulo_short_name()
            # Si hay precio pactado, lo marcamos con un asterisco al
            # lado del nombre. Usamos asterisco y no emoji porque la
            # fuente default de reportlab (Helvetica) no incluye glifos
            # Unicode altos y los renderiza como cuadraditos. El cliente
            # sabe que "(*)" significa precio acordado por la nota al
            # pie que agregamos abajo.
            if articulo_venta.articulo_id in pactados_ids:
                nombre_articulo = f'(*) {nombre_articulo}'
            nombre_articulo_corto = split_text_to_fit_width(
                nombre_articulo,
                config.column_width_article * cm,
                config.content_font,
                config.font_size_content
            )

            precio_text = str(articulo_venta.precio)
            precio_corto = split_text_to_fit_width(
                precio_text,
                config.column_width_price * cm,
                config.content_font,
                config.font_size_content
            )

            # El subtotal ahora respeta el descuento por línea.
            # Antes mostraba `articulo_venta.total` que era
            # cantidad × precio sin descuento — ese era el bug.
            subtotal_text = f'{subtotal_linea(articulo_venta):.2f}'
            subtotal_corto = split_text_to_fit_width(
                subtotal_text,
                config.column_width_total * cm,
                config.content_font,
                config.font_size_content
            )

            fila = [
                nombre_articulo_corto,
                articulo_venta.cantidad,
                precio_corto,
            ]
            if hay_descuentos_linea:
                desc_pct = articulo_venta.descuento_porcentaje or 0
                # Mostramos el % solo si > 0, sino quedan filas con
                # "0%" que distraen visualmente.
                fila.append(f'{desc_pct:g}%' if desc_pct else '')
            fila.append(subtotal_corto)
            data_articulos.append(fila)

        col_widths = [
            config.column_width_article * cm,
            config.column_width_price * cm,
            config.column_width_price * cm,
        ]
        if hay_descuentos_linea:
            # Columna de % chica, no necesita el ancho de precio.
            col_widths.append(1.5 * cm)
        col_widths.append(config.column_width_total * cm)
        tabla_articulos = Table(data_articulos, colWidths=col_widths)

        estilo_tabla_articulos = TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, config.table_border_color),
            ('FONTNAME', (0, 0), (-1, -1), config.content_font),
            ('FONTSIZE', (0, 0), (-1, -1), config.font_size_content)
        ])
        tabla_articulos.setStyle(estilo_tabla_articulos)

        # Control interno para depósito/reparto. La casilla y el campo de
        # observaciones se imprimen vacíos para completar a mano antes de
        # que el pedido salga. Usamos celdas dibujadas (en vez del glifo
        # Unicode de checkbox) para que funcione con cualquier fuente.
        ancho_comprobante = sum(col_widths)
        tabla_control = Table(
            [
                ['', 'PEDIDO CONTROLADO'],
                ['Observaciones del control:', ''],
                ['', ''],
            ],
            colWidths=[0.55 * cm, ancho_comprobante - 0.55 * cm],
            rowHeights=[0.50 * cm, 0.40 * cm, 0.65 * cm],
        )
        tabla_control.setStyle(TableStyle([
            ('BOX', (0, 0), (0, 0), 1, config.content_color),
            ('SPAN', (0, 1), (1, 1)),
            ('SPAN', (0, 2), (1, 2)),
            ('BOX', (0, 1), (1, 2), 1, config.content_color),
            ('FONTNAME', (0, 0), (-1, -1), config.content_font),
            ('FONTSIZE', (0, 0), (-1, -1), config.font_size_content),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (1, 0), (1, 0), 5),
            ('LEFTPADDING', (0, 1), (1, 2), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))

        # Tabla del total. Si hay descuento global, mostramos 3 líneas
        # (subtotal, descuento, total). Si además hubo saldo a favor
        # del cliente aplicado a esta venta, se muestra como línea
        # negativa + "Total a pagar" abajo.
        from cliente.models import MovimientoCuenta
        total_final = calcular_total_venta(pedido.venta)
        saldo_aplicado = MovimientoCuenta.objects.filter(
            venta=pedido.venta,
            tipo=MovimientoCuenta.TIPO_APLICACION_SALDO,
        ).aggregate(total=models.Sum('monto'))['total'] or 0
        saldo_aplicado_abs = abs(saldo_aplicado)

        data_total = []
        if descuento_global > 0:
            subtotal_bruto = subtotal_venta_sin_desc_global(pedido.venta)
            data_total.append(['Subtotal:', f'{subtotal_bruto:.2f}'])
            data_total.append([f'Descuento {descuento_global:g}%:', f'-{(subtotal_bruto - total_final):.2f}'])
            data_total.append(['Total venta:', f'{total_final:.2f}'])
        else:
            data_total.append(['Total venta:', f'{total_final:.2f}'])
        if saldo_aplicado_abs > 0:
            data_total.append(['Saldo a favor aplicado:', f'-{saldo_aplicado_abs:.2f}'])
            data_total.append(['Total a pagar:', f'{(total_final - saldo_aplicado_abs):.2f}'])
        tabla_total = Table(data_total, colWidths=[5 * cm, 3 * cm])
        estilo_tabla_total = TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, config.content_color),
            ('FONTNAME', (0, 0), (-1, -1), config.content_font),
            ('FONTSIZE', (0, 0), (-1, -1), config.font_size_total),
            # ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ])
        tabla_total.setStyle(estilo_tabla_total)

        # Añadir tablas a los elementos
        elements.append(tabla_control)
        elements.append(Spacer(1, compact_padding))
        elements.append(tabla_cliente)
        elements.append(Spacer(1, compact_padding))
        elements.append(tabla_articulos)
        elements.append(Spacer(1, compact_padding))
        elements.append(tabla_total)
        # Motivo del descuento global (opcional, solo si fue cargado).
        if descuento_global > 0 and motivo_descuento:
            from reportlab.platypus import Paragraph
            from reportlab.lib.styles import ParagraphStyle
            motivo_style = ParagraphStyle(
                name='motivo_desc',
                fontName=config.content_font,
                fontSize=max(6, config.font_size_content - 2),
                textColor=config.content_color,
            )
            elements.append(Spacer(1, padding / 4))
            elements.append(Paragraph(
                f'Descuento aplicado: {motivo_descuento}',
                motivo_style,
            ))
        # Si alguna línea es de precio pactado, agregamos una nota
        # al pie para que el cliente entienda qué significa el (*).
        if pactados_ids:
            from reportlab.platypus import Paragraph
            from reportlab.lib.styles import ParagraphStyle
            nota_style = ParagraphStyle(
                name='nota_pactado',
                fontName=config.content_font,
                fontSize=max(6, config.font_size_content - 2),
                textColor=config.content_color,
            )
            elements.append(Spacer(1, padding / 2))
            elements.append(Paragraph(
                '(*) Artículo con precio pactado con el cliente.',
                nota_style,
            ))

        # Recepción del cliente. Va al final de cada comprobante para que
        # pueda marcar conformidad o dejar asentado cualquier faltante,
        # diferencia o rotura al momento de recibir el pedido.
        tabla_conformidad = Table(
            [
                ['', 'CLIENTE CONFORME CON LO RECIBIDO'],
                ['Firma o aclaración:', ''],
                ['', ''],
            ],
            colWidths=[0.55 * cm, ancho_comprobante - 0.55 * cm],
            rowHeights=[0.50 * cm, 0.40 * cm, 0.75 * cm],
        )
        tabla_conformidad.setStyle(TableStyle([
            ('BOX', (0, 0), (0, 0), 1, config.content_color),
            ('FONTNAME', (0, 0), (-1, -1), config.content_font),
            ('FONTSIZE', (0, 0), (-1, -1), config.font_size_content),
            ('SPAN', (0, 1), (1, 1)),
            ('SPAN', (0, 2), (1, 2)),
            ('BOX', (0, 1), (1, 2), 1, config.content_color),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (1, 0), (1, 0), 5),
            ('LEFTPADDING', (0, 1), (1, 2), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
        elements.append(Spacer(1, compact_padding))
        elements.append(tabla_conformidad)

        # La altura de la hoja debe depender del contenido real. Contar la
        # cantidad de flowables agrandaba mucho la factura al sumar controles
        # y separadores, aunque visualmente ocuparan pocos milímetros.
        ancho_disponible = (
            config.page_width * cm - config.margin_bottom - config.margin_right
        )
        altura_pedido = 0
        for elemento in elements[inicio_elementos_pedido:]:
            _, altura = elemento.wrap(ancho_disponible, 1000 * cm)
            altura_pedido += altura
        alturas_pedidos.append(altura_pedido)

        if index < len(pedido_ids) - 1:
            elements.append(PageBreak())
        

    # Dejamos un pequeño margen de seguridad para el frame de ReportLab, sin
    # convertirlo en una franja vacía visible al final del comprobante.
    page_height = max(8 * cm, max(alturas_pedidos) + 0.65 * cm)
    page_size = (config.page_width * cm, page_height)

    # Set margins to zero
    pdf = SimpleDocTemplate(
        buffer, pagesize=page_size,
        topMargin=config.margin_top,
        bottomMargin=config.margin_bottom,
        leftMargin=config.margin_bottom,
        rightMargin=config.margin_right)

    # page_height = ((max(cantidad_articulos ) * 1.6* cm) + 1*cm + (80 if max(cantidad_articulos) < 4 else 5))
    
    # pdf = SimpleDocTemplate(buffer, pagesize=(8 * cm, page_height), topMargin=0 * cm, bottomMargin=0 * cm)

    pdf.build(elements)

    pdf_buffer = buffer.getvalue()
    buffer.close()
    from datetime import datetime
    fecha_del_dia = str(datetime.now().date())
    response = HttpResponse(pdf_buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="pedidos_{fecha_del_dia}.pdf"'
    return response

def add_quarter_page(canvas, doc):
    canvas.saveState()
    canvas.translate(0, -doc.height)
    canvas.restoreState()
    
def generar_pdf_pedido(request, pedido_id):
    return generar_pdf_pedidos(request, [pedido_id])

from django.views.generic.edit import CreateView, UpdateView
from cliente.models import Cliente

class ClienteCreateView(CreateView):
    model = Cliente
    fields = ['nombre', 'direccion', 'telefono']
    success_url = reverse_lazy('cliente_list')

class ClienteUpdateView(UpdateView):
    model = Cliente
    fields = ['nombre', 'direccion', 'telefono']
    success_url = reverse_lazy('cliente_list')
