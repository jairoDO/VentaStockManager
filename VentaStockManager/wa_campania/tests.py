"""
Tests E2E del flujo de campaña sin tocar el wa-bot real.

Mockeamos `wa_campania.wa_client` para simular respuestas del service
Node.js. Los tests cubren:
  - Resolver de audiencia (filtros vacíos, todos, saldos).
  - `crear_envios_pendientes` no duplica.
  - `enviar_campania` marca status según respuesta del cliente.
  - Render de variables en el mensaje.
  - Bot no disponible → todos los envíos quedan fallidos pero la
    campaña finaliza (no se traba).
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from cliente.models import Cliente, CuentaCliente, MovimientoCuenta
from wa_campania.audiencia import resolver_clientes
from wa_campania.models import Campania, EnvioWhatsapp
from wa_campania.tasks import (
    _render_mensaje,
    crear_envios_pendientes,
    enviar_campania,
)


User = get_user_model()


def _crear_cliente(nombre, apellido='', whatsapp='5491155551234', puede_recibir=True):
    # En tests creamos clientes con opt-in TRUE por default — sino,
    # cada test tendría que setear el flag a mano. Los tests de
    # consentimiento explícitamente pasan `puede_recibir=False`.
    c = Cliente.objects.create(
        nombre=nombre,
        apellido=apellido,
        whatsapp_number=whatsapp,
        puede_recibir_whatsapp=puede_recibir,
    )
    # El backfill de la migration creó cuentas para los clientes
    # existentes; para los que creamos en tests las hacemos a mano.
    CuentaCliente.objects.get_or_create(cliente=c)
    return c


class AudienciaResolverTests(TestCase):

    def setUp(self):
        self.c_con_wa = _crear_cliente('Con', 'Whatsapp', whatsapp='5491111111111')
        self.c_sin_wa = _crear_cliente('Sin', 'Whatsapp', whatsapp='')
        self.c_saldo_favor = _crear_cliente('Saldo', 'Favor', whatsapp='5492222222222')
        MovimientoCuenta.objects.create(
            cuenta=self.c_saldo_favor.cuenta,
            tipo=MovimientoCuenta.TIPO_PAGO,
            monto=Decimal('1000'),
        )

    def test_filtro_vacio_devuelve_vacio(self):
        # Si no marcamos ninguna condición, NO debe disparar a todos
        # los clientes del sistema por error.
        qs = resolver_clientes({})
        self.assertEqual(qs.count(), 0)

    def test_todos_excluye_sin_whatsapp(self):
        qs = resolver_clientes({'todos': True, 'solo_con_whatsapp_valido': True})
        ids = set(qs.values_list('id', flat=True))
        self.assertIn(self.c_con_wa.id, ids)
        self.assertIn(self.c_saldo_favor.id, ids)
        self.assertNotIn(self.c_sin_wa.id, ids)

    def test_todos_incluye_sin_whatsapp_si_filtro_lo_permite(self):
        qs = resolver_clientes({'todos': True, 'solo_con_whatsapp_valido': False})
        self.assertIn(self.c_sin_wa.id, qs.values_list('id', flat=True))

    def test_filtro_saldo_a_favor(self):
        qs = resolver_clientes({'con_saldo_a_favor': True, 'solo_con_whatsapp_valido': True})
        ids = set(qs.values_list('id', flat=True))
        self.assertIn(self.c_saldo_favor.id, ids)
        self.assertNotIn(self.c_con_wa.id, ids)

    def test_cliente_sin_consentimiento_nunca_recibe(self):
        # Aunque el filtro diga "todos", un cliente que no opt-in
        # NO debe estar en la audiencia. Esto es la garantía legal:
        # `puede_recibir_whatsapp=False` SIEMPRE excluye.
        c_no_consintio = _crear_cliente(
            'No', 'Consintio',
            whatsapp='5493333333333',
            puede_recibir=False,
        )
        qs = resolver_clientes({'todos': True, 'solo_con_whatsapp_valido': True})
        self.assertNotIn(c_no_consintio.id, qs.values_list('id', flat=True))

    def test_seleccion_manual_devuelve_solo_ids_elegidos(self):
        qs = resolver_clientes({
            'todos': True,
            'clientes_ids': [self.c_con_wa.id],
            'solo_con_whatsapp_valido': True,
        })
        self.assertEqual(list(qs.values_list('id', flat=True)), [self.c_con_wa.id])

    def test_seleccion_manual_respeta_consentimiento(self):
        no_consentido = _crear_cliente(
            'No', 'Consentido', whatsapp='5494444444444', puede_recibir=False,
        )
        qs = resolver_clientes({
            'clientes_ids': [no_consentido.id],
            'solo_con_whatsapp_valido': True,
        })
        self.assertFalse(qs.exists())


class ClientesCampaniaApiTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            'api_admin', password='x', is_staff=True, is_superuser=True,
        )
        for numero in range(12):
            _crear_cliente(
                f'Cliente {numero:02d}',
                whatsapp=f'549351555{numero:04d}',
            )
        _crear_cliente(
            'Sin permiso', whatsapp='5493519999999', puede_recibir=False,
        )
        self.client.force_login(self.admin)

    @mock.patch('wa_campania.views.wa_client.get_status_detail', return_value={})
    def test_lista_paginada_de_diez(self, mock_status):
        response = self.client.get('/wa-campania/api/clientes/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['results']), 10)
        self.assertEqual(data['total'], 12)
        self.assertEqual(data['pages'], 2)
        self.assertTrue(data['has_next'])

    @mock.patch('wa_campania.views.wa_client.get_status_detail', return_value={})
    def test_busqueda_y_opt_in(self, mock_status):
        response = self.client.get('/wa-campania/api/clientes/', {'q': 'Cliente 11'})
        data = response.json()
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['results'][0]['nombre'], 'Cliente 11')

        response = self.client.get('/wa-campania/api/clientes/', {'q': 'Sin permiso'})
        self.assertEqual(response.json()['total'], 0)

    @mock.patch('wa_campania.views.wa_client.get_status_detail')
    def test_excluye_el_numero_conectado_al_bot(self, mock_status):
        cliente = Cliente.objects.get(nombre='Cliente 03')
        mock_status.return_value = {
            'me': {'id': {'user': f'{cliente.whatsapp_number}:12'}},
        }

        data = self.client.get('/wa-campania/api/clientes/').json()

        self.assertEqual(data['total'], 11)
        self.assertEqual(data['excluded_sender_number'], cliente.whatsapp_number)
        self.assertEqual(data['excluded_sender_client_ids'], [cliente.id])
        self.assertNotIn(cliente.id, [item['id'] for item in data['results']])


class CampaniaAdminTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            'campaign_admin', password='x', is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.admin)

    @mock.patch('wa_campania.admin.async_task')
    @mock.patch('wa_campania.admin.crear_envios_pendientes', return_value=1)
    def test_guardar_y_enviar_guarda_y_encola(self, mock_crear, mock_async):
        response = self.client.post(
            reverse('admin:wa_campania_campania_add'),
            {
                'nombre': 'Promo directa',
                'mensaje': 'Hola {{nombre}}',
                'audiencia_filtro': '{"todos": true, "solo_con_whatsapp_valido": true}',
                '_saveandsend': 'Guardar y enviar',
            },
        )

        campania = Campania.objects.get(nombre='Promo directa')
        self.assertRedirects(
            response,
            reverse('admin:wa_campania_campania_change', args=[campania.pk]),
            fetch_redirect_response=False,
        )
        mock_crear.assert_called_once_with(campania)
        mock_async.assert_called_once_with(
            'wa_campania.tasks.enviar_campania', campania.id,
        )


class RenderMensajeTests(TestCase):

    def test_sustituye_variables(self):
        c = _crear_cliente('Juan', 'Perez', whatsapp='5491155551234')
        MovimientoCuenta.objects.create(
            cuenta=c.cuenta, tipo=MovimientoCuenta.TIPO_PAGO, monto=Decimal('500'),
        )
        msg = _render_mensaje(
            'Hola {{nombre}} {{apellido}}, tu saldo es ${{saldo}}', c,
        )
        self.assertEqual(msg, 'Hola Juan Perez, tu saldo es $500.00')

    def test_variable_inexistente_queda_literal(self):
        c = _crear_cliente('Juan', 'Perez', whatsapp='5491155551234')
        msg = _render_mensaje('Hola {{nombre}} y {{telefono}}', c)
        # `safe_substitute` deja `{{telefono}}` literal porque solo
        # convertimos nombre/apellido/saldo a $vars conocidas.
        self.assertIn('Juan', msg)
        self.assertIn('{{telefono}}', msg)


@override_settings(WHATSAPP_DELAY_SECONDS=0)
class EnviarCampaniaTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('test_admin', is_superuser=True, is_staff=True)
        self.c1 = _crear_cliente('Ana', 'Lopez', whatsapp='5491111111111')
        self.c2 = _crear_cliente('Bea', 'Diaz', whatsapp='5492222222222')
        self.campania = Campania.objects.create(
            nombre='Promo test',
            mensaje='Hola {{nombre}}',
            audiencia_filtro={'todos': True, 'solo_con_whatsapp_valido': True},
            creado_por=self.user,
        )

    def test_crear_envios_pendientes_idempotente(self):
        n1 = crear_envios_pendientes(self.campania)
        # Segunda llamada NO duplica (constraint UNIQUE).
        n2 = crear_envios_pendientes(self.campania)
        self.assertGreaterEqual(n1, 2)
        self.assertEqual(self.campania.envios.count(), n1)

    @mock.patch('wa_campania.tasks.wa_client.is_ready', return_value=(True, 'CONNECTED'))
    @mock.patch('wa_campania.tasks.wa_client.send_text', return_value={'ok': True, 'id': 'abc'})
    def test_envio_exitoso_marca_enviado(self, mock_send, mock_ready):
        crear_envios_pendientes(self.campania)
        resultado = enviar_campania(self.campania.id)
        self.assertTrue(resultado['ok'])
        self.assertGreaterEqual(resultado['enviados'], 2)
        self.assertEqual(resultado['fallidos'], 0)
        for e in self.campania.envios.all():
            self.assertEqual(e.status, EnvioWhatsapp.STATUS_ENVIADO)
            self.assertTrue(e.sent_at)
            self.assertIn(e.cliente.nombre, e.mensaje_renderizado)
        self.campania.refresh_from_db()
        self.assertEqual(self.campania.estado, Campania.ESTADO_FINALIZADA)

    @mock.patch('wa_campania.tasks.wa_client.is_ready', return_value=(True, 'CONNECTED'))
    @mock.patch('wa_campania.tasks.wa_client.send_text', return_value={'ok': False, 'error': 'numero invalido'})
    def test_envio_fallido_no_traba_la_cola(self, mock_send, mock_ready):
        crear_envios_pendientes(self.campania)
        resultado = enviar_campania(self.campania.id)
        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['enviados'], 0)
        self.assertGreaterEqual(resultado['fallidos'], 2)
        for e in self.campania.envios.all():
            self.assertEqual(e.status, EnvioWhatsapp.STATUS_FALLIDO)
            self.assertIn('numero invalido', e.error_msg)

    @mock.patch('wa_campania.tasks.wa_client.is_ready', return_value=(False, 'disconnected'))
    def test_bot_no_disponible_finaliza_campania_con_fallidos(self, mock_ready):
        crear_envios_pendientes(self.campania)
        resultado = enviar_campania(self.campania.id)
        self.assertFalse(resultado['ok'])
        self.campania.refresh_from_db()
        # La campaña queda finalizada para que el admin pueda crear
        # una nueva y reintentar; no la dejamos en "enviando" para
        # siempre.
        self.assertEqual(self.campania.estado, Campania.ESTADO_FINALIZADA)
        for e in self.campania.envios.all():
            self.assertEqual(e.status, EnvioWhatsapp.STATUS_FALLIDO)
            self.assertIn('wa-bot no disponible', e.error_msg)
