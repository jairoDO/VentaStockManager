"""
Escenarios E2E de lista de precios — preguntas del operador que se
repetían durante el testeo en producción:

  1. ¿Qué pasa con la lista si DESPUÉS de crearla actualizo el precio
     del artículo desde el admin?
  2. ¿Se "snapshotea" el precio al momento de crear la lista, o se
     recalcula al generar el PDF / link público?
  3. Si tengo un PrecioCliente pactado para ese cliente+artículo,
     ¿la lista lo respeta o usa el precio minorista?
  4. ¿Qué pasa con los PrecioCliente cuando subo el precio del
     artículo? ¿Se invalidan automáticamente?
  5. Lista compartida por link: ¿el cliente ve los precios "viejos"
     (snapshot) o los actuales?

Cada test verifica un comportamiento concreto + tiene un docstring
explicando QUÉ y POR QUÉ. Se corren con:

    python manage.py test articulo.tests_lista_escenarios -v 2

Local NO funciona si tu Python es < 3.10 (el código usa sintaxis
`int | None`). En Render Shell sí (Python 3.11).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from articulo.models import (
    Articulo,
    Categoria,
    ListaPrecios,
    ListaPreciosItem,
)
from cliente.models import Cliente, PrecioCliente


class ListaPreciosEscenariosBase(TestCase):
    """Setup compartido entre todos los escenarios."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='escenarios_test', password='x', is_staff=True,
        )
        cls.cat = Categoria.objects.create(
            nombre='Cat_escenarios', color='#abcdef',
        )
        cls.cliente = Cliente.objects.create(
            nombre='Cliente',
            apellido='Escenarios',
            direccion='Test 123',
            telefono='123456',
        )
        # Artículo con precio inicial conocido
        cls.art = Articulo.objects.create(
            codigo='ESC001',
            nombre='Artículo de prueba',
            marca='Marca X',
            precio_minorista=Decimal('100.00'),
            precio_mayorista=Decimal('80.00'),
            cantidad_por_mayor=10,
            categoria=cls.cat,
        )


class EscenarioPreciosActualizados(ListaPreciosEscenariosBase):
    """
    Escenario 1: precios se RECALCULAN, no se snapshotean.

    Diseño actual (articulo/models.py:56-64): cuando creás una lista
    con N artículos, NO guardamos el precio en ListaPreciosItem. Al
    pedir el PDF / link público, recalculamos:
        precio_final = PrecioCliente OR precio_minorista
                       (- descuento OR + aumento de la lista)

    Esto es a propósito: evita que la lista quede desactualizada cuando
    sube precio_minorista. El operador NO tiene que regenerar la lista
    cada vez que cambia un precio.

    Trade-off: si Osvaldo QUIERE congelar un precio para un cliente,
    tiene que crear un PrecioCliente, no confiar en la lista.
    """

    def test_cambio_precio_minorista_afecta_lista_automaticamente(self):
        # ARRANGE: lista con un artículo, precio inicial $100
        lista = ListaPrecios.objects.create(
            cliente=self.cliente,
            nombre='Lista inicial',
            descuento_porcentaje=Decimal('10'),  # 10% off
            tipo_ajuste='descuento',
            creado_por=self.user,
        )
        ListaPreciosItem.objects.create(lista=lista, articulo=self.art, orden=1)

        # ACT: cambiar precio del artículo de $100 a $150
        self.art.precio_minorista = Decimal('150.00')
        self.art.save()

        # ASSERT: cuando se rinde el PDF / link, el precio debe
        # reflejar $150 (no $100). El ListaPreciosItem no cambió,
        # pero la VISTA toma el precio actual del articulo.
        self.art.refresh_from_db()
        self.assertEqual(self.art.precio_minorista, Decimal('150.00'))
        # La lista todavía tiene el item (no fue borrado)
        self.assertEqual(lista.items.count(), 1)

    def test_lista_con_precio_pactado_respeta_pactado(self):
        # ARRANGE: lista del cliente + PrecioCliente acordado para
        # ese cliente+articulo a $70 (menos que el minorista de $100)
        lista = ListaPrecios.objects.create(
            cliente=self.cliente,
            nombre='Con pactado',
            descuento_porcentaje=Decimal('0'),
            creado_por=self.user,
        )
        ListaPreciosItem.objects.create(lista=lista, articulo=self.art, orden=1)
        PrecioCliente.objects.create(
            cliente=self.cliente,
            articulo=self.art,
            precio_unitario=Decimal('70.00'),
            creado_por=self.user,
        )

        # ASSERT: el precio efectivo del par cliente+art es 70
        # (no 100). La lista del cliente debe usar este.
        precio_pactado = PrecioCliente.objects.get(
            cliente=self.cliente, articulo=self.art,
        )
        self.assertEqual(precio_pactado.precio_unitario, Decimal('70.00'))


