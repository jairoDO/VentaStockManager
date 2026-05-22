#!/usr/bin/env python3
"""
Recalcula el estado (✅ / ⚠️ / ❌) de cada sección de MANUAL_OPERADOR.md
según la fecha de "Última revisión" y la fecha de hoy.

Reglas:
  - < 3 meses: ✅ Actualizada
  - 3-6 meses: ⚠️ Verificar
  - > 6 meses: ❌ Desactualizada

Uso:
    python docs/check_docs_staleness.py            # solo reportar
    python docs/check_docs_staleness.py --fix      # también actualizar el .md

Recomendado: correrlo en pre-commit hook o en CI mensual.
"""
from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

DOC_PATH = Path(__file__).parent / 'MANUAL_OPERADOR.md'
HOY = date.today()


def estado_segun_fecha(fecha_str: str) -> tuple[str, int]:
    """Devuelve (emoji_label, dias_desde) para una fecha YYYY-MM-DD."""
    try:
        fecha = date.fromisoformat(fecha_str)
    except ValueError:
        return ('❓ Fecha inválida', -1)
    delta = (HOY - fecha).days
    if delta < 90:
        return ('✅ Actualizada', delta)
    if delta < 180:
        return ('⚠️ Verificar', delta)
    return ('❌ Desactualizada', delta)


def main() -> int:
    if not DOC_PATH.exists():
        print(f'ERROR: no existe {DOC_PATH}')
        return 1

    content = DOC_PATH.read_text(encoding='utf-8')
    fix_mode = '--fix' in sys.argv

    # Encontrar todas las fechas "Última revisión: YYYY-MM-DD" en secciones
    pattern_seccion = re.compile(
        r'## (\d+)\. ([^\n]+)\n\n> 📅 \*\*Última revisión\*\*: '
        r'(\d{4}-\d{2}-\d{2}) — (✅[^\n]*|⚠️[^\n]*|❌[^\n]*|❓[^\n]*)',
        re.MULTILINE,
    )

    cambios = []
    nuevo_content = content
    for m in pattern_seccion.finditer(content):
        n_sec, titulo, fecha_str, estado_actual = m.groups()
        estado_correcto, dias = estado_segun_fecha(fecha_str)
        if estado_actual.strip() != estado_correcto:
            cambios.append({
                'seccion': f'{n_sec}. {titulo}',
                'fecha': fecha_str,
                'dias': dias,
                'estado_actual': estado_actual,
                'estado_correcto': estado_correcto,
            })
            # Reemplazar SOLO el estado al final de esa línea
            old_line = m.group(0)
            new_line = old_line.rsplit(' — ', 1)[0] + f' — {estado_correcto}'
            nuevo_content = nuevo_content.replace(old_line, new_line, 1)

    # También chequear la tabla "Índice + estado" arriba del archivo
    pattern_tabla = re.compile(
        r'^\| (\d+) \| (\[[^\]]+\]\([^)]+\)) \| (\d{4}-\d{2}-\d{2}) '
        r'\| (✅[^|]*|⚠️[^|]*|❌[^|]*|❓[^|]*) \|',
        re.MULTILINE,
    )
    for m in pattern_tabla.finditer(content):
        n_sec, link, fecha_str, estado_actual = m.groups()
        estado_correcto, dias = estado_segun_fecha(fecha_str)
        if estado_actual.strip() != estado_correcto:
            old_row = m.group(0)
            new_row = f'| {n_sec} | {link} | {fecha_str} | {estado_correcto} |'
            nuevo_content = nuevo_content.replace(old_row, new_row, 1)

    # Reportar
    if not cambios:
        print(f'✅ Todas las secciones tienen el estado correcto al {HOY}.')
        return 0

    print(f'⚠️ Hay {len(cambios)} sección(es) con estado desactualizado:\n')
    for c in cambios:
        print(f'  • {c["seccion"]}')
        print(f'    Fecha: {c["fecha"]} ({c["dias"]} días)')
        print(f'    Actual: {c["estado_actual"].strip()}')
        print(f'    Debería: {c["estado_correcto"]}')
        print()

    if fix_mode:
        DOC_PATH.write_text(nuevo_content, encoding='utf-8')
        print(f'✅ Actualizado {DOC_PATH.name}.')
        return 0
    else:
        print('Para aplicar los cambios: python docs/check_docs_staleness.py --fix')
        return 1


if __name__ == '__main__':
    sys.exit(main())
