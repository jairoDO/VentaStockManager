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


def _normalizar_nombre(s: str) -> str:
    """
    Normaliza un nombre para matching tolerante:
    - lowercase
    - sin bullets iniciales (•, *, .)
    - sin tildes
    - whitespace colapsado a un solo espacio
    """
    import unicodedata
    s = (s or '').strip().lower()
    s = re.sub(r'^[•\.\*\s]+', '', s)
    s = re.sub(r'\s+', ' ', s)
    s = ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )
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
        # Leer como BYTES primero — así vemos exactamente qué hay sin que
        # Python decodifique caracteres en silencio. Después decodificamos
        # con errors='replace' para que cualquier byte raro se vea como
        # un placeholder en lugar de explotar.
        with open(creds_path, 'rb') as f:
            raw_bytes = f.read()
    except Exception as e:
        raise CommandError(f'No se pudo leer el archivo de credenciales: {e}')

    # Strip de BOM UTF-8 / UTF-16 LE/BE.
    for bom in (b'\xef\xbb\xbf', b'\xff\xfe', b'\xfe\xff'):
        if raw_bytes.startswith(bom):
            raw_bytes = raw_bytes[len(bom):]
            break

    raw = raw_bytes.decode('utf-8', errors='replace')

    # Normalizar todo line ending a \n y stripear chars zero-width Unicode
    # comunes que el browser de Render a veces inyecta al pegar (zero-width
    # space, zero-width joiner, etc.).
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    for zw in ('​', '‌', '‍', '⁠', '﻿'):
        raw = raw.replace(zw, '')

    # Parser tolerante: strict=False permite control chars dentro de strings.
    try:
        info = json.loads(raw, strict=False)
    except json.JSONDecodeError:
        # Último recurso: strip de TODOS los control chars 0x00-0x1F
        # excepto \t \n \r (whitespace JSON-válido). Después de esto,
        # cualquier control char "loose" entre tokens también desaparece.
        import re
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)
        try:
            info = json.loads(cleaned, strict=False)
        except json.JSONDecodeError as e:
            # Diagnóstico extra: imprimir un dump hex de los primeros 50
            # bytes del archivo para que veamos qué hay.
            primeros = raw_bytes[:60]
            hex_dump = ' '.join(f'{b:02x}' for b in primeros)
            preview = primeros.decode('utf-8', errors='replace')
            raise CommandError(
                f'El JSON de credenciales está corrupto incluso después '
                f'de cleanup agresivo: {e}.\n\n'
                f'Primeros 60 bytes del archivo (hex):\n  {hex_dump}\n\n'
                f'Como texto (errores reemplazados):\n  {preview!r}\n\n'
                f'Acción: re-bajá el JSON original de Google Cloud Console '
                f'(Service Accounts → Keys → Add key → JSON) y en Render '
                f'Dashboard → Environment → Secret Files, BORRÁ el archivo '
                f'actual y subilo de nuevo desde el botón "Upload" (NO '
                f'pegues el contenido en el textarea del browser, que es '
                f'lo que rompe el formato).'
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

    # Pre-cache de TODOS los artículos en memoria — vamos a hacer fallback
    # match por nombre normalizado cuando el codigo del Sheet no matchea
    # ningún codigo de la DB (típico cuando el dump tiene codigos numéricos
    # internos como '548' y el Sheet tiene etiquetas humanas como 'H44').
    # Para ~5000 artículos esto entra cómodo en memoria.
    from collections import defaultdict
    cache_por_nombre: dict[str, list[Articulo]] = defaultdict(list)
    cache_por_codigo: dict[str, list[Articulo]] = defaultdict(list)
    for art in Articulo.objects.all().only('id', 'nombre', 'codigo', 'categoria_id'):
        key_n = _normalizar_nombre(art.nombre)
        if key_n:
            cache_por_nombre[key_n].append(art)
        if art.codigo:
            cache_por_codigo[art.codigo.strip()].append(art)

    stats = {
        'rows_procesadas': 0,
        'categorias_detectadas': 0,
        'categorias_no_existen': set(),
        'articulos_asignados': 0,
        'articulos_reasignados': 0,
        'articulos_sin_cambios': 0,
        'articulos_no_encontrados_en_db': 0,
        'articulos_matched_por_codigo': 0,
        'articulos_matched_por_nombre': 0,
        'articulos_ambiguos_por_nombre': 0,
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
            nombre_sheet = str(val_a or '').strip()
            nombre_norm = _normalizar_nombre(nombre_sheet)

            # Matching estratégico:
            #   1) Por codigo en DB (rápido y exacto)
            #   2) Si no hay match, fallback por nombre normalizado.
            #      Útil cuando el dump tiene codigos numéricos internos
            #      (548, 549, ...) distintos a los del Sheet (A1, H44, ...).
            #   3) Si el nombre matchea N>1 artículos (ambigüedad), skip
            #      y reportar — no asignamos al azar.
            arts = list(cache_por_codigo.get(codigo, []))
            metodo = 'codigo' if arts else None

            if not arts and nombre_norm:
                candidatos = cache_por_nombre.get(nombre_norm, [])
                if len(candidatos) == 1:
                    arts = candidatos
                    metodo = 'nombre'
                elif len(candidatos) > 1:
                    stats['articulos_ambiguos_por_nombre'] += 1
                    if verbose:
                        log(
                            f'  R{i+1:>4} AMBIGUO → {nombre_sheet[:40]!r} '
                            f'(cod={codigo}, {len(candidatos)} artículos en DB '
                            f'con ese nombre — skip)'
                        )
                    continue

            if not arts:
                stats['articulos_no_encontrados_en_db'] += 1
                if verbose:
                    log(
                        f'  R{i+1:>4} NO-MATCH → cod={codigo} '
                        f'nombre={nombre_sheet[:40]!r} (no existe en DB)'
                    )
                continue

            if metodo == 'codigo':
                stats['articulos_matched_por_codigo'] += 1
            elif metodo == 'nombre':
                stats['articulos_matched_por_nombre'] += 1

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
                    # `art` viene del cache (solo tiene id/nombre/codigo/
                    # categoria_id por el .only()). Usamos update() puntual
                    # en lugar de art.save() para NO disparar signals.
                    Articulo.objects.filter(pk=art.id).update(
                        categoria=categoria_actual,
                    )
                    art.categoria_id = categoria_actual.id  # mantener cache fresco

                if verbose:
                    log(
                        f'  R{i+1:>4} {accion} [{metodo}] → '
                        f'cod={codigo} "{nombre_sheet[:35]}" → {categoria_actual.nombre}'
                    )

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
        f"Match: {stats['articulos_matched_por_codigo']} por código, "
        f"{stats['articulos_matched_por_nombre']} por nombre. "
        f"Artículos: {stats['articulos_asignados']} asignados, "
        f"{stats['articulos_reasignados']} reasignados, "
        f"{stats['articulos_sin_cambios']} sin cambios, "
        f"{stats['articulos_no_encontrados_en_db']} no encontrados en DB"
    )
    if stats['articulos_ambiguos_por_nombre']:
        msg += f", {stats['articulos_ambiguos_por_nombre']} ambiguos por nombre"
    msg += ". "
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
        self.stdout.write(f'  Artículos matched por código:   {stats["articulos_matched_por_codigo"]}')
        self.stdout.write(f'  Artículos matched por nombre:   {stats["articulos_matched_por_nombre"]} (fallback)')
        self.stdout.write(f'  Artículos asignados (sin cat):  {stats["articulos_asignados"]}')
        self.stdout.write(f'  Artículos reasignados:          {stats["articulos_reasignados"]}')
        self.stdout.write(f'  Artículos sin cambios:          {stats["articulos_sin_cambios"]}')
        self.stdout.write(f'  Artículos NO encontrados en DB: {stats["articulos_no_encontrados_en_db"]}')
        if stats['articulos_ambiguos_por_nombre']:
            self.stdout.write(self.style.WARNING(
                f'  Artículos ambiguos por nombre:  {stats["articulos_ambiguos_por_nombre"]} '
                f'(hay >1 articulo en DB con ese nombre, no asignamos al azar)'
            ))
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