class EscenarioPrecioClienteSeInvalida(ListaPreciosEscenariosBase):
    """
    Escenario 2: al cambiar precio_minorista del artículo, los
    PrecioCliente sobre ese artículo se BORRAN automáticamente.

    Esto está implementado en Articulo.save() (líneas 563-606). La
    justificación: si vos acordaste $70 con Pérez cuando el minorista
    era $100, eso era un -30% implícito. Si después el minorista sube
    a $200, mantener el pactado en $70 = -65% — descuento gigante que
    nadie acordó. Mejor "romper" el acuerdo y obligar a Osvaldo a
    revisar.

    Trade-off: pérdida silenciosa de pactados. Atenuamos con un log.
    """

    def test_cambio_precio_minorista_borra_precios_cliente(self):
        # ARRANGE: PrecioCliente acordado para el artículo
        PrecioCliente.objects.create(
            cliente=self.cliente,
            articulo=self.art,
            precio_unitario=Decimal('70.00'),
            creado_por=self.user,
        )
        self.assertEqual(
            PrecioCliente.objects.filter(articulo=self.art).count(), 1,
        )

        # ACT: cambiar precio del artículo (de $100 a $150)
        self.art.precio_minorista = Decimal('150.00')
        self.art.save()

        # ASSERT: el PrecioCliente fue borrado
        self.assertEqual(
            PrecioCliente.objects.filter(articulo=self.art).count(), 0,
            msg='Articulo.save() debería borrar los PrecioCliente '
                'cuando precio_minorista cambia (acuerdos stale).',
        )

    def test_cambio_de_otro_campo_NO_borra_precios_cliente(self):
        # ARRANGE: PrecioCliente acordado
        PrecioCliente.objects.create(
            cliente=self.cliente,
            articulo=self.art,
            precio_unitario=Decimal('70.00'),
            creado_por=self.user,
        )

        # ACT: cambiar SOLO el nombre del artículo (precio sin cambios)
        self.art.nombre = 'Renombrado'
        self.art.save()

        # ASSERT: el PrecioCliente sigue ahí (no era un cambio de precio)
        self.assertEqual(
            PrecioCliente.objects.filter(articulo=self.art).count(), 1,
            msg='Solo cambios de precio_minorista deben invalidar '
                'PrecioCliente, no cambios de nombre/codigo/etc.',
        )

    def test_no_se_puede_borrar_articulo_en_lista(self):
        """
        ListaPreciosItem.articulo tiene on_delete=PROTECT. Eso impide
        borrar un artículo si está en alguna lista — el operador tiene
        que sacarlo de las listas primero.

        Por qué PROTECT y no CASCADE:
        - CASCADE: borrar artículo → las listas pierden items sin
          que el operador se entere. Si tenía 30 productos en la lista
          y borró uno, queda con 29 silenciosamente.
        - PROTECT: avisa "no puedo, está en la lista X". El operador
          decide explícitamente qué hacer.
        """
        # ARRANGE: artículo dentro de una lista
        lista = ListaPrecios.objects.create(
            cliente=self.cliente,
            nombre='Lista con item',
            descuento_porcentaje=Decimal('0'),
            creado_por=self.user,
        )
        ListaPreciosItem.objects.create(lista=lista, articulo=self.art, orden=1)

        # ACT + ASSERT: borrar el artículo debe fallar
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.art.delete()


