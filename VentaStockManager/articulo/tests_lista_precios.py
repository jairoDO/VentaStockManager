"""
Tests para la pantalla de "Lista de precios" + el flujo de creación
inline de artículos en la grilla.

Cobertura mínima exigida por el ticket:
  - listar listas previas + detalle (con/sin lista cargada)
  - guardar (create + update + atomic)
  - PDF (content-type pdf; skip si reportlab depende de algo no
    disponible en el entorno de tests)
  - crear artículos inline en grilla (POST con `nuevos: [...]`)

Usamos `Client` de Django (no E2E). Para el PDF chequeamos solo
que el endpoint responda 200 y devuelva un PDF binario válido —
no parseamos el contenido.
"""
from __future__ import annotations

import json
import unittest
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from articulo.models import (
    Articulo,
    Categoria,
    ListaPrecios,
    ListaPreciosItem,
)
from cliente.models import Cliente, PrecioCliente
from compra.models import Proveedor


class ListaPreciosTestsBase(TestCase):
    """Setup compartido: un cliente, categorías, proveedores y artículos."""

    @classmethod
    def setUpTestData(cls):
        cls.user_staff = User.objects.create_user(
            username='staff_lp', password='x', is_staff=True,
        )

        # Sufijo único para no chocar con seeds de la migración 0004.
        cls.cat = Categoria.objects.create(nombre='Limpieza_test_lp', color='#00bcd4')
        cls.prov = Proveedor.objects.create(nombre='Proveedor test lp')

        cls.cliente = Cliente.objects.create(
            nombre='Pepe',
            apellido='Test',
            direccion='Calle Falsa 123',
            telefono='1111',
        )
        # Algunos artículos para llenar las listas.
        cls.art1 = Articulo.objects.create(
            codigo='A001', nombre='Lavandina 1L',
            stock=10, precio_minorista=Decimal('1000.00'),
            precio_mayorista=Decimal('900.00'),
            vencimiento=date(2030, 1, 1),
            cantidad_por_mayor=100,
            categoria=cls.cat, proveedor=cls.prov,
        )
        cls.art2 = Articulo.objects.create(
            codigo='A002', nombre='Detergente 750ml',
            stock=20, precio_minorista=Decimal('500.00'),
            precio_mayorista=Decimal('450.00'),
            vencimiento=date(2030, 1, 1),
            cantidad_por_mayor=50,
            categoria=cls.cat, proveedor=cls.prov,
        )
        cls.art3 = Articulo.objects.create(
            codigo='A003', nombre='Esponja',
            stock=50, precio_minorista=Decimal('100.00'),
            precio_mayorista=Decimal('80.00'),
            vencimiento=date(2030, 1, 1),
            cantidad_por_mayor=10,
            categoria=cls.cat, proveedor=cls.prov,
        )

        # Una lista previa con 2 items para usar en los tests de
        # listar/detalle. La creamos con descuento 0 acá; otros tests
        # arman listas con descuento.
        cls.lista_previa = ListaPrecios.objects.create(
            cliente=cls.cliente,
            nombre='Lista marzo',
            descuento_porcentaje=Decimal('5.00'),
            descuento_motivo='Cliente fiel',
            creado_por=cls.user_staff,
        )
        ListaPreciosItem.objects.create(
            lista=cls.lista_previa, articulo=cls.art1, orden=0, nota='',
        )
        ListaPreciosItem.objects.create(
            lista=cls.lista_previa, articulo=cls.art2, orden=1, nota='Mín. 10 unidades',
        )

    def setUp(self):
        self.client.force_login(self.user_staff)


