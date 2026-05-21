"""
System check de Django para detectar el bug recurrente de comentarios
multi-línea en templates.

Django templates tienen DOS sintaxis para comentarios:

  {# texto #}                   ← UNA sola línea
  {% comment %}texto{% endcomment %}   ← multi-línea

Si usás `{# ... #}` con un salto de línea adentro, Django NO lo
interpreta como comentario y lo renderiza COMO TEXTO. Ese bug pasó
5 veces en este proyecto antes de poner este check (sí, 5).

Este check escanea todos los templates al arrancar Django y falla
ruidosamente si encuentra alguno roto. Pasa con:

  - `python manage.py runserver` → no arranca.
  - `python manage.py check` → exit 1.
  - El buildCommand de Render (`manage.py migrate`) → deploy aborta.
  - Cualquier test → fail.

O sea: deja de pasar inadvertido.

Si necesitás bypass de emergencia (NO recomendado), corré con
`SKIP_TEMPLATE_COMMENT_CHECK=1` en el environment.
"""

from __future__ import annotations

import os
import re

from django.conf import settings
from django.core import checks


# IDs únicos para los reportes del check (Django los usa en mensajes).
COMMENT_BUG_ID = 'configuracion.E001'


# Path de la carpeta del proyecto. Asumimos `BASE_DIR / VentaStockManager`
# pero también escaneamos cualquier `templates/` dentro de las apps.
def _candidate_template_dirs():
    """
    Devuelve la lista de directorios donde puede haber templates Django:
    BASE_DIR, settings.TEMPLATES[*]['DIRS'], y cada `<app>/templates/`.
    """
    base = getattr(settings, 'BASE_DIR', None)
    candidatos = set()

    if base:
        candidatos.add(str(base))

    for engine in (getattr(settings, 'TEMPLATES', None) or []):
        for d in engine.get('DIRS') or []:
            candidatos.add(str(d))

    # `INSTALLED_APPS` + buscar templates/ adentro de cada app.
    import django.apps
    for app_config in django.apps.apps.get_app_configs():
        candidatos.add(app_config.path)

    return [c for c in candidatos if c and os.path.isdir(c)]


# Compilado una vez: cualquier `{# ... #}` con un \n adentro.
_BROKEN_COMMENT_RE = re.compile(r'\{#([^#]*)#\}', re.DOTALL)


def _scan_file(path: str) -> list[tuple[int, str]]:
    """
    Devuelve [(line_number, preview), ...] para cada comentario roto.
    """
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            content = fh.read()
    except (OSError, UnicodeDecodeError):
        return []
    issues = []
    for m in _BROKEN_COMMENT_RE.finditer(content):
        inner = m.group(1)
        if '\n' in inner:
            line = content[: m.start()].count('\n') + 1
            preview = inner.strip().replace('\n', ' / ')[:80]
            issues.append((line, preview))
    return issues


@checks.register(checks.Tags.templates)
def check_no_broken_multiline_comments(app_configs, **kwargs):
    """
    Falla con `Error` (NO warning — error rompe `manage.py check`)
    si encuentra cualquier comentario `{# ... #}` multi-línea.

    Para bypass de emergencia: setear `SKIP_TEMPLATE_COMMENT_CHECK=1`
    en el environment. No usar salvo necesidad real.
    """
    if os.environ.get('SKIP_TEMPLATE_COMMENT_CHECK') == '1':
        return []

    errores: list[checks.CheckMessage] = []
    visited: set[str] = set()

    for base_dir in _candidate_template_dirs():
        for root, dirs, files in os.walk(base_dir):
            # Skip ruido típico — node_modules, .venv, staticfiles, etc.
            dirs[:] = [
                d for d in dirs
                if d not in ('.venv', 'venv', 'node_modules',
                             'staticfiles', '__pycache__', 'migrations',
                             '.git', '.tox')
            ]
            for f in files:
                if not f.endswith(('.html', '.htm')):
                    continue
                path = os.path.join(root, f)
                if path in visited:
                    continue
                visited.add(path)
                for line, preview in _scan_file(path):
                    errores.append(checks.Error(
                        (
                            f'Comentario {{# ... #}} multi-línea en '
                            f'{path}:{line}. Django NO lo interpreta como '
                            f'comentario y lo renderiza como TEXTO en la página.'
                        ),
                        hint=(
                            'Convertí a {% comment %}...{% endcomment %}. '
                            f'Preview: "{preview}…"'
                        ),
                        id=COMMENT_BUG_ID,
                    ))
    return errores