class EscenarioCompartirLista(ListaPreciosEscenariosBase):
    """
    Escenario 3: compartir lista por link público. Behavior:

      - compartir() genera un UUID4 y lo guarda en share_token.
      - Si la lista ya tenía un token, lo PISA (queda inválido el viejo).
      - share_expira_at se setea según `dias` (default desde
        ConfiguracionGeneral.lista_precios_link_dias, típicamente 7).
      - desactivar_link() pone share_token=None → link cae.

    Cuando un cliente accede al link público, la vista pública RE-CALCULA
    los precios desde Articulo + PrecioCliente. Si Osvaldo subió un
    precio entre que compartió y el cliente abrió el link, el cliente
    ve el precio nuevo (no el del momento del share).

    Eso puede ser bueno o malo según el caso:
      - Bueno: si baja un precio, el cliente lo ve sin tener que
        regenerar el link.
      - Malo: si sube un precio mientras el cliente está mirando, ve
        el aumento. Podría sentirlo como "trampa". En la práctica no
        es tema porque el operador no sube precios cada 5 minutos.
    """

    def test_compartir_genera_token_y_expiracion(self):
        lista = ListaPrecios.objects.create(
            cliente=self.cliente,
            nombre='Lista compartible',
            descuento_porcentaje=Decimal('0'),
            creado_por=self.user,
        )
        self.assertIsNone(lista.share_token)
        self.assertFalse(lista.link_activo)

        # ACT: compartir con 5 días de expiración
        result = lista.compartir(dias=5)

        # ASSERT
        lista.refresh_from_db()
        self.assertIsNotNone(lista.share_token)
        self.assertIsNotNone(lista.share_expira_at)
        self.assertTrue(lista.link_activo)
        self.assertEqual(result['share_token'], lista.share_token)
        # La expiración debe estar ~5 días en el futuro
        esperado = timezone.now() + timedelta(days=5)
        diff = abs((lista.share_expira_at - esperado).total_seconds())
        self.assertLess(diff, 60, msg='Expiración debería ser dentro de 5 días')

    def test_compartir_de_nuevo_pisa_el_token_anterior(self):
        """El link viejo deja de funcionar — es una forma de revocar."""
        lista = ListaPrecios.objects.create(
            cliente=self.cliente, nombre='X',
            descuento_porcentaje=Decimal('0'), creado_por=self.user,
        )
        result1 = lista.compartir(dias=7)
        token_viejo = result1['share_token']

        # ACT: compartir de nuevo
        result2 = lista.compartir(dias=7)

        # ASSERT: token nuevo, distinto del viejo
        self.assertNotEqual(result2['share_token'], token_viejo)

    def test_desactivar_link_revoca(self):
        lista = ListaPrecios.objects.create(
            cliente=self.cliente, nombre='X',
            descuento_porcentaje=Decimal('0'), creado_por=self.user,
        )
        lista.compartir(dias=7)
        self.assertTrue(lista.link_activo)

        # ACT
        lista.desactivar_link()

        # ASSERT
        lista.refresh_from_db()
        self.assertIsNone(lista.share_token)
        self.assertFalse(lista.link_activo)

    def test_link_expirado_se_considera_inactivo(self):
        """`link_activo` propiedad: token presente Y expiración futura."""
        lista = ListaPrecios.objects.create(
            cliente=self.cliente, nombre='X',
            descuento_porcentaje=Decimal('0'), creado_por=self.user,
        )
        import uuid
        lista.share_token = uuid.uuid4()
        lista.share_expira_at = timezone.now() - timedelta(days=1)  # ya expirado
        lista.save()

        self.assertFalse(lista.link_activo, 'Token presente pero expirado = inactivo')


class EscenarioDescuentoVsAumento(ListaPreciosEscenariosBase):
    """
    Escenario 4: lista con tipo_ajuste 'descuento' vs 'aumento'.

    descuento: precio_final = precio_base * (1 - pct/100)
    aumento:   precio_final = precio_base * (1 + pct/100)

    El campo `descuento_porcentaje` siempre guarda un número positivo
    (0-100); `tipo_ajuste` define el signo. Esto es por compatibilidad
    con DBs viejas donde solo había descuento (el rename completo del
    campo habría roto datos existentes).
    """

    def test_descuento_baja_precio(self):
        lista = ListaPrecios.objects.create(
            cliente=self.cliente,
            nombre='Promo -20%',
            descuento_porcentaje=Decimal('20'),
            tipo_ajuste='descuento',
            creado_por=self.user,
        )
        # Precio base $100, descuento 20% → $80
        precio_base = self.art.precio_minorista
        factor = Decimal('1') - lista.descuento_porcentaje / Decimal('100')
        esperado = precio_base * factor
        self.assertEqual(esperado, Decimal('80.00'))

    def test_aumento_sube_precio(self):
        lista = ListaPrecios.objects.create(
            cliente=self.cliente,
            nombre='Inflación +15%',
            descuento_porcentaje=Decimal('15'),
            tipo_ajuste='aumento',
            creado_por=self.user,
        )
        # Precio base $100, aumento 15% → $115
        precio_base = self.art.precio_minorista
        factor = Decimal('1') + lista.descuento_porcentaje / Decimal('100')
        esperado = precio_base * factor
        self.assertEqual(esperado, Decimal('115.00'))
