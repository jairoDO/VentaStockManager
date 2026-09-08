import json
from types import SimpleNamespace

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from cliente.models import Cliente
from cliente.admin import ClienteAdmin
from vendedor.models import Vendedor
from venta.views_nueva import (
    api_cliente_asignarme,
    api_cliente_crear,
    api_clientes_buscar,
)


class CarteraClienteTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()
        self.user_osvaldo = User.objects.create_user(
            'osvaldo_test', password='x', is_staff=True, is_superuser=True,
        )
        self.osvaldo = Vendedor.objects.create(
            usuario=self.user_osvaldo,
            nombre='Osvaldo',
            apellido='Prueba',
        )
        self.user_otro = User.objects.create_user(
            'otro_vendedor_test', password='x', is_staff=True,
        )
        self.otro = Vendedor.objects.create(
            usuario=self.user_otro,
            nombre='Otro',
            apellido='Vendedor',
        )
        self.cliente_osvaldo = Cliente.objects.create(
            nombre='Maira',
            apellido='Osvaldo',
            vendedor_asignado=self.osvaldo,
            asignacion_vendedor_confirmada=True,
        )
        self.cliente_otro = Cliente.objects.create(
            nombre='Maira',
            apellido='Otro',
            vendedor_asignado=self.otro,
            asignacion_vendedor_confirmada=True,
        )

    def test_admin_en_nueva_venta_solo_busca_su_cartera(self):
        request = self.factory.get('/venta/api/clientes/buscar/', {'q': 'Maira'})
        request.user = self.user_osvaldo

        response = api_clientes_buscar(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [resultado['id'] for resultado in payload['results']],
            [self.cliente_osvaldo.pk],
        )

    def test_si_no_hay_resultado_propio_busca_clientes_sin_asignar(self):
        libre = Cliente.objects.create(nombre='Micaela', apellido='Libre')
        request = self.factory.get('/venta/api/clientes/buscar/', {'q': 'Micaela'})
        request.user = self.user_osvaldo

        response = api_clientes_buscar(request)
        payload = json.loads(response.content)

        self.assertTrue(payload['buscando_fuera_cartera'])
        self.assertEqual([r['id'] for r in payload['results']], [libre.pk])
        self.assertTrue(payload['results'][0]['requiere_asignacion'])

    def test_resultado_propio_tiene_prioridad_sobre_un_cliente_libre(self):
        Cliente.objects.create(nombre='Maira', apellido='Libre')
        request = self.factory.get('/venta/api/clientes/buscar/', {'q': 'Maira'})
        request.user = self.user_osvaldo

        payload = json.loads(api_clientes_buscar(request).content)

        self.assertFalse(payload['buscando_fuera_cartera'])
        self.assertEqual(
            [r['id'] for r in payload['results']],
            [self.cliente_osvaldo.pk],
        )

    def test_vendedor_puede_asignarse_un_cliente_libre(self):
        libre = Cliente.objects.create(nombre='Cliente', apellido='Libre')
        request = self.factory.post(f'/venta/api/clientes/{libre.pk}/asignarme/')
        request.user = self.user_osvaldo

        response = api_cliente_asignarme(request, libre.pk)
        libre.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(libre.vendedor_asignado, self.osvaldo)
        self.assertTrue(libre.asignacion_vendedor_confirmada)

    def test_vendedor_puede_reasignarse_cliente_de_otro(self):
        request = self.factory.post(
            f'/venta/api/clientes/{self.cliente_otro.pk}/asignarme/',
        )
        request.user = self.user_osvaldo

        response = api_cliente_asignarme(request, self.cliente_otro.pk)
        self.cliente_otro.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cliente_otro.vendedor_asignado, self.osvaldo)

    def test_si_no_hay_resultado_propio_muestra_cliente_de_otro(self):
        request = self.factory.get(
            '/venta/api/clientes/buscar/', {'q': 'Otro'},
        )
        request.user = self.user_osvaldo

        payload = json.loads(api_clientes_buscar(request).content)

        self.assertTrue(payload['buscando_fuera_cartera'])
        self.assertEqual(payload['results'][0]['id'], self.cliente_otro.pk)
        self.assertEqual(
            payload['results'][0]['vendedor_actual'],
            self.otro.display_name(),
        )

    def test_cliente_nuevo_queda_asignado_al_vendedor_logueado(self):
        request = self.factory.post(
            '/venta/api/clientes/crear/',
            data=json.dumps({
                'nombre': 'Cliente',
                'apellido': 'Nuevo',
                'telefono': '3515551234',
            }),
            content_type='application/json',
        )
        request.user = self.user_osvaldo

        response = api_cliente_crear(request)
        payload = json.loads(response.content)
        cliente = Cliente.objects.get(pk=payload['cliente']['id'])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(cliente.vendedor_asignado, self.osvaldo)
        self.assertTrue(cliente.asignacion_vendedor_confirmada)
        self.assertEqual(cliente.creado_por, self.user_osvaldo)

    def test_cliente_creado_desde_admin_sugiere_al_vendedor_creador(self):
        request = self.factory.post('/admin/cliente/cliente/add/')
        request.user = self.user_otro
        cliente = Cliente(nombre='Micaela', apellido='Creada por Lucas')
        form = SimpleNamespace(changed_data=[])

        ClienteAdmin(Cliente, admin.site).save_model(
            request, cliente, form, change=False,
        )

        self.assertEqual(cliente.creado_por, self.user_otro)
        self.assertEqual(cliente.vendedor_sugerido, self.otro)
        self.assertFalse(cliente.asignacion_vendedor_confirmada)

    def test_pantalla_masiva_filtra_y_confirma_sugerencias(self):
        self.cliente_osvaldo.vendedor_asignado = None
        self.cliente_osvaldo.vendedor_sugerido = self.osvaldo
        self.cliente_osvaldo.asignacion_vendedor_confirmada = False
        self.cliente_osvaldo.save()
        self.client.force_login(self.user_osvaldo)

        pantalla = self.client.get(
            reverse('cliente_cartera'),
            {'estado': 'pendientes', 'sugerido': self.osvaldo.pk},
        )
        self.assertEqual(pantalla.status_code, 200)
        self.assertContains(pantalla, 'Maira Osvaldo')
        self.assertNotContains(pantalla, 'Maira Otro')

        respuesta = self.client.post(reverse('cliente_cartera'), {
            'cliente_ids': [self.cliente_osvaldo.pk],
            'accion': 'confirmar_sugerencias',
        })
        self.assertEqual(respuesta.status_code, 302)
        self.cliente_osvaldo.refresh_from_db()
        self.assertEqual(self.cliente_osvaldo.vendedor_asignado, self.osvaldo)
        self.assertIsNone(self.cliente_osvaldo.vendedor_sugerido)
        self.assertTrue(self.cliente_osvaldo.asignacion_vendedor_confirmada)

    def test_pantalla_masiva_reasigna_varios_clientes(self):
        self.client.force_login(self.user_osvaldo)
        respuesta = self.client.post(reverse('cliente_cartera'), {
            'cliente_ids': [self.cliente_osvaldo.pk, self.cliente_otro.pk],
            'accion': 'asignar_vendedor',
            'vendedor_destino': self.osvaldo.pk,
        })
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            Cliente.objects.filter(
                pk__in=[self.cliente_osvaldo.pk, self.cliente_otro.pk],
                vendedor_asignado=self.osvaldo,
                asignacion_vendedor_confirmada=True,
            ).count(),
            2,
        )

    def test_reasignar_sin_elegir_vendedor_muestra_aviso_sin_error(self):
        self.client.force_login(self.user_osvaldo)

        respuesta = self.client.post(reverse('cliente_cartera'), {
            'cliente_ids': [self.cliente_osvaldo.pk],
            'accion': 'asignar_vendedor',
            'vendedor_destino': '',
        })

        self.assertEqual(respuesta.status_code, 302)
        self.cliente_osvaldo.refresh_from_db()
        self.assertEqual(self.cliente_osvaldo.vendedor_asignado, self.osvaldo)
