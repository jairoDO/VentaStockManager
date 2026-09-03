from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from articulo.models import DifusionListaPreciosEnvio, ListaPrecios
from articulo.tasks_difusion import (
    crear_envios_pendientes_difusion,
    procesar_difusion,
)
from cliente.models import Cliente


@override_settings(PUBLIC_SITE_URL='https://golosinas-insa.onrender.com')
class DifusionLinkPublicoTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('difusion_test', is_staff=True)
        self.cliente = Cliente.objects.create(
            nombre='Cliente',
            apellido='Prueba',
            telefono='3515551234',
            whatsapp_number='5493515551234',
            puede_recibir_whatsapp=True,
        )
        self.lista = ListaPrecios.objects.create(
            cliente=self.cliente,
            nombre='Lista pública',
            creado_por=self.usuario,
        )

    def test_crea_link_publico_antes_de_encolar_pdf_mas_link(self):
        self.assertFalse(self.lista.link_activo)

        creados = crear_envios_pendientes_difusion(
            self.lista,
            [self.cliente.id],
            modo_override=DifusionListaPreciosEnvio.MODO_AMBOS,
            user=self.usuario,
        )

        self.lista.refresh_from_db()
        self.assertEqual(creados, 1)
        self.assertTrue(self.lista.link_activo)
        self.assertIsNotNone(self.lista.share_token)

    @mock.patch('articulo.tasks_difusion.wa_client.send_text')
    @mock.patch('articulo.tasks_difusion.wa_client.exists')
    @mock.patch('articulo.tasks_difusion.wa_client.is_ready')
    def test_worker_renueva_link_vencido_antes_de_enviar(
        self, mock_ready, mock_exists, mock_send,
    ):
        mock_ready.return_value = (True, 'CONNECTED')
        mock_exists.return_value = {'ok': True, 'exists': True}
        mock_send.return_value = {'ok': True}
        DifusionListaPreciosEnvio.objects.create(
            lista=self.lista,
            cliente=self.cliente,
            modo=DifusionListaPreciosEnvio.MODO_LINK,
            telefono_usado=self.cliente.whatsapp_number,
        )

        with override_settings(WHATSAPP_DELAY_SECONDS=0):
            resultado = procesar_difusion(self.lista.id)

        self.lista.refresh_from_db()
        self.assertTrue(resultado['ok'])
        self.assertTrue(self.lista.link_activo)
        mensaje = mock_send.call_args.args[1]
        self.assertIn('https://golosinas-insa.onrender.com/p/lista-precios/', mensaje)
        self.assertIn(str(self.lista.share_token), mensaje)
