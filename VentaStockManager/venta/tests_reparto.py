import json
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from articulo.models import Articulo
from cliente.models import Cliente, DireccionCliente
from vendedor.models import Repartidor, Vendedor
from venta.models import ArticuloVenta, Pedido, PedidoEstadoHistorial, Venta


class RepartoFlujoTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser('admin_reparto', password='x')
        self.usuario_vendedor = User.objects.create_user(
            'vendedor_reparto', password='x', is_staff=True,
        )
        self.vendedor = Vendedor.objects.create(
            usuario=self.usuario_vendedor,
            nombre='Venta',
            apellido='Uno',
        )
        self.usuario_repartidor = User.objects.create_user('repartidor_uno', password='x')
        self.repartidor = Repartidor.objects.create(
            usuario=self.usuario_repartidor,
            nombre='Repartidor Uno',
        )
        self.usuario_otro = User.objects.create_user('repartidor_dos', password='x')
        self.otro_repartidor = Repartidor.objects.create(
            usuario=self.usuario_otro,
            nombre='Repartidor Dos',
        )
        self.cliente = Cliente.objects.create(
            nombre='Cliente',
            apellido='Con dirección',
            telefono='3515555555',
            whatsapp_number='5493515555555',
            direccion='',
        )
        self.articulo = Articulo.objects.create(
            nombre='Producto reparto',
            stock=20,
            precio_minorista='1500.00',
            precio_mayorista='1300.00',
            vencimiento=date.today() + timedelta(days=60),
        )

    def _crear_venta(self, cliente=None, fecha_entrega=None, vendedor=None):
        venta = Venta.objects.create(
            fecha_compra=date.today(),
            fecha_entrega=fecha_entrega or date.today(),
            cliente=cliente or self.cliente,
            vendedor=vendedor or self.vendedor,
        )
        ArticuloVenta.objects.create(
            venta=venta,
            articulo=self.articulo,
            cantidad=1,
            precio='1500.00',
        )
        return venta

    def test_confirmar_direccion_actualiza_cliente_y_pedido_nuevo(self):
        pedido_legacy = self._crear_venta().pedido
        self.assertFalse(pedido_legacy.direccion_confirmada)
        self.client.force_login(self.usuario_vendedor)
        response = self.client.post(
            reverse('venta_api_cliente_direccion', args=[self.cliente.pk]),
            data=json.dumps({
                'direccion_texto': 'Av. Colón 1234',
                'localidad': 'Córdoba',
                'provincia': 'Córdoba',
                'referencia': 'Portón azul',
                'latitud': '-31.412345',
                'longitud': '-64.201234',
                'precision_metros': 12,
                'fuente': 'gps',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        direccion = DireccionCliente.objects.get(cliente=self.cliente)
        self.assertTrue(direccion.confirmada)
        self.assertTrue(direccion.es_principal)
        self.assertEqual(direccion.confirmada_por, self.usuario_vendedor)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.direccion, 'Av. Colón 1234')
        self.assertEqual(response.json()['pedidos_actualizados'], 1)

        pedido_legacy.refresh_from_db()
        self.assertEqual(pedido_legacy.direccion_entrega, direccion)
        self.assertEqual(pedido_legacy.direccion_entrega_texto, 'Av. Colón 1234')
        self.assertTrue(pedido_legacy.direccion_confirmada)

        venta = self._crear_venta()
        pedido = venta.pedido
        self.assertEqual(pedido.direccion_entrega, direccion)
        self.assertEqual(pedido.direccion_entrega_texto, 'Av. Colón 1234')
        self.assertEqual(pedido.localidad_entrega, 'Córdoba')
        self.assertTrue(pedido.direccion_confirmada)
        self.assertTrue(pedido.tiene_coordenadas_entrega)

    def test_confirmar_direccion_rechaza_texto_sin_punto_en_el_mapa(self):
        self.client.force_login(self.usuario_vendedor)
        response = self.client.post(
            reverse('venta_api_cliente_direccion', args=[self.cliente.pk]),
            data=json.dumps({
                'direccion_texto': 'Av. Colón 1234',
                'localidad': 'Córdoba',
                'provincia': 'Córdoba',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Ubicá la dirección en el mapa', response.json()['error'])
        self.assertFalse(DireccionCliente.objects.filter(cliente=self.cliente).exists())

    @patch('venta.views_nueva._consultar_nominatim')
    def test_buscar_direccion_devuelve_opciones_para_el_mapa(self, consultar):
        consultar.return_value = [
            {
                'display_name': 'Avenida Colón 1234, Villa Allende, Argentina',
                'direccion_texto': 'Avenida Colón 1234',
                'localidad': 'Villa Allende',
                'provincia': 'Córdoba',
                'latitud': '-31.300000',
                'longitud': '-64.300000',
            },
            {
                'display_name': 'Avenida Colón 1234, Córdoba, Argentina',
                'direccion_texto': 'Avenida Colón 1234',
                'localidad': 'Córdoba',
                'provincia': 'Córdoba',
                'latitud': '-31.410000',
                'longitud': '-64.210000',
            },
        ]
        self.client.force_login(self.usuario_vendedor)
        response = self.client.get(
            reverse('venta_api_direccion_geocodificar'),
            {
                'direccion': 'Av. Colón 1234',
                'localidad': 'Córdoba',
                'provincia': 'Córdoba',
            },
        )

        self.assertEqual(response.status_code, 200)
        consultar.assert_called_once_with('Av. Colón 1234', 'Córdoba', 'Córdoba')
        resultado = response.json()['resultados'][0]
        self.assertEqual(resultado['localidad'], 'Córdoba')
        self.assertEqual(resultado['latitud'], '-31.410000')
        self.assertEqual(resultado['longitud'], '-64.210000')

    def test_guardar_venta_usa_la_direccion_seleccionada(self):
        direccion = DireccionCliente.objects.create(
            cliente=self.cliente,
            etiqueta='Depósito',
            direccion_texto='Circunvalación 2500',
            localidad='Córdoba',
            provincia='Córdoba',
            latitud='-31.450000',
            longitud='-64.220000',
            confirmada=True,
            es_principal=True,
        )
        self.client.force_login(self.usuario_vendedor)
        response = self.client.post(
            reverse('venta_api_guardar'),
            data=json.dumps({
                'cliente_id': self.cliente.pk,
                'direccion_id': direccion.pk,
                'vendedor_id': self.vendedor.pk,
                # Aunque un cliente viejo intente enviar otra fecha, el
                # backend debe usar automáticamente la fecha operativa.
                'fecha_compra': '2000-01-01',
                'fecha_entrega': str(date.today()),
                'items': [{
                    'articulo_id': self.articulo.pk,
                    'cantidad': 1,
                    'precio': '1500.00',
                    'descuento_porcentaje': 0,
                }],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        venta = Venta.objects.get(pk=response.json()['venta_id'])
        from venta.views_nueva import _fecha_hoy_operativa
        self.assertEqual(venta.fecha_compra, _fecha_hoy_operativa())
        self.assertEqual(venta.pedido.direccion_entrega, direccion)
        self.assertEqual(venta.pedido.direccion_entrega_texto, 'Circunvalación 2500')
        self.assertTrue(venta.pedido.direccion_confirmada)

    def test_guardar_venta_rechaza_fecha_entrega_vacia(self):
        self.client.force_login(self.usuario_vendedor)
        response = self.client.post(
            reverse('venta_api_guardar'),
            data=json.dumps({
                'cliente_id': self.cliente.pk,
                'vendedor_id': self.vendedor.pk,
                'fecha_compra': str(date.today()),
                'fecha_entrega': '',
                'items': [{
                    'articulo_id': self.articulo.pk,
                    'cantidad': 1,
                    'precio': '1500.00',
                    'descuento_porcentaje': 0,
                }],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'La fecha de entrega es obligatoria',
            ' '.join(response.json()['errores']),
        )
        self.assertEqual(Venta.objects.count(), 0)

    def test_guardar_venta_rechaza_direccion_sin_confirmar(self):
        DireccionCliente.objects.create(
            cliente=self.cliente,
            direccion_texto='Av. Colón 1234',
            localidad='Córdoba',
            provincia='Córdoba',
            latitud='-31.410000',
            longitud='-64.210000',
            confirmada=False,
            es_principal=True,
        )
        self.client.force_login(self.usuario_vendedor)
        response = self.client.post(
            reverse('venta_api_guardar'),
            data=json.dumps({
                'cliente_id': self.cliente.pk,
                'vendedor_id': self.vendedor.pk,
                'fecha_entrega': str(date.today()),
                'items': [{
                    'articulo_id': self.articulo.pk,
                    'cantidad': 1,
                    'precio': '1500.00',
                    'descuento_porcentaje': 0,
                }],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            'Confirmá la dirección para poder guardar la venta.',
            response.json()['errores'],
        )
        self.assertEqual(Venta.objects.count(), 0)

    def test_pantalla_venta_renderiza_control_de_direccion(self):
        # El administrador omite la restricción horaria de los vendedores,
        # así la prueba valida siempre el template real de carga de venta.
        self.client.force_login(self.admin)
        response = self.client.get(reverse('venta_nueva'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dirección pendiente de confirmar')
        self.assertContains(response, 'Usar mi ubicación actual')
        self.assertContains(response, 'Buscar dirección en el mapa')
        self.assertContains(response, '/venta/api/direcciones/geocodificar/')
        self.assertContains(response, 'Ubicá un punto en el mapa antes de confirmar')
        self.assertContains(response, 'Confirmá la dirección para poder guardar la venta')
        self.assertContains(response, 'La fecha de entrega es obligatoria')
        self.assertContains(response, "fechaEntrega: ''")
        self.assertNotContains(response, 'Fecha de compra')
        self.assertNotContains(response, 'fechaCompra')

    def test_admin_muestra_repartos_como_aplicacion_con_acceso_al_mapa(self):
        from VentaStockManager.admin import admin_site

        request = RequestFactory().get('/admin/')
        request.user = self.admin
        apps = admin_site.get_app_list(request)
        reparto = next(app for app in apps if app['app_label'] == 'reparto')

        self.assertEqual(reparto['name'], 'Repartos')
        self.assertEqual(reparto['app_url'], '/reparto/')
        self.assertEqual(reparto['models'][0]['name'], 'Planificar reparto')
        self.assertEqual(reparto['models'][0]['admin_url'], '/reparto/planificar/')
        self.assertEqual(reparto['models'][1]['name'], 'Ver mapa')
        self.assertEqual(reparto['models'][1]['admin_url'], '/reparto/')

    def test_vendedor_no_ve_aplicacion_general_de_repartos(self):
        from VentaStockManager.admin import admin_site

        request = RequestFactory().get('/admin/')
        request.user = self.usuario_vendedor
        apps = admin_site.get_app_list(request)

        self.assertNotIn('reparto', {app['app_label'] for app in apps})

    def test_admin_asigna_solo_pedidos_seleccionados(self):
        direccion = DireccionCliente.objects.create(
            cliente=self.cliente,
            direccion_texto='Ruta 20 km 5',
            localidad='Córdoba',
            latitud='-31.430000',
            longitud='-64.210000',
            confirmada=True,
            es_principal=True,
        )
        pedido_elegido = self._crear_venta().pedido

        otro_cliente = Cliente.objects.create(
            nombre='Otro', apellido='Cliente', whatsapp_number='5493515550000',
        )
        otro_pedido = self._crear_venta(otro_cliente).pedido

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('reparto_planificar'),
            data={
                'fecha': str(date.today()),
                'asignacion': 'sin_asignar',
                'pedido': str(pedido_elegido.pk),
                'repartidor_id': str(self.repartidor.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('reparto_planificar')))
        pedido_elegido.refresh_from_db()
        otro_pedido.refresh_from_db()
        self.assertEqual(pedido_elegido.repartidor, self.repartidor)
        self.assertEqual(pedido_elegido.estado, Pedido.ASIGNADO)
        self.assertIsNone(otro_pedido.repartidor)
        self.assertTrue(
            PedidoEstadoHistorial.objects.filter(
                pedido=pedido_elegido,
                estado_nuevo=Pedido.ASIGNADO,
            ).exists()
        )

        mapa_admin = self.client.get(
            reverse('reparto_panel'),
            {'fecha': str(date.today())},
        )
        self.assertEqual(mapa_admin.status_code, 200)
        self.assertContains(mapa_admin, 'Mapa general de repartos')
        self.assertContains(mapa_admin, 'Cliente Con dirección')
        self.assertContains(mapa_admin, 'Otro Cliente')

    def test_vendedor_no_puede_asignar_pedidos(self):
        pedido = self._crear_venta().pedido
        self.client.force_login(self.usuario_vendedor)
        response = self.client.post(
            reverse('reparto_planificar'),
            data={
                'fecha': str(date.today()),
                'pedido': str(pedido.pk),
                'repartidor_id': str(self.repartidor.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)
        pedido.refresh_from_db()
        self.assertIsNone(pedido.repartidor)

    def test_planificacion_abre_en_hoy_y_no_esta_en_acciones_de_pedido(self):
        from VentaStockManager.admin import admin_site

        pedido_hoy = self._crear_venta().pedido
        pedido_manana = self._crear_venta(
            fecha_entrega=date.today() + timedelta(days=1),
        ).pedido
        self.client.force_login(self.admin)

        response = self.client.get(reverse('reparto_planificar'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['fecha'], date.today())
        self.assertContains(response, f'value="{date.today().isoformat()}"')
        self.assertContains(response, f'#{pedido_hoy.pk}')
        self.assertNotContains(response, f'#{pedido_manana.pk}')
        request = RequestFactory().get('/admin/venta/pedido/')
        request.user = self.admin
        pedido_admin = admin_site._registry[Pedido]
        self.assertNotIn('asignar_repartidor', pedido_admin.get_actions(request))

    def test_planificacion_selecciona_todos_los_filtros_incluso_otras_paginas(self):
        User = get_user_model()
        usuario_vendedor_dos = User.objects.create_user(
            'vendedor_reparto_dos', password='x', is_staff=True,
        )
        vendedor_dos = Vendedor.objects.create(
            usuario=usuario_vendedor_dos,
            nombre='Venta',
            apellido='Dos',
        )
        usuario_vendedor_tres = User.objects.create_user(
            'vendedor_reparto_tres', password='x', is_staff=True,
        )
        vendedor_tres = Vendedor.objects.create(
            usuario=usuario_vendedor_tres,
            nombre='Venta',
            apellido='Tres',
        )
        pedidos_vendedor_uno = [
            self._crear_venta().pedido
            for _ in range(21)
        ]
        pedido_vendedor_dos = self._crear_venta(vendedor=vendedor_dos).pedido
        pedido_vendedor_tres = self._crear_venta(vendedor=vendedor_tres).pedido
        pedido_manana = self._crear_venta(
            fecha_entrega=date.today() + timedelta(days=1),
        ).pedido

        self.client.force_login(self.admin)
        filtros = {
            'fecha': str(date.today()),
            'vendedor': [str(self.vendedor.pk), str(vendedor_dos.pk)],
            'asignacion': 'sin_asignar',
        }
        primera_pagina = self.client.get(reverse('reparto_planificar'), filtros)

        self.assertEqual(primera_pagina.status_code, 200)
        self.assertEqual(primera_pagina.context['total_resultados'], 22)
        self.assertEqual(primera_pagina.context['pagina'].paginator.num_pages, 2)
        self.assertContains(
            primera_pagina,
            'Seleccionar los 20 pedidos de esta página',
        )
        self.assertContains(primera_pagina, 'Incluir también todas las páginas')
        self.assertContains(primera_pagina, 'Es opcional')

        asignar = self.client.post(reverse('reparto_planificar'), {
            **filtros,
            'seleccionar_todos': '1',
            'repartidor_id': str(self.repartidor.pk),
        })

        self.assertEqual(asignar.status_code, 302)
        self.assertEqual(
            Pedido.objects.filter(
                pk__in=[pedido.pk for pedido in pedidos_vendedor_uno],
                repartidor=self.repartidor,
            ).count(),
            21,
        )
        pedido_vendedor_dos.refresh_from_db()
        pedido_vendedor_tres.refresh_from_db()
        pedido_manana.refresh_from_db()
        self.assertEqual(pedido_vendedor_dos.repartidor, self.repartidor)
        self.assertIsNone(pedido_vendedor_tres.repartidor)
        self.assertIsNone(pedido_manana.repartidor)

    def test_repartidor_solo_actualiza_sus_pedidos(self):
        direccion = DireccionCliente.objects.create(
            cliente=self.cliente,
            direccion_texto='San Martín 500',
            localidad='Córdoba',
            latitud='-31.400000',
            longitud='-64.180000',
            confirmada=True,
            es_principal=True,
        )
        pedido = self._crear_venta().pedido
        pedido.repartidor = self.repartidor
        pedido.estado = Pedido.ASIGNADO
        pedido.save(update_fields=['repartidor', 'estado'])

        self.client.force_login(self.usuario_repartidor)
        panel = self.client.get(
            reverse('reparto_panel'),
            {'fecha': str(date.today())},
        )
        self.assertEqual(panel.status_code, 200)
        self.assertContains(panel, 'Cliente Con dirección')
        self.assertContains(panel, 'Ver pedido completo')
        self.assertContains(panel, self.articulo.nombre)
        self.assertContains(panel, 'Pendiente de cobro')
        self.assertContains(panel, 'Ordenar por cercanía')
        self.assertContains(panel, 'Más cercano')
        self.assertContains(panel, 'navigator.geolocation')
        self.assertContains(panel, 'data-pedido-id')

        response = self.client.post(
            reverse('reparto_actualizar_estado', args=[pedido.pk]),
            data=json.dumps({'estado': Pedido.ENTREGADO}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.ENTREGADO)
        self.assertIsNotNone(pedido.entregado_en)

        correccion = self.client.post(
            reverse('reparto_actualizar_estado', args=[pedido.pk]),
            data=json.dumps({'estado': Pedido.EN_REPARTO}),
            content_type='application/json',
        )
        self.assertEqual(correccion.status_code, 200)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.EN_REPARTO)
        self.assertIsNone(pedido.entregado_en)
        self.assertTrue(
            PedidoEstadoHistorial.objects.filter(
                pedido=pedido,
                estado_anterior=Pedido.ENTREGADO,
                estado_nuevo=Pedido.EN_REPARTO,
                usuario=self.usuario_repartidor,
            ).exists()
        )

        self.client.force_login(self.usuario_otro)
        prohibido = self.client.post(
            reverse('reparto_actualizar_estado', args=[pedido.pk]),
            data=json.dumps({'estado': Pedido.ENTREGADO}),
            content_type='application/json',
        )
        self.assertEqual(prohibido.status_code, 403)

    def test_no_entregado_requiere_motivo(self):
        pedido = self._crear_venta().pedido
        pedido.repartidor = self.repartidor
        pedido.estado = Pedido.ASIGNADO
        pedido.save(update_fields=['repartidor', 'estado'])
        self.client.force_login(self.usuario_repartidor)

        response = self.client.post(
            reverse('reparto_actualizar_estado', args=[pedido.pk]),
            data=json.dumps({'estado': Pedido.NO_ENTREGADO}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.ASIGNADO)

    def test_repartidor_puede_iniciar_y_cerrar_sesion(self):
        login = self.client.get(reverse('login'))
        self.assertEqual(login.status_code, 200)
        self.assertTemplateUsed(login, 'registration/login_admin.html')
        self.assertContains(login, 'id="layout-content"')
        self.assertContains(login, 'class="side-bar"')
        self.assertContains(login, 'login-bg-default')
        self.assertContains(login, 'material-icons prefix password-visible')
        self.assertContains(login, '>person<')
        self.assertContains(login, '>lock<')
        self.assertNotContains(login, 'arrow_forward')
        self.assertNotContains(login, 'content_copy')
        self.assertNotContains(login, 'Osvaldo Administrator - Precios')

        acceso_invalido = self.client.post(reverse('login'), {
            'username': self.usuario_repartidor.username,
            'password': 'incorrecta',
        })
        self.assertEqual(acceso_invalido.status_code, 200)
        self.assertContains(
            acceso_invalido,
            'El usuario o la contraseña no son correctos.',
        )

        acceso = self.client.post(reverse('login'), {
            'username': self.usuario_repartidor.username,
            'password': 'x',
        })
        self.assertRedirects(
            acceso,
            reverse('reparto_panel'),
            fetch_redirect_response=False,
        )

        self.client.logout()
        acceso_desde_admin = self.client.post(
            f"{reverse('login')}?next=/admin/",
            {
                'username': self.usuario_repartidor.username,
                'password': 'x',
                'next': '/admin/',
            },
        )
        self.assertRedirects(
            acceso_desde_admin,
            reverse('reparto_panel'),
            fetch_redirect_response=False,
        )

        salida = self.client.post(reverse('logout'))
        self.assertRedirects(
            salida,
            reverse('login'),
            fetch_redirect_response=False,
        )

        acceso_admin = self.client.post(reverse('login'), {
            'username': self.admin.username,
            'password': 'x',
        })
        self.assertRedirects(
            acceso_admin,
            reverse('admin:index'),
            fetch_redirect_response=False,
        )

        self.client.logout()
        acceso_admin_anterior = self.client.get('/admin/login/')
        self.assertRedirects(
            acceso_admin_anterior,
            reverse('login'),
            fetch_redirect_response=False,
        )

    def test_admin_crea_usuario_repartidor_desde_gestion(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('crear_usuario'), {
            'username': 'repartidor_nuevo',
            'password': 'clave-segura-123',
            'nombre': 'María',
            'apellido': 'Reparto',
            'telefono': '3515550000',
            'tipo': 'repartidor',
        })

        self.assertRedirects(
            response,
            reverse('gestion_usuarios'),
            fetch_redirect_response=False,
        )
        usuario = get_user_model().objects.get(username='repartidor_nuevo')
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        self.assertEqual(usuario.repartidor.nombre, 'María Reparto')
        self.assertEqual(usuario.repartidor.telefono, '3515550000')

        listado = self.client.get(reverse('gestion_usuarios'))
        self.assertContains(listado, 'repartidor_nuevo')
        self.assertContains(listado, 'Repartidor')

    def test_gestion_usuarios_ofrece_un_boton_directo_por_rol(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('gestion_usuarios'),
            {'crear': 'repartidor'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Crear vendedor')
        self.assertContains(response, 'Crear repartidor')
        self.assertContains(response, 'Crear administrador')
        self.assertContains(response, 'formAbierto: true')
        self.assertContains(response, "tipo: 'repartidor'")
        self.assertNotContains(response, 'type="radio" name="tipo"')
        self.assertContains(
            response,
            ":type=\"mostrarPasswordCreacion ? 'text' : 'password'\"",
        )
        self.assertContains(
            response,
            ":type=\"mostrarPasswordReset ? 'text' : 'password'\"",
        )

    def test_panel_admin_reemplaza_user_clasico_por_accesos_de_rol(self):
        from VentaStockManager.admin import admin_site

        request = RequestFactory().get('/admin/')
        request.user = self.admin
        apps = admin_site.get_app_list(request)
        app_usuarios = next(app for app in apps if app['app_label'] == 'auth')
        nombres = [modelo['name'] for modelo in app_usuarios['models']]

        self.assertEqual(app_usuarios['name'], 'Usuarios')
        self.assertEqual(nombres, [
            'Crear vendedor',
            'Crear repartidor',
            'Crear administrador',
            'Gestionar usuarios existentes',
        ])
        self.assertNotIn('Users', nombres)

    def test_crear_usuario_configura_vendedor_y_administrador_en_un_paso(self):
        self.client.force_login(self.admin)
        response_vendedor = self.client.post(reverse('crear_usuario'), {
            'username': 'vendedor_directo',
            'password': 'clave-segura-123',
            'nombre': 'Venta',
            'apellido': 'Directa',
            'tipo': 'vendedor',
        })
        response_admin = self.client.post(reverse('crear_usuario'), {
            'username': 'admin_directo',
            'password': 'clave-segura-123',
            'nombre': 'Admin',
            'apellido': 'Directo',
            'tipo': 'superuser',
        })

        self.assertEqual(response_vendedor.status_code, 302)
        self.assertEqual(response_admin.status_code, 302)
        vendedor = get_user_model().objects.get(username='vendedor_directo')
        administrador = get_user_model().objects.get(username='admin_directo')
        self.assertTrue(vendedor.is_staff)
        self.assertFalse(vendedor.is_superuser)
        self.assertEqual(vendedor.vendedor.nombre, 'Venta')
        self.assertTrue(administrador.is_staff)
        self.assertTrue(administrador.is_superuser)
        self.assertFalse(Vendedor.objects.filter(usuario=administrador).exists())
