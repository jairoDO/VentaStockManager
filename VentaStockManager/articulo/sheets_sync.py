"""
Sync de escritura hacia Google Sheets.

Pareja del sync de lectura que vive en `articulo/task.py`. La lectura
(Sheets → DB) ya estaba implementada; este módulo agrega la parte de
delete (DB → Sheets) para cerrar el ciclo: cuando alguien borra un
Artículo en Django, también desaparece del Sheet.

Diseño:
  - Vaciamos la fila en vez de eliminarla. Eliminar shiftea todas las
    filas siguientes y, si hubiera referencias por número, se rompen.
    Vaciar es un no-op para todo lo demás y el próximo sync de lectura
    simplemente saltea la fila vacía.
  - El service account necesita permisos de Editor (no solo Viewer)
    en el Sheet. Si solo tiene Viewer, esta función va a devolver
    `{'ok': False, 'error': '...403...'}` — eso queda en logs pero
    no rompe el delete en Django.
  - Toda función devuelve un dict en vez de lanzar excepción. Así el
    caller (un signal handler / task asíncrona) puede decidir si
    loguear o reintentar, sin matar el resto del flujo.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

# Scope read-write. Lo separamos del scope readonly de `task.py` para
# limitar el blast radius si las credenciales se filtran en un
# entorno donde solo necesitamos leer.
SCOPES_WRITE = ['https://www.googleapis.com/auth/spreadsheets']


def _get_sheets_service():
    """Cliente de la API de Sheets con scope de escritura."""
    creds_path = getattr(settings, 'GOOGLE_CREDENTIALS_PATH', None)
    if not creds_path or not os.path.exists(creds_path):
        raise FileNotFoundError(
            f'GOOGLE_CREDENTIALS_PATH inválido: {creds_path!r}'
        )
    credentials = service_account.Credentials.from_service_account_file(
        creds_path, scopes=SCOPES_WRITE,
    )
    return build('sheets', 'v4', credentials=credentials)


def vaciar_fila_articulo(codigo_interno: str) -> dict:
    """
    Busca un artículo en la planilla por `codigo_interno` (columna B,
    según `task.py:procesar_archivo_xlsx`) y vacía su fila completa.

    Devuelve:
      - `{'ok': True, 'row': N}` cuando vació la fila N.
      - `{'ok': True, 'row': None}` cuando no encontró el artículo
        (caso normal si nunca llegó a sincronizarse).
      - `{'ok': False, 'error': msg}` ante cualquier falla.
    """
    if not codigo_interno:
        return {'ok': False, 'error': 'codigo_interno vacío'}

    try:
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        result = sheet.values().get(
            spreadsheetId=settings.GOOGLE_SHEET_ID,
            range=settings.GOOGLE_SHEET_RANGE,
        ).execute()
    except FileNotFoundError as exc:
        return {'ok': False, 'error': f'credenciales: {exc}'}
    except HttpError as exc:
        return {'ok': False, 'error': f'sheets api: {exc}'}
    except Exception as exc:
        return {'ok': False, 'error': f'inesperado: {exc}'}

    values = result.get('values', [])
    if not values:
        return {'ok': True, 'row': None}

    # Buscar la fila por codigo_interno (columna B = índice 1).
    fila_match = None
    for i, row in enumerate(values):
        if len(row) > 1 and str(row[1]).strip() == str(codigo_interno).strip():
            # i es 0-indexed sobre values[]. La planilla es 1-indexed
            # y `GOOGLE_SHEET_RANGE` ("articulos!A1:Z1500") arranca
            # en A1, así que la fila real es i + 1.
            fila_match = i + 1
            break

    if fila_match is None:
        return {'ok': True, 'row': None}

    range_a_vaciar = f'articulos!A{fila_match}:Z{fila_match}'
    try:
        sheet.values().clear(
            spreadsheetId=settings.GOOGLE_SHEET_ID,
            range=range_a_vaciar,
            body={},
        ).execute()
    except HttpError as exc:
        return {'ok': False, 'error': f'clear falló: {exc}'}

    log.info('Sheets: artículo %s vaciado en fila %d', codigo_interno, fila_match)
    return {'ok': True, 'row': fila_match}
