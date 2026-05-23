"""
Command idempotente para cargar la lista de precios "histórica" en
formato texto (copiada del Excel del operador).

Formato esperado del archivo (tab-separated):

    MASTICABLE(caramelo)            # ← línea SIN precio = categoría header
    [línea vacía]
    •Billiken yogur	A1		$4800   # ← articulo: nombre, código, precio
    .billiken frutal	A2		$4800
    ...
    GOMITASS                        # ← nueva categoría, los siguientes
    Goma fantasía billeken	B0	$6000  # articulos van en GOMITASS
    ...

Reglas de parseo:
  - Líneas vacías → skip.
  - Línea con un campo que matches `$\\d+` o `\\d+` puro → ARTICULO.
    Layout: nombre TAB código TAB[TAB...] precio.
  - Línea sin precio → CATEGORÍA header. Setea la "categoría actual"
    para los artículos que vengan después.
  - Líneas obvias de cabecera (`precio minorista`, `desde cuando es
    mayorista`, etc.) → skip.

Idempotencia:
  - Categoria: match por nombre case-insensitive. Si existe, se reusa.
  - Articulo: match por `codigo` AGRUPADO POR categoría. Mismo código
    en categorías distintas (ej. "M1" en almacén Y en cigarrillos) =
    dos artículos distintos.
  - Si el articulo existe y --update-precios está pasado, actualizamos
    precio_mayorista y nombre.

Uso:

    python manage.py cargar_lista_precios path/to/lista.txt
    python manage.py cargar_lista_precios path/to/lista.txt --dry-run
    python manage.py cargar_lista_precios path/to/lista.txt --update-precios
    python manage.py cargar_lista_precios path/to/lista.txt --minorista-igual-mayorista

NO toca artículos que NO estén en el archivo — para borrar artículos
viejos hacelo a mano desde el admin (la grilla soft-delete vendrá en
una versión futura).
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from articulo.models import Articulo, Categoria


# Fecha "centinela" para articulos sin vencimiento real. El campo
# `vencimiento` es NOT NULL a nivel DB (legado de cuando todos los
# productos eran perecederos). 2099-12-31 ≈ "nunca vence".
VENCIMIENTO_SENTINEL = date(2099, 12, 31)


# Frases que aparecen en líneas de header del Excel — no son
# categorías reales, las ignoramos.
HEADER_KEYWORDS = (
    'codigo interno',
    'precio minorista',
    'precio mayorista',
    'desde cuando es mayorista',
)


def _es_linea_header(texto: str) -> bool:
    """¿Es una línea de header del Excel original (no categoría)?"""
    low = texto.lower()
    return any(kw in low for kw in HEADER_KEYWORDS)


def _clean_categoria(s: str) -> str:
    """Limpia el nombre de categoría: saca `* _ . :`, espacios extra."""
    s = re.sub(r'[*_]', '', s).strip()
    s = re.sub(r'[\.\,\:]+$', '', s)
    s = re.sub(r'\s+', ' ', s)
    # "CIGARRILLOS" o "cigarrillos" da igual — guardamos title case
    # como se ve mejor en el admin.
    return s.strip()


def _clean_nombre(s: str) -> str:
    """Limpia el nombre del articulo: bullets, dots iniciales, espacios."""
    s = s.strip()
    # Bullets y puntos al principio (•, ., *)
    s = re.sub(r'^[•\.\*\s]+', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def _parse_precio(raw: str) -> Decimal | None:
    """
    Parse de precio robusto a los typos del Excel.

    Acepta: `$4800`, `$ 4000`, `S1200` (typo S por $), `4800`, `$18500`,
    `4500.50`, `4500,50`. Rechaza: `001`, `0000`, `00`, no-digits.
    """
    s = raw.strip()
    # Quitar prefijos de moneda y typos
    s = s.lstrip('$Ss').strip()
    # Espacios internos (ej. "$ 4000" después de split → " 4000")
    s = s.replace(' ', '')
    # Coma decimal a punto
    s = s.replace(',', '.')
    if not re.match(r'^\d+(\.\d+)?$', s):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if d <= 0:
        return None
    # Sanity check: precios > 10M no son realistas en este negocio.
    if d > Decimal('10000000'):
        return None
    return d


# Regex para identificar un campo que parece ser un PRECIO.
# Acepta los typos comunes ($ pegado, S typo, espacios).
RE_CAMPO_PRECIO = re.compile(r'^\$?\s*[Ss]?\s*\d[\d\.,\s]*$')


def _parse_linea(line: str) -> tuple | None:
    """
    Parsea una línea cruda. Retorna:
      - ('articulo', nombre, codigo, precio_decimal)
      - ('categoria', nombre)
      - None si la línea no se puede parsear / es header / vacía.
    """
    if not line.strip():
        return None

    parts = [p.strip() for p in line.split('\t') if p.strip()]
    if not parts:
        return None

    # ¿Hay un campo con pinta de precio?
    precio_idx = None
    for i, p in enumerate(parts):
        if i == 0:
            # El primer campo es siempre el nombre — un articulo cuyo
            # "nombre" es solo un número no tiene sentido.
            continue
        if RE_CAMPO_PRECIO.match(p):
            precio_idx = i
            break

    if precio_idx is None:
        # Línea sin precio → posible categoría.
        full = ' '.join(parts).strip()
        if _es_linea_header(full):
            return None
        if len(full) < 2:
            return None
        return ('categoria', _clean_categoria(full))

    # Línea con precio → articulo.
    precio = _parse_precio(parts[precio_idx])
    if precio is None:
        return None

    # Código: campo justo antes del precio (si existe).
    codigo = parts[precio_idx - 1] if precio_idx >= 1 else ''
    # Si el "código" es muy largo (>20 chars), probablemente NO es código
    # sino parte del nombre — descartamos.
    if len(codigo) > 20 or RE_CAMPO_PRECIO.match(codigo):
        # No hay código real, todo lo previo es nombre.
        codigo = ''
        nombre = ' '.join(parts[:precio_idx])
    else:
        # Nombre: todos los campos anteriores al código.
        if precio_idx >= 2:
            nombre = ' '.join(parts[:precio_idx - 1])
        else:
            nombre = parts[0]

    nombre = _clean_nombre(nombre)
    if not nombre or len(nombre) < 2:
        return None

    return ('articulo', nombre, codigo, precio)


class Command(BaseCommand):
    help = (
        'Carga categorías + artículos desde un archivo de texto tab-separated. '
        'Idempotente: re-ejecutar no duplica.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'path',
            type=str,
            help='Ruta al archivo .txt con la lista (formato Excel pegado).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parsea y muestra qué haría, sin tocar la DB.',
        )
        parser.add_argument(
            '--update-precios',
            action='store_true',
            help='Si el articulo ya existe, ACTUALIZAR su precio_mayorista '
                 '(default: dejar como está).',
        )
        parser.add_argument(
            '--minorista-igual-mayorista',
            action='store_true',
            help='Al CREAR un articulo nuevo, copiar precio_mayorista también '
                 'a precio_minorista (default: minorista=NULL, lo completa el '
                 'operador después).',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Mostrar cada línea procesada.',
        )

    def handle(self, *args, **opts):
        path = Path(opts['path'])
        if not path.exists():
            raise CommandError(f'Archivo no encontrado: {path}')

        dry_run = opts['dry_run']
        update_precios = opts['update_precios']
        minorista_igual = opts['minorista_igual_mayorista']
        verbose = opts['verbose']

        stats = {
            'cat_creadas': 0,
            'cat_existentes': 0,
            'art_creados': 0,
            'art_actualizados': 0,
            'art_sin_cambios': 0,
            'art_sin_categoria': 0,
            'skipped': 0,
        }

        # Lectura completa primero → si está mal el archivo, fallar
        # antes de tocar nada en DB.
        with path.open('r', encoding='utf-8') as f:
            lines = list(f)

        self.stdout.write(self.style.WARNING(
            f'{"DRY RUN — " if dry_run else ""}'
            f'Procesando {len(lines)} líneas de {path}...'
        ))

        # Cache de categorías por nombre normalizado para evitar 1 query/articulo.
        cache_categorias: dict[str, Categoria | None] = {}

        def _get_or_create_categoria(nombre: str) -> Categoria | None:
            """Match case-insensitive. None en dry-run si no existe."""
            key = nombre.lower().strip()
            if key in cache_categorias:
                return cache_categorias[key]
            existente = Categoria.objects.filter(nombre__iexact=nombre).first()
            if existente:
                stats['cat_existentes'] += 1
                cache_categorias[key] = existente
                return existente
            if dry_run:
                cache_categorias[key] = None
                stats['cat_creadas'] += 1
                return None
            nueva = Categoria.objects.create(nombre=nombre)
            stats['cat_creadas'] += 1
            cache_categorias[key] = nueva
            return nueva

        # Procesamos todo en una transacción para que un crash a la
        # mitad NO deje el DB en estado parcial.
        with transaction.atomic():
            categoria_actual: Categoria | None = None

            for line_no, raw in enumerate(lines, 1):
                parsed = _parse_linea(raw)
                if parsed is None:
                    if verbose and raw.strip():
                        self.stdout.write(f'  L{line_no:>4} SKIP  → {raw.strip()[:60]}')
                    stats['skipped'] += 1
                    continue

                if parsed[0] == 'categoria':
                    nombre_cat = parsed[1]
                    categoria_actual = _get_or_create_categoria(nombre_cat)
                    marcador = self.style.SUCCESS('CAT  ')
                    self.stdout.write(f'  L{line_no:>4} {marcador} → {nombre_cat}')
                    continue

                # parsed[0] == 'articulo'
                _, nombre_art, codigo, precio = parsed

                if categoria_actual is None and not dry_run:
                    # En real mode, si no hay categoría todavía, no creamos.
                    # En dry-run igual seguimos para que el operador vea
                    # qué hay sin categoría.
                    stats['art_sin_categoria'] += 1
                    if verbose:
                        self.stdout.write(self.style.WARNING(
                            f'  L{line_no:>4} SIN-CAT → {nombre_art[:40]} '
                            f'(saltado: no hay categoría todavía)'
                        ))
                    continue

                # Match por (codigo, categoria) si codigo está. Si no, por nombre.
                qs = Articulo.objects.all()
                if codigo:
                    qs = qs.filter(codigo=codigo)
                    if categoria_actual:
                        qs = qs.filter(categoria=categoria_actual)
                else:
                    qs = qs.filter(nombre__iexact=nombre_art)
                    if categoria_actual:
                        qs = qs.filter(categoria=categoria_actual)
                existente = qs.first()

                if existente:
                    cambios = []
                    if update_precios and existente.precio_mayorista != precio:
                        cambios.append('precio')
                        if not dry_run:
                            existente.precio_mayorista = precio
                    if existente.nombre != nombre_art:
                        cambios.append('nombre')
                        if not dry_run:
                            existente.nombre = nombre_art
                    if cambios:
                        if not dry_run:
                            existente.save(update_fields=[
                                f.replace('precio', 'precio_mayorista')
                                for f in cambios
                            ])
                        stats['art_actualizados'] += 1
                        if verbose:
                            self.stdout.write(
                                f'  L{line_no:>4} UPD   → {nombre_art[:40]} '
                                f'[{", ".join(cambios)}]'
                            )
                    else:
                        stats['art_sin_cambios'] += 1
                else:
                    # Create.
                    if not dry_run:
                        Articulo.objects.create(
                            codigo=codigo or '',
                            nombre=nombre_art,
                            precio_mayorista=precio,
                            precio_minorista=precio if minorista_igual else None,
                            categoria=categoria_actual,
                            stock=0,
                            vencimiento=VENCIMIENTO_SENTINEL,
                            marca='Generico',
                        )
                    stats['art_creados'] += 1
                    if verbose:
                        self.stdout.write(
                            f'  L{line_no:>4} NEW   → {nombre_art[:40]} '
                            f'(cod={codigo or "—"}, ${precio})'
                        )

            if dry_run:
                # Aborto la transacción explícitamente.
                self.stdout.write(self.style.WARNING(
                    '\n⚠ DRY RUN — abortando transacción, no se guardó nada.'
                ))
                transaction.set_rollback(True)

        # Resumen.
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('━━━ Resumen ━━━'))
        self.stdout.write(f'  Categorías creadas:     {stats["cat_creadas"]}')
        self.stdout.write(f'  Categorías existentes:  {stats["cat_existentes"]}')
        self.stdout.write(f'  Articulos creados:      {stats["art_creados"]}')
        self.stdout.write(f'  Articulos actualizados: {stats["art_actualizados"]}')
        self.stdout.write(f'  Articulos sin cambios:  {stats["art_sin_cambios"]}')
        if stats['art_sin_categoria']:
            self.stdout.write(self.style.WARNING(
                f'  Articulos sin categoría (saltados): {stats["art_sin_categoria"]}'
            ))
        self.stdout.write(f'  Líneas skipeadas:       {stats["skipped"]}')
