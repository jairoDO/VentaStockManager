"""
Cliente HTTP del service `wa-bot` (open-wa via Node.js).

Toda la integración con WhatsApp pasa por acá. Si mañana cambiamos de
provider (Meta Cloud API, Twilio, etc.), reescribimos solo este
módulo y todo lo demás sigue funcionando.

Diseño:
  - Funciones puras `send_text` / `send_media` que devuelven un dict
    con `ok: bool` y, según el resultado, `id` o `error`. NO lanzan
    excepción ante un fallo de red o del provider — el caller decide
    qué hacer (en nuestro caso, marcar el EnvioWhatsapp como fallido
    y seguir con el siguiente).
  - Timeout corto (10 segundos) porque el worker manda de a uno y no
    queremos que un envío trabado bloquee la cola.
  - Helper `is_ready()` para chequear antes de empezar a procesar la
    cola; ahorra crear 500 envíos pendientes si el bot ni siquiera
    está conectado.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import requests
from django.conf import settings

log = logging.getLogger(__name__)


def _base_url() -> str:
    # Lo leemos en cada llamada en vez de cachearlo a nivel módulo
    # para que el testing pueda monkeypatchear `settings.WHATSAPP_API_URL`.
    return getattr(settings, 'WHATSAPP_API_URL', 'http://wa-bot:3000').rstrip('/')


def _auth_headers() -> dict:
    """
    Header de autenticación si `WHATSAPP_API_TOKEN` está seteado en
    settings. En dev local la var está vacía y el wa-bot acepta sin
    token (modo dev). En producción ambos lados tienen que coincidir.
    """
    token = getattr(settings, 'WHATSAPP_API_TOKEN', '') or ''
    return {'X-Bot-Token': token} if token else {}


def is_ready() -> tuple[bool, str]:
    """
    True si el wa-bot está conectado a WhatsApp. Devuelve también un
    string descriptivo para loguear el motivo si no.
    """
    try:
        r = requests.get(
            f'{_base_url()}/status',
            headers=_auth_headers(),
            timeout=5,
        )
        if r.status_code == 401:
            return False, 'unauthorized (revisar WHATSAPP_API_TOKEN)'
        data = r.json()
        return bool(data.get('ready')), data.get('state') or data.get('reason') or 'unknown'
    except requests.RequestException as exc:
        return False, f'connection_error: {exc}'


def send_text(phone: str, message: str) -> dict:
    """
    Manda un mensaje de texto. Devuelve `{ok: True, id}` o
    `{ok: False, error}`.
    """
    if not phone or not message:
        return {'ok': False, 'error': 'phone y message son requeridos'}
    try:
        r = requests.post(
            f'{_base_url()}/send-text',
            json={'phone': phone, 'message': message},
            headers=_auth_headers(),
            timeout=10,
        )
        return r.json()
    except requests.RequestException as exc:
        log.warning('wa-bot send_text falló: %s', exc)
        return {'ok': False, 'error': f'connection_error: {exc}'}


def send_media(
    phone: str,
    message: str,
    media_bytes: bytes,
    mime: str,
    filename: Optional[str] = None,
) -> dict:
    """
    Manda un archivo (imagen o PDF) opcionalmente con caption. El
    caller pasa los bytes crudos del adjunto; nosotros nos encargamos
    del base64.

    El límite práctico de WhatsApp es ~16MB por archivo; el wa-bot
    aceta hasta 20MB (body limit). Si la imagen es más grande, el
    envío va a fallar — el caller debería comprimirla antes.
    """
    if not phone:
        return {'ok': False, 'error': 'phone es requerido'}
    if not media_bytes:
        return {'ok': False, 'error': 'media_bytes vacío'}
    b64 = base64.b64encode(media_bytes).decode('ascii')
    payload = {
        'phone': phone,
        'message': message or '',
        'base64': b64,
        'mime': mime or 'application/octet-stream',
        'filename': filename or os.path.basename(filename or 'adjunto'),
    }
    try:
        # Timeout más alto que send_text porque subir 16MB tarda.
        r = requests.post(
            f'{_base_url()}/send-media',
            json=payload,
            headers=_auth_headers(),
            timeout=30,
        )
        return r.json()
    except requests.RequestException as exc:
        log.warning('wa-bot send_media falló: %s', exc)
        return {'ok': False, 'error': f'connection_error: {exc}'}
