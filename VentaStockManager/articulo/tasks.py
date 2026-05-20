"""
Tasks asíncronas de la app articulo.

Mantengo este archivo separado de `task.py` (singular, ya existe con
las funciones de sync de lectura) porque django-q2 referencia las
tareas por su path importable (`articulo.tasks.foo`) y mezclarlas con
las funciones legacy mezcla scopes:
  - `task.py` (singular, legacy) = sync de LECTURA desde Sheets.
  - `tasks.py` (plural, este) = jobs que corren en django-q2.

A futuro probablemente se unifiquen pero hoy preferimos no romper el
sync existente.
"""

from __future__ import annotations

import logging

from django.core.management import call_command

from articulo.sheets_sync import vaciar_fila_articulo

log = logging.getLogger(__name__)


def sync_borrar_articulo_de_sheets(codigo_interno: str, articulo_nombre: str = '') -> dict:
    """
    Task encolada por el signal `post_delete` de Articulo.

    Vacía la fila correspondiente en Google Sheets. Maneja errores
    devolviendo un dict (no lanza), así django-q2 lo guarda como
    resultado y podemos auditar si algo falló sin dramatismo.

    Args:
        codigo_interno: clave de búsqueda en la columna B del Sheet.
        articulo_nombre: solo para logging — facilita la lectura del
            audit log cuando hay que investigar "qué se borró ayer".
    """
    log.info(
        'sync delete a Sheets: codigo_interno=%s nombre=%r',
        codigo_interno, articulo_nombre,
    )
    resultado = vaciar_fila_articulo(codigo_interno)
    if not resultado.get('ok'):
        log.error(
            'falló sync delete para %s: %s',
            codigo_interno, resultado.get('error'),
        )
    elif resultado.get('row'):
        log.info(
            'sync delete OK: %s vaciado en fila %d',
            codigo_interno, resultado['row'],
        )
    else:
        log.info(
            'sync delete NOP: %s no estaba en el Sheet (nada para hacer)',
            codigo_interno,
        )
    return resultado


def aplicar_reglas_categoria_scheduled() -> str:
    """
    Wrapper para el panel de tareas / django-q2 Schedule.

    Aplica las reglas de categoría sobre los artículos sin clasificar.
    No fuerza pisar las que ya tienen categoría (eso requiere el
    flag --forzar explícito, no lo exponemos por panel).
    """
    log.info('Schedule: aplicar_reglas_categoria arranca')
    try:
        call_command('aplicar_reglas_categoria')
    except Exception as exc:
        log.exception('aplicar_reglas_categoria falló: %s', exc)
        raise
    return 'aplicar_reglas_categoria OK'
