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
            'precios/stock de los artículos en la DB. El sync se '
            'prende/apaga desde el admin: '
            '/admin/configuracion/configuraciongeneral/ → "Sheets sync '
            'habilitado". Si está apagado, ejecutar esta tarea devuelve '
            'un mensaje "sync desactivado" sin hacer nada.'
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
    {
        'id': 'recordatorios_saldo',
        'titulo': 'Recordatorios de saldo deudor',
        'descripcion': (
            'Manda WhatsApp a clientes con saldo deudor + sin compras '
            'recientes, según la configuración en '
            '/admin/configuracion/configuraciongeneral/. Respeta la '
            'frecuencia (no spamea: si ya le mandamos al mismo cliente '
            'en los últimos N días, lo salta). Si el master switch está '
            'apagado, devuelve NO-OP. Útil para correrlo a mano después '
            'de cambiar la config o forzar antes del fin de mes.'
        ),
        'func_path': 'cliente.tasks_recordatorios.recordatorios_saldo_scheduled',
        'icono': '💸',
    },
    {
        'id': 'backfill_whatsapp_number',
        'titulo': 'Completar WhatsApp desde teléfono',
        'descripcion': (
            'Recorre los clientes con `whatsapp_number` vacío e intenta '
            'derivarlo desde el campo `telefono` (formato AR). Idempotente: '
            'solo completa vacíos, nunca pisa lo cargado a mano. NO cambia '
            'el opt-in (puede_recibir_whatsapp). Útil si en la pantalla '
            '"Difundir" no aparecen clientes que sí tienen teléfono.'
        ),
        'func_path': 'cliente.tasks.backfill_whatsapp_number_scheduled',
        'icono': '📱',
    },
]


def buscar_tarea(tarea_id: str) -> Tarea | None:
    """Busca una tarea por id en el catálogo, o None si no existe."""
    return next((t for t in CATALOGO_TAREAS if t['id'] == tarea_id), None)
