from datetime import date, timedelta
import re

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from articulo.models import Articulo
from cliente.models import Cliente
from vendedor.models import Vendedor
from venta.models import ArticuloVenta, Venta
from venta.views import generar_pdf_pedidos


class PedidoPDFControlTests(TestCase):

    def setUp(self):
        usuario = get_user_model().objects.create_user('vendedor_pdf')
        vendedor = Vendedor.objects.create(
            usuario=usuario,
            nombre='Venta',
            apellido='Prueba',
        )
        cliente = Cliente.objects.create(
            nombre='Cliente',
            apellido='Prueba',
            direccion='Calle 123',
        )
        articulo = Articulo.objects.create(
            nombre='Producto de prueba',
            stock=10,
            precio_minorista='1500.00',
            precio_mayorista='1300.00',
            vencimiento=date.today() + timedelta(days=90),
        )
        venta = Venta.objects.create(
            fecha_compra=date.today(),
            fecha_entrega=date.today() + timedelta(days=1),
            cliente=cliente,
            vendedor=vendedor,
        )
        ArticuloVenta.objects.create(
            venta=venta,
            articulo=articulo,
            cantidad=2,
            precio='1500.00',
        )
        self.pedido = venta.pedido

    def test_pdf_incluye_controles_de_pedido_y_recepcion(self):
        request = RequestFactory().get('/venta/pedido/generar-pdf/')

        response = generar_pdf_pedidos(request, [self.pedido.pk])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertGreater(len(response.content), 1000)

    def test_pedido_largo_se_divide_en_paginas_en_vez_de_cortarse(self):
        # Con 55 productos el comprobante supera ampliamente el largo de una
        # hoja térmica. Debe continuar en otra página, no crecer como una única
        # página que algunos drivers recortan.
        for numero in range(55):
            articulo = Articulo.objects.create(
                nombre=f'Producto largo numero {numero:02d}',
                stock=100,
                precio_minorista='100.00',
                precio_mayorista='90.00',
                vencimiento=date.today() + timedelta(days=90),
            )
            ArticuloVenta.objects.create(
                venta=self.pedido.venta,
                articulo=articulo,
                cantidad=1,
                precio='100.00',
            )

        request = RequestFactory().get('/venta/pedido/generar-pdf/')
        response = generar_pdf_pedidos(request, [self.pedido.pk])

        # Los diccionarios de página de ReportLab conservan `/Type /Page` sin
        # comprimir. Excluimos `/Pages` con el límite de palabra del regex.
        paginas = len(re.findall(rb'/Type\s*/Page\b', response.content))
        self.assertGreaterEqual(paginas, 2)

    def test_pedido_largo_puede_generarse_como_una_tira_continua(self):
        for numero in range(55):
            articulo = Articulo.objects.create(
                nombre=f'Producto continuo numero {numero:02d}',
                stock=100,
                precio_minorista='100.00',
                precio_mayorista='90.00',
                vencimiento=date.today() + timedelta(days=90),
            )
            ArticuloVenta.objects.create(
                venta=self.pedido.venta,
                articulo=articulo,
                cantidad=1,
                precio='100.00',
            )

        request = RequestFactory().get(
            '/venta/pedido/generar-pdf/?formato=continuo'
        )
        response = generar_pdf_pedidos(request, [self.pedido.pk])

        paginas = len(re.findall(rb'/Type\s*/Page\b', response.content))
        self.assertEqual(paginas, 1)
