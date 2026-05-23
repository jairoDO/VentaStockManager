"""
Categoriza artículos en la DB usando la estructura del Google Sheet
compartido del operador.

Por qué este comando existe:
  Al hacer cutover a la DB nueva (vía dump), los IDs cambian y el
  sync de Sheets matchea por `codigo_interno`/`codigo`. Pero las
  CATEGORÍAS no viajan en el dump si fueron creadas en la nueva DB
  con nombres distintos a los del Sheet, o si el dump no tenía
  el campo `categoria` poblado para los artículos.

  Este comando NO trae artículos del Sheet (eso ya lo hace
  `actualizar_precios_articulos_desde_drive`). SOLO usa la
  estructura visual del Sheet (las filas en negrita son headers
  de categoría → las filas debajo son artículos de esa categoría)
  para SETEAR el campo `categoria` de los artículos que ya están
  en la DB.

Estrategia:
  - Conecta al Sheet con las MISMAS credenciales que el sync existente
    (`settings.GOOGLE_CREDENTIALS_PATH` + `GOOGLE_SHEET_ID`).
  - Usa `spreadsheets().get(includeGridData=True)` (no `values().get()`)
    para tener acceso al formato de cada celda (negrita, etc.).
  - Recorre filas: si la celda A está bold + B/D vacíos → header.
    Sino → artículo: buscar en DB por `codigo` (col B), setear
    `categoria` a la última vista.
  - Idempotente: solo updatea si la categoría difiere de la actual.

Flags:
  --dry-run        no escribe, solo cuenta.
  --forzar         pisa categorías ya asignadas (default: solo
                   completa las que están en NULL).
  --sheet-id ID    override del Sheet (default: settings.GOOGLE_SHEET_ID).
  --verbose        print por cada categoría/artículo procesado.
"""
from __future__ import annotations

import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from articulo.models import Articulo, Categoria


# Solo lectura — minimizamos blast radius de las credenciales.
SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']


def _clean_categoria(s: str) -> str:
    """Igual que en cargar_lista_precios_xlsx — saca markdown / espacios."""
    s = re.sub(r'[*_]', '', str(s)).strip()
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def _login_sheets():
    """
    Cliente Sheets API con credenciales de service account.

    Lee el JSON manualmente y normaliza caracteres de control antes
    de parsearlo, porque Render guarda los Secret Files con line endings
    CRLF (Windows) y a veces BOM, lo que rompe el parser JSON estricto
    de Python ("Invalid control character at: line N").

    Pipeline:
      1. Leer archivo en utf-8
      2. Quitar BOM (\\ufeff) si lo trae
      3. Normalizar CRLF → LF
      4. json.loads + from_service_account_info
    """
    import json
    import os
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_path = getattr(settings, 'GOOGLE_CREDENTIALS_PATH', None)
    if not creds_path:
        raise CommandError(
            'GOOGLE_CREDENTIALS_PATH no está seteado en settings. '
            'No se puede conectar al Google Sheet.'
        )
    if not os.path.exists(creds_path):
        raise CommandError(
            f'GOOGLE_CREDENTIALS_PATH apunta a un archivo que no existe: {creds_path}. '
            f'En Render asegurate de que el Secret File está montado en esa ruta.'
        )

    try:
        with open(creds_path, 'r', encoding='utf-8') as f:
            raw = f.read()
    except Exception as e:
        raise CommandError(f'No se pudo leer el archivo de credenciales: {e}')

    # Saneamiento: BOM al principio + CRLF → LF.
    if raw.startswith('﻿'):
        raw = raw.lstrip('﻿')
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')

    # Parser tolerante: strict=False permite caracteres de control dentro
    # de strings JSON (típicamente \t, \v u otros que aparecen cuando el
    # archivo pasa por un editor en el browser). Esto es exactamente para
    # lo que existe la flag — perfectamente safe para credenciales SA.
    try:
        info = json.loads(raw, strict=False)
    except json.JSONDecodeError:
        # Último recurso: strip de TODOS los control chars 0x00-0x1F
        # excepto \t \n \r (que son whitespace JSON válido).
        import re
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
        try:
            info = json.loads(cleaned, strict=False)
        except json.JSONDecodeError as e:
            raise CommandError(
                f'El JSON de credenciales está corrupto incluso después '
                f'de limpiar control chars: {e}. Re-bajá las credenciales '
                f'de Google Cloud Console y subilas de nuevo al Secret File '
                f'de Render SIN editarlas en el browser.'
            )

    try:
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES,
        )
    except Exception as e:
        raise CommandError(
            f'Credenciales inválidas (JSON parseable pero estructura mal): {e}'
        )

    return build('sheets', 'v4', credentials=credentials)


