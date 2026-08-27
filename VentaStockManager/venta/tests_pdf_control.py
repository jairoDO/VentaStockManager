from datetime import date, timedelta

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
