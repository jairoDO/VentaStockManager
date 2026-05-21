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


def get_status_detail() -> dict:
    """
    Variante "completa" de `is_ready()`. Devuelve TODO el JSON que
    expone el wa-bot, normalizado al shape que consume el panel admin:

      {
        ok: True|False,         # si pudimos hablar con el bot
        ready: bool,
        state: str,             # CONNECTED, UNPAIRED, ... o reason
        me: {id, pushname, ...} | None,
        error: str | None,
      }

    En caso de fallo de red devuelve `ok=False` con el mensaje en
    `error` — el caller renderiza "wa-bot no disponible" en la UI.
    """
    try:
        r = requests.get(
            f'{_base_url()}/status',
            headers=_auth_headers(),
            timeout=5,
        )
        if r.status_code == 401:
            return {
                'ok': False,
                'ready': False,
                'state': 'unauthorized',
                'me': None,
                'error': 'unauthorized (revisar WHATSAPP_API_TOKEN)',
            }
        data = r.json()
        return {
            'ok': True,
            'ready': bool(data.get('ready')),
            'state': data.get('state') or data.get('reason') or 'unknown',
            'me': data.get('me'),
            'error': None,
        }
    except requests.RequestException as exc:
        return {
            'ok': False,
            'ready': False,
            'state': 'connection_error',
            'me': None,
            'error': str(exc),
        }


def get_qr_bytes() -> tuple[bytes | None, str]:
    """
    Devuelve los bytes PNG del último QR generado, o (None, motivo)
    si no hay QR disponible (ej. la sesión ya está activa, o el bot
    todavía no inicializó).

    Pensado para que el panel admin proxy-ee el PNG sin exponer el
    puerto del wa-bot al browser del operador (más seguro: el browser
    solo conoce localhost:8000).
    """
    try:
        r = requests.get(
            f'{_base_url()}/qr',
            headers=_auth_headers(),
            timeout=5,
        )
        if r.status_code == 204:
            return None, 'no_qr_available'
        if r.status_code == 401:
            return None, 'unauthorized'
        if r.status_code != 200:
            return None, f'http_{r.status_code}'
        return r.content, 'ok'
    except requests.RequestException as exc:
        return None, f'connection_error: {exc}'


def logout() -> dict:
    """
    Pide al wa-bot que cierre la sesión actual. El bot va a hacer
    `client.logout()` (borra archivos de sesión) + `process.exit(0)`
    para forzar un arranque limpio. docker-compose lo reinicia
    gracias a `restart: unless-stopped`.

    Devuelve `{ok: True}` (el container se está reiniciando) o
    `{ok: False, error}` si no se pudo contactar.
    """
    try:
        r = requests.post(
            f'{_base_url()}/logout',
            headers=_auth_headers(),
            timeout=10,
        )
        if r.status_code == 401:
            return {'ok': False, 'error': 'unauthorized'}
        return r.json()
    except requests.RequestException as exc:
        log.warning('wa-bot logout falló: %s', exc)
        return {'ok': False, 'error': f'connection_error: {exc}'}


def restart() -> dict:
    """
    Reinicia el proceso del wa-bot SIN borrar la sesión. Útil cuando
    el bot quedó en un estado raro pero todavía tiene la sesión
    válida en el volume — al reiniciar se reconecta sin pedir QR.
    """
    try:
        r = requests.post(
            f'{_base_url()}/restart',
            headers=_auth_headers(),
            timeout=10,
        )
        if r.status_code == 401:
            return {'ok': False, 'error': 'unauthorized'}
        return r.json()
    except requests.RequestException as exc:
        log.warning('wa-bot restart falló: %s', exc)
        return {'ok': False, 'error': f'connection_error: {exc}'}


def exists(phone: str) -> dict:
    """
    Verifica si un número está registrado en WhatsApp. Devuelve:
      - {'ok': True, 'exists': True, 'jid': '...'}   → existe, podemos mandarle
      - {'ok': True, 'exists': False}                → NO existe en WhatsApp
      - {'ok': False, 'error': '...'}                → no pudimos chequear

    Por qué importa: `send_text/send_media` aceptan cualquier número
    formalmente válido y devuelven `ok: True` aunque WhatsApp NO entregue
    el mensaje (silently dropped si el JID no está registrado). Este
    check previo evita "envíos a la nada" — la task de difusión lo usa
    para marcar el envío como `fallido` con motivo claro.
    """
    if not phone:
        return {'ok': False, 'error': 'phone es requerido'}
    try:
        r = requests.get(
            f'{_base_url()}/exists',
            params={'phone': phone},
            headers=_auth_headers(),
            timeout=10,
        )
        if r.status_code == 401:
            return {'ok': False, 'error': 'unauthorized'}
        return r.json()
    except requests.RequestException as exc:
        log.warning('wa-bot exists() falló: %s', exc)
        return {'ok': False, 'error': f'connection_error: {exc}'}


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