def categorizar_desde_sheet(
    *,
    sheet_id: str | None = None,
    range_name: str | None = None,
    dry_run: bool = False,
    forzar: bool = False,
    verbose: bool = False,
    stdout=None,
) -> dict:
    """
    Función core — expuesta para que el panel de tareas la dispare
    también (sin pasar por el management command).

    Devuelve un dict con stats. Si stdout viene, escribe progreso.
    """
    def log(msg):
        if stdout is not None:
            stdout.write(msg)

    sheet_id = sheet_id or settings.GOOGLE_SHEET_ID
    range_name = range_name or settings.GOOGLE_SHEET_RANGE

    service = _login_sheets()
    # `get` con `includeGridData=True` nos da el formato de cada celda
    # (bold, etc.) — `values().get()` solo trae los valores planos.
    meta = service.spreadsheets().get(
        spreadsheetId=sheet_id,
        ranges=[range_name],
        includeGridData=True,
    ).execute()

    sheets_data = meta.get('sheets', [])
    if not sheets_data:
        raise RuntimeError(f'El Sheet {sheet_id} no tiene hojas accesibles.')
    data = sheets_data[0].get('data', [])
    if not data:
        raise RuntimeError('La hoja existe pero no tiene data en el rango.')
    row_data = data[0].get('rowData', [])

    # Cache de categorías por nombre (case-insensitive).
    cache_cats: dict[str, Categoria] = {
        c.nombre.lower(): c for c in Categoria.objects.all()
    }

    stats = {
        'rows_procesadas': 0,
        'categorias_detectadas': 0,
        'categorias_no_existen': set(),
        'articulos_asignados': 0,
        'articulos_reasignados': 0,
        'articulos_sin_cambios': 0,
        'articulos_no_encontrados_en_db': 0,
        'filas_sin_categoria_activa': 0,
    }

    categoria_actual: Categoria | None = None

    with transaction.atomic():
        for i, row in enumerate(row_data):
            values = row.get('values', [])
            if not values:
                continue
            stats['rows_procesadas'] += 1

            cell_a = values[0] if len(values) > 0 else {}
            cell_b = values[1] if len(values) > 1 else {}
            cell_d = values[3] if len(values) > 3 else {}

            val_a = (
                cell_a.get('formattedValue')
                or cell_a.get('effectiveValue', {}).get('stringValue')
                or ''
            )
            val_b = cell_b.get('formattedValue', '') if cell_b else ''
            val_d = cell_d.get('formattedValue', '') if cell_d else ''

            if not val_a:
                continue

            # Detectar header de categoría: bold + sin valor en B/D.
            bold = (
                cell_a.get('effectiveFormat', {})
                      .get('textFormat', {})
                      .get('bold', False)
            )
            if bold and not val_b and not val_d:
                nombre_cat = _clean_categoria(val_a)
                cat = cache_cats.get(nombre_cat.lower())
                if cat:
                    categoria_actual = cat
                    stats['categorias_detectadas'] += 1
                    if verbose:
                        log(f'  R{i+1:>4} CAT detected → {nombre_cat} (id={cat.id})')
                else:
                    # La categoría del Sheet NO existe en la DB. Marcamos
                    # como activa-vacía para que los artículos de abajo
                    # no se asignen accidentalmente a la categoría anterior.
                    categoria_actual = None
                    stats['categorias_no_existen'].add(nombre_cat)
                    if verbose:
                        log(f'  R{i+1:>4} CAT no existe en DB → {nombre_cat} (skip artículos hasta próxima cat)')
                continue

            # Articulo: necesita codigo (B) y precio (D) para considerar.
            if not val_b:
                continue
            if categoria_actual is None:
                stats['filas_sin_categoria_activa'] += 1
                continue

            codigo = str(val_b).strip()
            # Match por código. Puede haber duplicados (codigo no es
            # unique en DB), procesamos TODOS los que matchean.
            qs = Articulo.objects.filter(codigo=codigo)
            arts = list(qs)
            if not arts:
                stats['articulos_no_encontrados_en_db'] += 1
                if verbose:
                    log(f'  R{i+1:>4} NO-MATCH → cod={codigo} (no existe en DB)')
                continue

            for art in arts:
                if art.categoria_id == categoria_actual.id:
                    stats['articulos_sin_cambios'] += 1
                    continue
                if art.categoria_id is not None and not forzar:
                    stats['articulos_sin_cambios'] += 1
                    continue

                if art.categoria_id is None:
                    accion = 'ASGN'
                    stats['articulos_asignados'] += 1
                else:
                    accion = 'REAS'
                    stats['articulos_reasignados'] += 1

                if not dry_run:
                    art.categoria = categoria_actual
                    art.save(update_fields=['categoria'])

                if verbose:
                    log(f'  R{i+1:>4} {accion} → cod={codigo} → {categoria_actual.nombre}')

        if dry_run:
            transaction.set_rollback(True)

    # Convertir set a list para serializar bien en el reporte.
    stats['categorias_no_existen'] = sorted(stats['categorias_no_existen'])
    return stats


