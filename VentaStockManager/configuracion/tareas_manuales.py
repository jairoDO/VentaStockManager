"""
Catálogo de tareas que se pueden disparar a mano desde el panel del
admin (`/configuracion/panel-tareas/`).

Cada entry describe UNA tarea ejecutable. Las "tareas manuales" son
en realidad las MISMAS funciones que dispara django-q2 por cron —
acá solo las exponemos con metadata para que el operador pueda
correrlas sin esperar el schedule (o sin esperar a que se acuerde
de prenderlas).

Cuándo es útil:
  - Acabás de cambiar la retención de meses y querés ver el efecto
    ya, sin esperar al próximo domingo.
  - El sync de Sheets falló y querés reintentarlo manualmente.
  - Necesitás demostrarle a Osvaldo cómo funciona algo en vivo.

Agregar una tarea nueva es solo agregar una entrada acá — la vista
los renderiza automáticamente.
"""

from __future__ import annotations

from typing import TypedDict


class Tarea(TypedDict):
    id: str
    titulo: str
    descripcion: str
    func_path: str
    icono: str


CATALOGO_TAREAS: list[Tarea] = [
    {
        'id': 'archivar_ventas',
        'titulo': 'Archivar ventas antiguas',
        'descripcion': (
            'Marca como archivadas las ventas con más de N meses (donde N '
            'se configura en "Configuración general"). No borra nada: las '
            'ventas quedan ocultas del listado normal del admin pero '
            'siguen consultables con el filtro "Archivadas".'
        ),
        'func_path': 'venta.tasks.archivar_ventas_antiguas_scheduled',
        'icono': '🗂️',
    },
    {
        'id': 'sync_articulos',
        'titulo': 'Sincronizar artículos desde Google Sheets',
        'descripcion': (
            'Descarga la planilla de Google Sheets y actualiza '
            'precios/stock de los artículos en la DB. Útil después de '
            'que Osvaldo modificó precios en la planilla y querés '
            'reflejarlos sin esperar al próximo cron.'
        ),
        'func_path': 'articulo.task.actualizar_precios_articulos_desde_drive',
        'icono': '📊',
    },
    {
        'id': 'aplicar_reglas_categoria',
        'titulo': 'Aplicar reglas de categoría',
        'descripcion': (
            'Recorre los artículos sin categoría asignada y les pone '
            'una según las reglas configuradas (matching del nombre '
            'contra las palabras clave). NO toca los que ya tienen '
            'categoría. Útil después de cargar artículos nuevos desde '
            'Sheets o de agregar palabras clave a una regla.'
        ),
        'func_path': 'articulo.tasks.aplicar_reglas_categoria_scheduled',
        'icono': '🏷️',
    },
]


def buscar_tarea(tarea_id: str) -> Tarea | None:
    """Busca una tarea por id en el catálogo, o None si no existe."""
    return next((t for t in CATALOGO_TAREAS if t['id'] == tarea_id), None)