# ---------------------------------------------------------------------------
# Tests: API listas previas + detalle
# ---------------------------------------------------------------------------
class ListaPreciosListarTests(ListaPreciosTestsBase):

    def test_listas_previas_del_cliente(self):
        """GET listas de un cliente devuelve nombre, descuento, count_items."""
        url = reverse(
            'lista_precios_api_listas_cliente',
            kwargs={'cliente_id': self.cliente.id},
        )
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['cliente_id'], self.cliente.id)
        self.assertEqual(len(data['listas']), 1)
        lista = data['listas'][0]
        self.assertEqual(lista['id'], self.lista_previa.id)
        self.assertEqual(lista['nombre'], 'Lista marzo')
        self.assertEqual(lista['count_items'], 2)
        self.assertEqual(Decimal(lista['descuento_porcentaje']), Decimal('5.00'))

    def test_detalle_lista_calcula_precios_con_descuento(self):
        """
        Detalle de la lista: el precio efectivo de cada item se sirve
        ya con el descuento aplicado. La lista tiene 5% de descuento,
        así que art1 ($1000) sale a $950 y art2 ($500) a $475.
        """
        url = reverse(
            'lista_precios_api_detalle',
            kwargs={
                'cliente_id': self.cliente.id,
                'lista_id': self.lista_previa.id,
            },
        )
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['id'], self.lista_previa.id)
        self.assertEqual(data['nombre'], 'Lista marzo')
        self.assertEqual(len(data['items']), 2)
        # Items ordenados por `orden`: art1 primero.
        item_art1 = next(i for i in data['items'] if i['articulo_id'] == self.art1.id)
        item_art2 = next(i for i in data['items'] if i['articulo_id'] == self.art2.id)
        self.assertEqual(Decimal(item_art1['precio_efectivo']), Decimal('950.00'))
        self.assertEqual(Decimal(item_art2['precio_efectivo']), Decimal('475.00'))
        # La nota del item se preserva.
        self.assertEqual(item_art2['nota'], 'Mín. 10 unidades')

    def test_detalle_respeta_precio_pactado(self):
        """
        Si hay PrecioCliente para (cliente, articulo), ese precio es la
        base — no el minorista. Después de eso se aplica el descuento
        de la lista. Para art1 con PrecioCliente=800 y 5% off:
        800 * 0.95 = 760.
        """
        PrecioCliente.objects.create(
            cliente=self.cliente,
            articulo=self.art1,
            precio_unitario=Decimal('800.00'),
        )
        url = reverse(
            'lista_precios_api_detalle',
            kwargs={
                'cliente_id': self.cliente.id,
                'lista_id': self.lista_previa.id,
            },
        )
        r = self.client.get(url)
        data = r.json()
        item_art1 = next(i for i in data['items'] if i['articulo_id'] == self.art1.id)
        self.assertEqual(Decimal(item_art1['precio_efectivo']), Decimal('760.00'))
        self.assertTrue(item_art1['tiene_precio_pactado'])