def categorizar_desde_sheet_scheduled() -> str:
    """
    Wrapper para django-q2 / panel de tareas. Devuelve un string de
    resumen porque así lo espera el panel para mostrarlo al operador.
    """
    try:
        stats = categorizar_desde_sheet(dry_run=False, forzar=False, verbose=False)
    except Exception as e:
        return f'❌ Error: {e}'

    msg = (
        f"✓ Procesadas {stats['rows_procesadas']} filas. "
        f"{stats['categorias_detectadas']} categorías detectadas. "
        f"Artículos: {stats['articulos_asignados']} asignados, "
        f"{stats['articulos_reasignados']} reasignados, "
        f"{stats['articulos_sin_cambios']} sin cambios, "
        f"{stats['articulos_no_encontrados_en_db']} no encontrados en DB. "
    )
    if stats['categorias_no_existen']:
        msg += (
            f"Categorías del Sheet que NO existen en DB "
            f"(crear primero o renombrar para que coincidan): "
            f"{', '.join(stats['categorias_no_existen'])}."
        )
    return msg


class Command(BaseCommand):
    help = (
        'Asigna categoría a los artículos en la DB según en qué sección '
        '(header bold) aparecen en el Google Sheet compartido.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--forzar', action='store_true',
            help='Reasigna incluso si el artículo ya tenía categoría.',
        )
        parser.add_argument(
            '--sheet-id', default=None,
            help='ID del Sheet a leer (default: settings.GOOGLE_SHEET_ID).',
        )
        parser.add_argument('--verbose', action='store_true')

    def handle(self, *args, **opts):
        self.stdout.write(self.style.WARNING(
            f'{"DRY RUN — " if opts["dry_run"] else ""}'
            f'Leyendo Sheet...'
        ))

        stats = categorizar_desde_sheet(
            sheet_id=opts['sheet_id'],
            dry_run=opts['dry_run'],
            forzar=opts['forzar'],
            verbose=opts['verbose'],
            stdout=self.stdout,
        )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('━━━ Resumen ━━━'))
        self.stdout.write(f'  Filas procesadas:               {stats["rows_procesadas"]}')
        self.stdout.write(f'  Categorías detectadas en Sheet: {stats["categorias_detectadas"]}')
        self.stdout.write(f'  Artículos asignados (sin cat):  {stats["articulos_asignados"]}')
        self.stdout.write(f'  Artículos reasignados:          {stats["articulos_reasignados"]}')
        self.stdout.write(f'  Artículos sin cambios:          {stats["articulos_sin_cambios"]}')
        self.stdout.write(f'  Artículos NO encontrados en DB: {stats["articulos_no_encontrados_en_db"]}')
        self.stdout.write(f'  Filas sin categoría activa:     {stats["filas_sin_categoria_activa"]}')
        if stats['categorias_no_existen']:
            self.stdout.write(self.style.WARNING(
                f'\n  ⚠ Categorías del Sheet que NO existen en DB:'
            ))
            for c in stats['categorias_no_existen']:
                self.stdout.write(f'    - {c}')
            self.stdout.write(self.style.WARNING(
                '    Creá esas categorías en la DB y volvé a correr el comando.'
            ))
