from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cliente.models import Cliente, RutaSemanalCliente
from cliente.rutas import recalcular_rutas_sugeridas
from venta.models import Venta
from vendedor.models import Vendedor
from wa_campania.audiencia import resolver_clientes


class RutasSemanalesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            'admin_rutas', password='x', is_staff=True, is_superuser=True,
        )
        self.vendedor = Vendedor.objects.create(
            usuario=self.admin, nombre='Ana', apellido='Ruta',
        )
        self.cliente = Cliente.objects.create(
            nombre='Cliente', apellido='Semanal',
            vendedor_asignado=self.vendedor,
            whatsapp_number='5493515551111',
            puede_recibir_whatsapp=True,
        )

    def _fecha_reciente(self, weekday, semanas_atras=0):
        hoy = timezone.now().date()
        return hoy - timedelta(
            days=(hoy.weekday() - weekday) % 7 + semanas_atras * 7,
        )

    def test_calculo_elige_el_dia_mas_frecuente(self):
        for semana in (0, 1, 2):
            Venta.objects.create(
                fecha_compra=self._fecha_reciente(0, semana),
                fecha_entrega=self._fecha_reciente(0, semana),
                cliente=self.cliente,
                vendedor=self.vendedor,
            )
        Venta.objects.create(
            fecha_compra=self._fecha_reciente(1),
            fecha_entrega=self._fecha_reciente(1),
            cliente=self.cliente,
            vendedor=self.vendedor,
        )

        recalcular_rutas_sugeridas()

        ruta = RutaSemanalCliente.objects.get(cliente=self.cliente)
        self.assertEqual(ruta.dia_semana, 0)
        self.assertEqual(ruta.ventas_analizadas, 4)
        self.assertEqual(float(ruta.confianza), 75.0)
        self.assertTrue(ruta.sugerida)

    def test_recalculo_no_pisa_una_asignacion_manual(self):
        RutaSemanalCliente.objects.create(
            cliente=self.cliente, vendedor=self.vendedor,
            dia_semana=4, sugerida=False, confianza=100,
        )
        Venta.objects.create(
            fecha_compra=self._fecha_reciente(0),
            fecha_entrega=self._fecha_reciente(0),
            cliente=self.cliente,
            vendedor=self.vendedor,
        )

        recalcular_rutas_sugeridas()

        ruta = RutaSemanalCliente.objects.get(cliente=self.cliente)
        self.assertEqual(ruta.dia_semana, 4)
        self.assertFalse(ruta.sugerida)

    def test_pantalla_permite_asignar_todo_el_filtro(self):
        RutaSemanalCliente.objects.create(
            cliente=self.cliente, vendedor=self.vendedor, dia_semana=0,
        )
        self.client.force_login(self.admin)

        response = self.client.post('/clientes/rutas-semanales/', {
            'accion': 'asignar',
            'todos_filtrados': '1',
            'volver_a': f'vendedor={self.vendedor.id}&dia=0',
            'vendedor_destino': self.vendedor.id,
            'dia_destino': 2,
        })

        self.assertEqual(response.status_code, 302)
        self.cliente.ruta_semanal.refresh_from_db()
        self.assertEqual(self.cliente.ruta_semanal.dia_semana, 2)
        self.assertFalse(self.cliente.ruta_semanal.sugerida)

    def test_filtros_combinados_y_checkboxes_se_renderizan(self):
        RutaSemanalCliente.objects.create(
            cliente=self.cliente, vendedor=self.vendedor, dia_semana=1,
        )
        otro = Cliente.objects.create(nombre='Otro', apellido='Sin ruta')
        self.client.force_login(self.admin)

        response = self.client.get('/clientes/rutas-semanales/', {
            'vendedor': self.vendedor.id,
            'dia': 1,
            'estado': 'sugeridos',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['pagina'].paginator.count, 1)
        self.assertContains(response, f'name="cliente_ids" value="{self.cliente.id}"')
        self.assertNotContains(response, f'name="cliente_ids" value="{otro.id}"')
        self.assertContains(response, 'class="browser-default"')
        self.assertContains(response, '1 cliente')
        self.assertContains(response, 'admin_rutas (Ana Ruta)')
        self.assertContains(response, '· Martes')

    def test_busqueda_filtra_por_nombre_o_direccion(self):
        RutaSemanalCliente.objects.create(
            cliente=self.cliente, vendedor=self.vendedor, dia_semana=1,
        )
        otro = Cliente.objects.create(
            nombre='Otro', apellido='Cliente', direccion='Barrio Centro',
        )
        self.client.force_login(self.admin)

        response = self.client.get('/clientes/rutas-semanales/', {'q': 'Semanal'})

        self.assertEqual(response.context['pagina'].paginator.count, 1)
        self.assertContains(response, self.cliente.nombre_completo())
        self.assertNotContains(response, otro.nombre_completo())

    def test_campania_filtra_por_vendedor_y_dia_de_ruta(self):
        RutaSemanalCliente.objects.create(
            cliente=self.cliente, vendedor=self.vendedor, dia_semana=3,
        )

        audiencia = resolver_clientes({
            'vendedor_ids': [self.vendedor.id],
            'dia_visita': 3,
            'solo_con_whatsapp_valido': True,
        })

        self.assertEqual(list(audiencia), [self.cliente])
