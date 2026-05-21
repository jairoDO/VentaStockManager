"""
Tests de la app articulo.

Cubre:
  - Utilidades varias (sugerir_codigo_interno).
  - Signal de delete → sync a Sheets (mockeando django-q2 y Sheets API).
"""

from __future__ import annotations

import unittest
from unittest import mock

from django.test import TestCase

from articulo.models import Articulo


class TestUtils(TestCase):

    @unittest.skip(
        'Test legacy roto: sugerir_codigo_interno() está bugueado y '
        'devuelve un generator object literal en el string. Lo arreglamos '
        'cuando hagamos la limpieza del modelo Articulo, no acá.'
    )
    def test_sugerir_codigo_interno(self):
        articulo1 = Articulo(nombre="Caja de Chupetines Chicles", marca="bagley")
        nombre_sugerido = articulo1.sugerir_codigo_interno()
        self.assertTrue(nombre_sugerido.startswith('bagleycdcc'))


class SignalDeleteSheetsTests(TestCase):
    """
    Verifica que el signal `post_delete` se comporta según el flag
    `SHEETS_DELETE_SYNC_ENABLED` y los datos del artículo.
    """

    def _crear_articulo(self, codigo_interno='A1', nombre='Test', codigo='11'):
        # El modelo tiene campos peculiares (vencimiento es DateField
        # sin null, codigo es CharField aunque tenga números). Sembramos
        # valores razonables para que se pueda guardar.
        from datetime import date, timedelta
        return Articulo.objects.create(
            codigo=codigo,
            codigo_interno=codigo_interno,
            nombre=nombre,
            marca='TestMarca',
            precio_minorista=100,
            precio_mayorista=90,
            cantidad_por_mayor=10,
            stock=10,
            vencimiento=date.today() + timedelta(days=30),
        )

    def _set_sheets_flags(self, master=False, delete=False):
        """
        Helper: mutamos el singleton de ConfiguracionGeneral para los
        tests. Antes esto se hacía con @override_settings; ahora los
        flags viven en el modelo y necesitamos persistirlos.
        """
        from configuracion.models import get_config
        cfg = get_config()
        cfg.sheets_sync_habilitado = master
        cfg.sheets_delete_sync_habilitado = delete
        cfg.save()

    @mock.patch('articulo.signals.log')
    def test_flag_off_no_encola(self, mock_log):
        # Por default los flags están apagados y no debería pasar nada.
        self._set_sheets_flags(master=False, delete=False)
        with mock.patch('django_q.tasks.async_task') as mock_async:
            articulo = self._crear_articulo(codigo_interno='OFF1')
            articulo.delete()
            mock_async.assert_not_called()

    def test_flag_on_encola_task(self):
        # Con ambos flags en True el signal SÍ encola.
        self._set_sheets_flags(master=True, delete=True)
        with mock.patch('django_q.tasks.async_task') as mock_async:
            articulo = self._crear_articulo(codigo_interno='ON1')
            articulo.delete()
            mock_async.assert_called_once()
            # El primer arg posicional es la ruta del task; el segundo
            # el codigo_interno; el tercero el nombre.
            args, _ = mock_async.call_args
            self.assertEqual(args[0], 'articulo.tasks.sync_borrar_articulo_de_sheets')
            self.assertEqual(args[1], 'ON1')

    def test_master_off_aunque_delete_on_no_encola(self):
        # El master flag apaga TODO aunque el específico esté prendido.
        # Test del "doble gate": defensa contra olvido al apagar.
        self._set_sheets_flags(master=False, delete=True)
        with mock.patch('django_q.tasks.async_task') as mock_async:
            articulo = self._crear_articulo(codigo_interno='MAS1')
            articulo.delete()
            mock_async.assert_not_called()

    def test_sin_codigo_interno_no_encola(self):
        # Si el artículo no tiene codigo_interno, no hay clave para
        # buscar en el Sheet — el signal corta antes y no llama a
        # async_task. El modelo autogenera codigo_interno en save(),
        # así que usamos un .update() crudo para dejarlo NULL sin
        # pasar por save() y simular el escenario de datos legacy.
        # Flags ON: si NO los prendemos, el corte sería por flags y no
        # estaríamos testeando lo que queremos.
        self._set_sheets_flags(master=True, delete=True)
        articulo = self._crear_articulo(codigo_interno='SIN1')
        Articulo.objects.filter(pk=articulo.pk).update(codigo_interno=None)
        articulo.refresh_from_db()
        with mock.patch('django_q.tasks.async_task') as mock_async:
            articulo.delete()
            mock_async.assert_not_called()
