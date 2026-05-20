"""
Tests de la grilla de precios — API JSON de listar y guardar.

Cubrimos:
  - Render de la pantalla (200 con usuario staff).
  - Listado: filtros por categoría / proveedor / búsqueda y la
    paginación.
  - Bulk save: actualiza solo los campos enviados, y se ve en DB.
  - Validación: precios negativos rechazados con 400 y la DB no
    cambia.
  - Auth: sin login se redirige al admin login.

Tests cortos a propósito. Usamos `Client` de Django (no E2E).
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from articulo.models import Articulo, Categoria
from compra.models import Proveedor


class GrillaPreciosTestsBase(TestCase):
    """Setup compartido: un par de categorías, proveedores y 60 artículos."""

    @classmethod
    def setUpTestData(cls):
        cls.user_staff = User.objects.create_user(
            username='staff_grilla',
            password='x',
            is_staff=True,
        )
        cls.user_regular = User.objects.create_user(
            username='regular_grilla',
            password='x',
            is_staff=False,
        )

        # Nombres únicos con sufijo `_test_grilla` para no chocar con
        # las categorías que vienen seedeadas desde la migración de
        # datos (0004_seed_categorias_y_reglas). El test DB las hereda
        # y `Categoria.nombre` es unique.
        cls.cat_limpieza = Categoria.objects.create(nombre='Limpieza_test_grilla', color='#00bcd4')
        cls.cat_golosinas = Categoria.objects.create(nombre='Golosinas_test_grilla', color='#ff9800')

        cls.prov_a = Proveedor.objects.create(nombre='Proveedor A test grilla')
        cls.prov_b = Proveedor.objects.create(nombre='Proveedor B test grilla')

        # Creamos artículos: 30 con categoría limpieza + prov A,
        # 25 con categoría golosinas + prov B, 5 sin categoría /
        # sin proveedor. Total 60, lo suficiente para probar
        # paginación (page_size default 50).
        articulos = []
        for i in range(30):
            articulos.append(Articulo(
                codigo=f'L{i:03d}',
                nombre=f'Lavandina {i}',
                stock=10 + i,
                precio_minorista=Decimal('1000.00'),
                precio_mayorista=Decimal('900.00'),
                vencimiento=date(2030, 1, 1),
                marca='Generico',
                cantidad_por_mayor=100,
                categoria=cls.cat_limpieza,
                proveedor=cls.prov_a,
            ))
        for i in range(25):
            articulos.append(Articulo(
                codigo=f'G{i:03d}',
                nombre=f'Caramelo {i}',
                stock=5 + i,
                precio_minorista=Decimal('500.00'),
                precio_mayorista=Decimal('450.00'),
                vencimiento=date(2030, 1, 1),
                marca='Arcor',
                cantidad_por_mayor=50,
                categoria=cls.cat_golosinas,
                proveedor=cls.prov_b,
            ))
        for i in range(5):
            articulos.append(Articulo(
                codigo=f'X{i:03d}',
                nombre=f'Misc {i}',
                stock=1,
                precio_minorista=Decimal('100.00'),
                precio_mayorista=Decimal('90.00'),
                vencimiento=date(2030, 1, 1),
                marca='Generico',
                cantidad_por_mayor=10,
                categoria=None,
                proveedor=None,
            ))
        # bulk_create dispara el insert sin pasar por Articulo.save()
        # — los codigo_interno se generan en save(). Acá no nos
        # importa porque los tests no chequean codigo_interno; los
        # endpoints lo devuelven igual aunque sea null.
        Articulo.objects.bulk_create(articulos)


class GrillaListarTests(GrillaPreciosTestsBase):
    """Tests del GET /articulos/api/grilla/."""

    def setUp(self):
        self.client.force_login(self.user_staff)

    def test_render_pantalla(self):
        """La pantalla principal devuelve 200 y contiene el x-data."""
        url = reverse('grilla_precios')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'grillaPrecios()')
        # Las opciones de filtros viajan como json_script.
        self.assertContains(r, 'opt-categorias')
        self.assertContains(r, 'opt-proveedores')

    def test_listar_sin_filtros_paginado(self):
        """Sin filtros: total 60, primera página con page_size items."""
        url = reverse('grilla_precios_api_listar')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['total_items'], 60)
        # Default page_size = 50.
        self.assertEqual(len(data['items']), 50)
        self.assertEqual(data['page'], 1)
        self.assertEqual(data['total_pages'], 2)

        # Cada item tiene la estructura esperada.
        primero = data['items'][0]
        for clave in (
            'id', 'codigo', 'codigo_interno', 'nombre', 'marca',
            'categoria_id', 'categoria_nombre', 'categoria_color',
            'proveedor_id', 'proveedor_nombre',
            'stock', 'precio_minorista', 'precio_mayorista', 'cantidad_por_mayor',
        ):
            self.assertIn(clave, primero, f'Falta {clave} en el item')

    def test_listar_filtra_por_categoria(self):
        """categoria=<id_limpieza> trae solo limpieza (30 items)."""
        url = reverse('grilla_precios_api_listar')
        r = self.client.get(url, {'categoria': self.cat_limpieza.id})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['total_items'], 30)
        for it in data['items']:
            self.assertEqual(it['categoria_id'], self.cat_limpieza.id)

    def test_listar_filtra_sin_categoria(self):
        """categoria=0 trae solo los que tienen categoria_id NULL."""
        url = reverse('grilla_precios_api_listar')
        r = self.client.get(url, {'categoria': '0'})
        data = r.json()
        self.assertEqual(data['total_items'], 5)
        for it in data['items']:
            self.assertIsNone(it['categoria_id'])

    def test_listar_filtra_por_proveedor(self):
        url = reverse('grilla_precios_api_listar')
        r = self.client.get(url, {'proveedor': self.prov_b.id})
        data = r.json()
        self.assertEqual(data['total_items'], 25)
        for it in data['items']:
            self.assertEqual(it['proveedor_id'], self.prov_b.id)

    def test_listar_busca_por_q(self):
        """q matchea contra nombre, codigo o codigo_interno."""
        url = reverse('grilla_precios_api_listar')
        r = self.client.get(url, {'q': 'Caramelo'})
        data = r.json()
        self.assertEqual(data['total_items'], 25)

        # Búsqueda por código.
        r = self.client.get(url, {'q': 'L001'})
        data = r.json()
        self.assertEqual(data['total_items'], 1)
        self.assertEqual(data['items'][0]['codigo'], 'L001')

    def test_listar_paginacion(self):
        """page=2 trae los 10 restantes (60 - 50)."""
        url = reverse('grilla_precios_api_listar')
        r = self.client.get(url, {'page': 2})
        data = r.json()
        self.assertEqual(data['page'], 2)
        self.assertEqual(len(data['items']), 10)


class GrillaGuardarTests(GrillaPreciosTestsBase):
    """Tests del POST /articulos/api/grilla/guardar/."""

    def setUp(self):
        self.client.force_login(self.user_staff)
        # Tomamos algunos artículos conocidos para testear el guardado.
        self.articulos = list(Articulo.objects.order_by('id')[:3])

    def _post(self, payload):
        return self.client.post(
            reverse('grilla_precios_api_guardar'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_guardar_bulk_actualiza_db(self):
        a1, a2, a3 = self.articulos
        r = self._post({
            'cambios': [
                {'id': a1.id, 'precio_minorista': '1234.56', 'precio_mayorista': '1100.00'},
                {'id': a2.id, 'stock': 999},
                # Cambio parcial: sólo cantidad_por_mayor.
                {'id': a3.id, 'cantidad_por_mayor': 200},
            ],
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['actualizados'], 3)

        a1.refresh_from_db()
        a2.refresh_from_db()
        a3.refresh_from_db()
        self.assertEqual(a1.precio_minorista, Decimal('1234.56'))
        self.assertEqual(a1.precio_mayorista, Decimal('1100.00'))
        self.assertEqual(a2.stock, 999)
        self.assertEqual(a3.cantidad_por_mayor, 200)

    def test_guardar_rechaza_precio_negativo(self):
        a1 = self.articulos[0]
        precio_original = a1.precio_minorista
        r = self._post({
            'cambios': [
                {'id': a1.id, 'precio_minorista': '-10.00'},
            ],
        })
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertFalse(data['ok'])
        self.assertTrue(any(e.get('campo') == 'precio_minorista' for e in data['errores']))

        # Y la DB no se tocó.
        a1.refresh_from_db()
        self.assertEqual(a1.precio_minorista, precio_original)

    def test_guardar_rechaza_stock_negativo(self):
        a1 = self.articulos[0]
        r = self._post({
            'cambios': [
                {'id': a1.id, 'stock': -1},
            ],
        })
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertFalse(data['ok'])

    def test_guardar_rechaza_articulo_inexistente(self):
        r = self._post({
            'cambios': [
                {'id': 9_999_999, 'precio_minorista': '100.00'},
            ],
        })
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertFalse(data['ok'])

    def test_guardar_batch_atomico_no_aplica_si_hay_error(self):
        """
        Si un cambio del batch es inválido, NINGUNO se aplica. Esto
        protege contra el caso "se actualizó la mitad y la otra se
        rechazó" — el operador ve el error claro y vuelve a intentar.
        """
        a1, a2 = self.articulos[:2]
        precio_a1 = a1.precio_minorista
        precio_a2 = a2.precio_minorista
        r = self._post({
            'cambios': [
                {'id': a1.id, 'precio_minorista': '777.77'},
                {'id': a2.id, 'precio_minorista': '-5.00'},  # inválido
            ],
        })
        self.assertEqual(r.status_code, 400)
        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertEqual(a1.precio_minorista, precio_a1)
        self.assertEqual(a2.precio_minorista, precio_a2)


class GrillaAuthTests(GrillaPreciosTestsBase):
    """Sin login válido o sin permisos, redirige al login del admin."""

    def test_listar_sin_login_redirige(self):
        url = reverse('grilla_precios_api_listar')
        r = self.client.get(url)
        # staff_member_required redirige a /admin/login/.
        self.assertEqual(r.status_code, 302)
        self.assertIn('/admin/login/', r.url)

    def test_guardar_sin_login_redirige(self):
        url = reverse('grilla_precios_api_guardar')
        r = self.client.post(
            url,
            data=json.dumps({'cambios': []}),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 302)
        self.assertIn('/admin/login/', r.url)

    def test_usuario_no_staff_redirige(self):
        self.client.force_login(self.user_regular)
        r = self.client.get(reverse('grilla_precios_api_listar'))
        self.assertEqual(r.status_code, 302)