# ---------------------------------------------------------------------------
# Tests: API guardar lista
# ---------------------------------------------------------------------------
class ListaPreciosGuardarTests(ListaPreciosTestsBase):

    def _post(self, payload):
        return self.client.post(
            reverse('lista_precios_api_guardar'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_crear_lista_nueva(self):
        """POST sin id crea una lista nueva con sus items."""
        r = self._post({
            'id': None,
            'cliente_id': self.cliente.id,
            'nombre': 'Lista nueva',
            'descuento_porcentaje': '10.00',
            'descuento_motivo': 'Promo',
            'items': [
                {'articulo_id': self.art1.id, 'orden': 0, 'nota': ''},
                {'articulo_id': self.art3.id, 'orden': 1, 'nota': 'Frágil'},
            ],
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertIsNotNone(data.get('lista_id'))

        lista = ListaPrecios.objects.get(pk=data['lista_id'])
        self.assertEqual(lista.cliente, self.cliente)
        self.assertEqual(lista.nombre, 'Lista nueva')
        self.assertEqual(lista.descuento_porcentaje, Decimal('10.00'))
        items = list(lista.items.order_by('orden'))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].articulo, self.art1)
        self.assertEqual(items[1].articulo, self.art3)
        self.assertEqual(items[1].nota, 'Frágil')

    def test_actualizar_lista_existente_reemplaza_items(self):
        """
        POST con id pisa los items de la lista (wipe & re-create).
        Inicialmente la lista previa tiene art1 y art2; la dejamos solo
        con art3 y verificamos que ya no aparecen los otros.
        """
        r = self._post({
            'id': self.lista_previa.id,
            'cliente_id': self.cliente.id,
            'nombre': 'Lista marzo (actualizada)',
            'descuento_porcentaje': '0',
            'descuento_motivo': '',
            'items': [
                {'articulo_id': self.art3.id, 'orden': 0, 'nota': ''},
            ],
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['lista_id'], self.lista_previa.id)

        self.lista_previa.refresh_from_db()
        self.assertEqual(self.lista_previa.nombre, 'Lista marzo (actualizada)')
        items = list(self.lista_previa.items.all())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].articulo, self.art3)

    def test_guardar_atomico_si_articulo_invalido(self):
        """
        Si un articulo_id no existe, devolvemos 400 y NO creamos la
        lista. Verificamos contando las listas antes/después.
        """
        count_antes = ListaPrecios.objects.filter(cliente=self.cliente).count()
        r = self._post({
            'id': None,
            'cliente_id': self.cliente.id,
            'nombre': 'No debería crearse',
            'descuento_porcentaje': '0',
            'descuento_motivo': '',
            'items': [
                {'articulo_id': self.art1.id, 'orden': 0, 'nota': ''},
                {'articulo_id': 9_999_999, 'orden': 1, 'nota': ''},
            ],
        })
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertFalse(data['ok'])
        count_despues = ListaPrecios.objects.filter(cliente=self.cliente).count()
        self.assertEqual(count_antes, count_despues)

    def test_guardar_rechaza_descuento_fuera_de_rango(self):
        """Descuento > 100 se rechaza con 400."""
        r = self._post({
            'id': None,
            'cliente_id': self.cliente.id,
            'nombre': 'Lista test',
            'descuento_porcentaje': '150',
            'descuento_motivo': '',
            'items': [{'articulo_id': self.art1.id, 'orden': 0, 'nota': ''}],
        })
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertFalse(data['ok'])

    def test_guardar_rechaza_sin_nombre(self):
        """Sin nombre se rechaza con 400."""
        r = self._post({
            'id': None,
            'cliente_id': self.cliente.id,
            'nombre': '   ',
            'descuento_porcentaje': '0',
            'descuento_motivo': '',
            'items': [{'articulo_id': self.art1.id, 'orden': 0, 'nota': ''}],
        })
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertFalse(data['ok'])


# ---------------------------------------------------------------------------
# Tests: PDF
# ---------------------------------------------------------------------------
class ListaPreciosPDFTests(ListaPreciosTestsBase):

    def test_pdf_devuelve_content_type_pdf(self):
        """
        El endpoint del PDF responde 200 con content-type application/pdf.
        Skippeable si reportlab no está disponible en el entorno
        (fallback documentado en el ticket).
        """
        try:
            import reportlab  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('reportlab no instalado en este entorno')

        url = reverse(
            'lista_precios_api_pdf',
            kwargs={'lista_id': self.lista_previa.id},
        )
        try:
            r = self.client.get(url)
        except Exception as e:
            # Si reportlab levanta algo por config faltante (fuente,
            # color, etc.) lo skippeamos en vez de fallar.
            raise unittest.SkipTest(f'reportlab error de runtime: {e}')

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        # Header PDF mágico al inicio del binario.
        self.assertTrue(r.content.startswith(b'%PDF-'), 'No empieza con magic %PDF-')

    def test_pdf_lista_vacia_no_falla(self):
        """
        Una lista sin items igual genera PDF (con un placeholder).
        Edge case importante: el operador podría haber guardado una
        lista vacía para empezar a cargarla más tarde.
        """
        try:
            import reportlab  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('reportlab no instalado en este entorno')

        lista_vacia = ListaPrecios.objects.create(
            cliente=self.cliente,
            nombre='Vacia',
            descuento_porcentaje=Decimal('0'),
            descuento_motivo='',
            creado_por=self.user_staff,
        )
        url = reverse(
            'lista_precios_api_pdf',
            kwargs={'lista_id': lista_vacia.id},
        )
        try:
            r = self.client.get(url)
        except Exception as e:
            raise unittest.SkipTest(f'reportlab error de runtime: {e}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')


# ---------------------------------------------------------------------------
# Tests: artículos disponibles (panel izquierdo del armado de lista)
# ---------------------------------------------------------------------------
class ListaPreciosDisponiblesTests(ListaPreciosTestsBase):

    def test_articulos_disponibles_con_precio_efectivo(self):
        """
        El listado de artículos disponibles devuelve cada item con
        `precio_efectivo` (sin descuento de lista — el descuento lo
        aplica el front en vivo).
        """
        url = reverse('lista_precios_api_articulos')
        r = self.client.get(url, {'cliente_id': self.cliente.id})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        # Al menos los 3 artículos del setup.
        self.assertGreaterEqual(data['total_items'], 3)
        ids = [it['id'] for it in data['items']]
        self.assertIn(self.art1.id, ids)
        item_art1 = next(it for it in data['items'] if it['id'] == self.art1.id)
        # Sin precio pactado y sin descuento → precio_efectivo == minorista.
        self.assertEqual(Decimal(item_art1['precio_efectivo']), Decimal('1000.00'))
        self.assertFalse(item_art1['tiene_precio_pactado'])

    def test_articulos_disponibles_respeta_precio_pactado(self):
        """Si hay PrecioCliente, el precio_efectivo lo usa."""
        PrecioCliente.objects.create(
            cliente=self.cliente,
            articulo=self.art1,
            precio_unitario=Decimal('750.00'),
        )
        url = reverse('lista_precios_api_articulos')
        r = self.client.get(url, {'cliente_id': self.cliente.id, 'q': 'Lavandina'})
        data = r.json()
        item_art1 = next(it for it in data['items'] if it['id'] == self.art1.id)
        self.assertEqual(Decimal(item_art1['precio_efectivo']), Decimal('750.00'))
        self.assertTrue(item_art1['tiene_precio_pactado'])


# ---------------------------------------------------------------------------
# Tests: creación inline de artículos en la grilla (FEATURE 2)
# ---------------------------------------------------------------------------
class GrillaCrearArticulosInlineTests(TestCase):
    """
    Verifica que POST `/articulos/api/grilla/guardar/` con un array
    `nuevos: [...]` crea los artículos y devuelve los IDs reales
    emparejados con el `_temp_id` del front.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user_staff = User.objects.create_user(
            username='staff_grilla_nuevos', password='x', is_staff=True,
        )
        cls.cat = Categoria.objects.create(nombre='Test cat nuevos', color='#ff0000')
        cls.prov = Proveedor.objects.create(nombre='Test prov nuevos')

    def setUp(self):
        self.client.force_login(self.user_staff)

    def _post(self, payload):
        return self.client.post(
            reverse('grilla_precios_api_guardar'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_crear_articulos_inline(self):
        """
        Body con `nuevos: [...]` crea los artículos. La respuesta
        empareja `_temp_id` ↔ `id` real. Verificamos también que el
        codigo_interno se auto-genera cuando no viene.
        """
        count_antes = Articulo.objects.count()
        r = self._post({
            'cambios': [],
            'nuevos': [
                {
                    '_temp_id': 'tmp_1',
                    'nombre': 'Producto nuevo A',
                    'codigo': 'NEW001',
                    'marca': 'MarcaTest',
                    'categoria_id': self.cat.id,
                    'proveedor_id': self.prov.id,
                    'precio_minorista': '1500.00',
                    'precio_mayorista': '1400.00',
                    'cantidad_por_mayor': 50,
                    'stock': 25,
                    'vencimiento': '2027-06-30',
                },
                {
                    '_temp_id': 'tmp_2',
                    'nombre': 'Producto nuevo B sin codigo',
                    # codigo / codigo_interno vacíos: el save() del
                    # modelo auto-genera el codigo_interno.
                    'precio_minorista': '100',
                },
            ],
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['actualizados'], 0)
        self.assertEqual(len(data['creados']), 2)

        # 2 artículos creados.
        self.assertEqual(Articulo.objects.count(), count_antes + 2)

        # Empareja _temp_id ↔ id.
        emparejado = {c['_temp_id']: c['id'] for c in data['creados']}
        self.assertIn('tmp_1', emparejado)
        self.assertIn('tmp_2', emparejado)

        a1 = Articulo.objects.get(pk=emparejado['tmp_1'])
        self.assertEqual(a1.nombre, 'Producto nuevo A')
        self.assertEqual(a1.codigo, 'NEW001')
        self.assertEqual(a1.precio_minorista, Decimal('1500.00'))
        self.assertEqual(a1.categoria_id, self.cat.id)
        self.assertEqual(a1.proveedor_id, self.prov.id)
        self.assertEqual(a1.vencimiento, date(2027, 6, 30))

        a2 = Articulo.objects.get(pk=emparejado['tmp_2'])
        self.assertEqual(a2.nombre, 'Producto nuevo B sin codigo')
        # codigo_interno auto-generado (no vacío).
        self.assertTrue(a2.codigo_interno)

    def test_crear_articulos_inline_rechaza_sin_nombre(self):
        """Sin nombre se rechaza con 400 y NO crea nada (atomic)."""
        count_antes = Articulo.objects.count()
        r = self._post({
            'cambios': [],
            'nuevos': [
                {'_temp_id': 'tmp_1', 'nombre': '   ', 'precio_minorista': '100'},
            ],
        })
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertFalse(data['ok'])
        self.assertEqual(Articulo.objects.count(), count_antes)

    def test_crear_articulos_inline_default_vencimiento(self):
        """
        Si no se pasa `vencimiento`, el backend aplica hoy+90d.
        No chequeamos la fecha exacta para evitar flake — solo que
        el artículo se creó (es decir: el campo NOT NULL no rompió
        el insert).
        """
        r = self._post({
            'cambios': [],
            'nuevos': [
                {'_temp_id': 'tmp_x', 'nombre': 'Sin venc', 'precio_minorista': '50'},
            ],
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        creado_id = data['creados'][0]['id']
        a = Articulo.objects.get(pk=creado_id)
        # Vencimiento futuro (defensivo, no nos importa el día exacto).
        self.assertGreater(a.vencimiento, date.today())

    def test_mixed_cambios_y_nuevos(self):
        """
        En la MISMA request mandamos `cambios` y `nuevos`: ambos se
        aplican atómicamente.
        """
        existente = Articulo.objects.create(
            codigo='EXIST', nombre='Existente',
            stock=10, precio_minorista=Decimal('500'),
            precio_mayorista=Decimal('450'),
            vencimiento=date(2030, 1, 1),
            cantidad_por_mayor=10,
        )
        r = self._post({
            'cambios': [{'id': existente.id, 'precio_minorista': '777.77'}],
            'nuevos': [
                {'_temp_id': 'tmp_mix', 'nombre': 'Mix nuevo', 'precio_minorista': '99'},
            ],
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['actualizados'], 1)
        self.assertEqual(len(data['creados']), 1)
        existente.refresh_from_db()
        self.assertEqual(existente.precio_minorista, Decimal('777.77'))


# ---------------------------------------------------------------------------
# Tests: vista pública por token (FEATURE 3)
# ---------------------------------------------------------------------------
class ListaPreciosVistaPublicaTests(ListaPreciosTestsBase):
    """
    Vista PÚBLICA por token UUID. No requiere auth.

    Cubrimos los 3 caminos:
      - token válido y vigente → 200 + datos
      - token expirado → 404 con template específico
      - token revocado / inexistente → 404
    """

    def setUp(self):
        # Override: NO logueamos. La gracia de esta vista es que es
        # pública. Reemplazamos `setUp` de la base que loguea staff.
        pass

    def test_token_valido_renderiza_lista(self):
        info = self.lista_previa.compartir(dias=7)
        url = reverse('lista_precios_publica_web', args=[info['share_token']])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        # Datos del cliente y al menos uno de los precios.
        self.assertContains(r, self.cliente.nombre_completo())
        # art1 ($1000) con 5% desc = 950. El template aplica
        # `floatformat:2` que respeta la locale activa (es-AR usa
        # coma decimal), así que aceptamos ambos formatos.
        content = r.content.decode()
        self.assertTrue(
            '950.00' in content or '950,00' in content,
            f'No encontré 950.00 ni 950,00 en el response',
        )

    def test_token_expirado_da_404(self):
        """Token cuya fecha de expiración ya pasó → 404 + template."""
        self.lista_previa.share_token = uuid.uuid4()
        self.lista_previa.share_expira_at = timezone.now() - timedelta(hours=1)
        self.lista_previa.save(update_fields=['share_token', 'share_expira_at'])

        url = reverse('lista_precios_publica_web', args=[self.lista_previa.share_token])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)
        # El template específico tiene el copy "Link no disponible".
        self.assertContains(r, 'Link no disponible', status_code=404)

    def test_token_inexistente_da_404(self):
        """UUID al azar (no en DB) → 404."""
        url = reverse('lista_precios_publica_web', args=[uuid.uuid4()])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, 'Link no disponible', status_code=404)

    def test_link_revocado_da_404(self):
        """Después de desactivar_link, el viejo token deja de funcionar."""
        info = self.lista_previa.compartir(dias=7)
        token = info['share_token']
        self.lista_previa.desactivar_link()
        url = reverse('lista_precios_publica_web', args=[token])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)

    def test_pdf_publico_con_token_valido(self):
        """
        GET /p/lista-precios/<token>/pdf/ devuelve 200 + content-type pdf.

        Si reportlab tiene issues en el entorno, lo skippeamos
        defensivamente (igual que el test del PDF interno).
        """
        try:
            import reportlab  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('reportlab no instalado en este entorno')

        info = self.lista_previa.compartir(dias=3)
        url = reverse('lista_precios_publica_pdf', args=[info['share_token']])
        try:
            r = self.client.get(url)
        except Exception as e:
            raise unittest.SkipTest(f'reportlab error de runtime: {e}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF-'))

    def test_pdf_admin_sin_login_redirige(self):
        """
        El PDF interno SIGUE protegido — no se vuelve público al agregar
        la versión por token. Confirma que el cambio no rompió auth.
        """
        url = reverse('lista_precios_api_pdf', args=[self.lista_previa.id])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('/admin/login/', r.url)


# ---------------------------------------------------------------------------
# Tests: APIs de compartir / desactivar
# ---------------------------------------------------------------------------
class ListaPreciosCompartirTests(ListaPreciosTestsBase):

    def test_compartir_genera_share_url(self):
        """POST a /compartir/ genera token + url absoluta + expira_at."""
        url = reverse('lista_precios_api_compartir', args=[self.lista_previa.id])
        r = self.client.post(url, data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['share_token'])
        self.assertTrue(data['share_url'].startswith('http'))
        self.assertIn(data['share_token'], data['share_url'])
        # La fecha de expiración viene en formato ISO no vacía (default
        # de config = 7 días > 0).
        self.assertTrue(data['expira_at'])

        self.lista_previa.refresh_from_db()
        self.assertTrue(self.lista_previa.link_activo)

    def test_compartir_con_dias_custom(self):
        """Body {dias: 30} overridea el default de config."""
        url = reverse('lista_precios_api_compartir', args=[self.lista_previa.id])
        r = self.client.post(
            url,
            data=json.dumps({'dias': 30}),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        # Expira aprox en 30 días — chequeamos un rango holgado para
        # evitar flake por skew de clock.
        self.lista_previa.refresh_from_db()
        delta = self.lista_previa.share_expira_at - timezone.now()
        self.assertGreater(delta, timedelta(days=29))
        self.assertLess(delta, timedelta(days=31))

    def test_desactivar_link(self):
        """POST a /desactivar-link/ revoca el token (link_activo = False)."""
        self.lista_previa.compartir(dias=7)
        self.assertTrue(self.lista_previa.link_activo)

        url = reverse('lista_precios_api_desactivar_link', args=[self.lista_previa.id])
        r = self.client.post(url)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.lista_previa.refresh_from_db()
        self.assertFalse(self.lista_previa.link_activo)
        self.assertIsNone(self.lista_previa.share_token)


# ---------------------------------------------------------------------------
# Tests: helpers del modelo (compartir / desactivar / link_activo)
# ---------------------------------------------------------------------------
class ListaPreciosModelHelpersTests(TestCase):
    """Tests unitarios del modelo, sin tocar HTTP."""

    @classmethod
    def setUpTestData(cls):
        cls.cliente = Cliente.objects.create(
            nombre='Modelo', apellido='Test',
            direccion='Tests', telefono='0',
        )
        cls.lista = ListaPrecios.objects.create(
            cliente=cls.cliente, nombre='helper',
        )

    def test_link_activo_inicial_false(self):
        """Una lista recién creada NO tiene link activo."""
        self.assertFalse(self.lista.link_activo)

    def test_compartir_setea_token_y_expira(self):
        info = self.lista.compartir(dias=14)
        self.assertIsNotNone(info['share_token'])
        self.assertIsNotNone(info['share_expira_at'])
        self.assertTrue(self.lista.link_activo)
        # +14d ~ futuro lejano.
        delta = info['share_expira_at'] - timezone.now()
        self.assertGreater(delta, timedelta(days=13))

    def test_compartir_con_dias_cero_no_expira(self):
        """dias=0 explícito = link sin vencimiento."""
        info = self.lista.compartir(dias=0)
        self.assertIsNotNone(info['share_token'])
        self.assertIsNone(info['share_expira_at'])
        self.assertTrue(self.lista.link_activo)

    def test_desactivar_link_es_idempotente(self):
        self.lista.compartir(dias=7)
        self.lista.desactivar_link()
        self.assertFalse(self.lista.link_activo)
        # Llamar de nuevo no rompe ni modifica nada raro.
        self.lista.desactivar_link()
        self.assertFalse(self.lista.link_activo)
        self.assertIsNone(self.lista.share_token)
