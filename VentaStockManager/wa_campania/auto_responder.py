"""
Auto-responder de mensajes entrantes al wa-bot.

Cuando un cliente le manda un mensaje al WhatsApp del negocio, el bot
hace POST a `/wa-campania/api/incoming/` con `{from, text}`. Esta vista
decide si responder y qué mandar.

Filosofía:
  - Solo respondemos a clientes registrados (`whatsapp_number` matchea).
    NO respondemos a números desconocidos — no exponemos info, no
    abrimos vectores de spam.
  - Solo respondemos a keywords reconocidos. Cualquier otra cosa se
    ignora silenciosamente (el bot no responde, el cliente no ve nada).
    El operador puede entrar al chat normal en WhatsApp Web y
    responder a mano cuando vea el mensaje.
  - El operador puede apagar todo desde
    /admin/configuracion/configuraciongeneral/ → checkbox
    "auto responder habilitado".

Keywords reconocidos (case-insensitive, sin tildes, después de trim):
  - "lista", "precios", "precio", "lista de precios" → mandar lista
  - "saldo", "cuanto debo", "mi saldo" → responder saldo

Si el cliente tiene lista asociada, respetamos su preferencia de
formato (texto / link / pdf / ambos), igual que el flujo de difusión.
Si no tiene lista, respondemos un mensaje cortés ("todavía no te
armamos una lista, le aviso al dueño").
"""
from __future__ import annotations

import logging
import re
import unicodedata

from articulo.models import ListaPrecios
from articulo.tasks_difusion import _render_lista_como_texto
from cliente.models import Cliente
from configuracion.models import get_config


log = logging.getLogger(__name__)


# Keywords reconocidos. Lista cerrada para no tener falsos positivos.
# Si el cliente manda "necesito la lista" → matchea por "lista" (sub-
# string). Si manda "cuanto debo" → matchea por exact "cuanto debo".
KEYWORDS_LISTA = ('lista', 'precios', 'precio')
KEYWORDS_SALDO = ('saldo', 'cuanto debo', 'que debo', 'cuánto debo', 'qué debo')


def _normalizar_texto(s: str) -> str:
    """
    Lowercase + sin tildes + trim. Así "Cuánto Debo?" → "cuanto debo"
    y matchea con KEYWORDS_SALDO sin que el operador tenga que listar
    cada variación con/sin tilde.
    """
    if not s:
        return ''
    # NFKD descompone los acentos, luego filtramos los chars de
    # combinación. "á" → "a", "ñ" → "ñ" (se preserva la ñ — ASCII no
    # tiene tilde para descomponer).
    sin_tildes = ''.join(
        c for c in unicodedata.normalize('NFKD', s)
        if not unicodedata.combining(c)
    )
    # Sacamos signos de puntuación finales tipo "?" "." que el cliente
    # suele mandar — "lista?" debería matchear igual.
    limpio = re.sub(r'[?!.,;:]+$', '', sin_tildes.lower().strip())
    return limpio


def _matchea_keyword(texto_norm: str, keywords: tuple[str, ...]) -> bool:
    """
    True si el texto (ya normalizado) matchea cualquier keyword. Match
    es EXACTO o como palabra dentro del texto — así "necesito la lista"
    matchea "lista" pero "listado" o "listar" no.
    """
    for kw in keywords:
        if texto_norm == kw:
            return True
        # \b = word boundary. Funciona con espacios, inicio/fin de string,
        # signos. Match case-insensitive ya hecho arriba.
        if re.search(r'\b' + re.escape(kw) + r'\b', texto_norm):
            return True
    return False


def _identificar_cliente(phone: str) -> Cliente | None:
    """
    Busca el cliente por whatsapp_number. Maneja variaciones de formato
    AR (con/sin 549, etc.) probando primero el match exacto y luego
    fuzzy por los últimos dígitos.
    """
    if not phone:
        return None
    # Limpiamos a solo dígitos.
    digits = re.sub(r'\D', '', phone)
    if not digits:
        return None

    # Match exacto primero — es el caso más común.
    cliente = Cliente.objects.filter(whatsapp_number=digits).first()
    if cliente:
        return cliente

    # Fallback: match por los últimos 10 dígitos (móvil AR sin internacional).
    # Útil si el cliente está cargado como '5493513452496' pero el bot
    # recibe el JID con sufijo `:NN` y solo nos pasaron '3513452496'.
    if len(digits) >= 10:
        ultimos_10 = digits[-10:]
        cliente = Cliente.objects.filter(whatsapp_number__endswith=ultimos_10).first()
        if cliente:
            return cliente

    return None


def _armar_respuesta_lista(cliente: Cliente, request, texto_original: str = '') -> dict:
    """
    Decide qué mandarle al cliente cuando pide "lista". Resuelve modo
    según preferencia → global. Si no tiene lista asignada, devuelve
    un mensaje cortés en vez de fallo silencioso.

    Devuelve dict con:
      {action: 'reply_text'|'reply_media', text: str, attachment?: {...}}
    """
    lista = (
        ListaPrecios.objects
        .filter(cliente=cliente)
        .order_by('-updated_at')
        .first()
    )
    if not lista:
        # Cliente sin lista asignada. Dos cosas:
        #   1. Crear una `SolicitudListaCliente` pendiente para que el
        #      operador la vea en el badge del header del admin. SIN
        #      esto, "le aviso al dueño" sería una promesa vacía — el
        #      dueño nunca se enteraría.
        #   2. Responder cortés al cliente.
        # Dedupe: si ya hay una solicitud PENDIENTE para este cliente,
        # no creamos otra (sería ruido). Si la última quedó resuelta
        # y vuelve a pedir, sí creamos nueva.
        from articulo.models import SolicitudListaCliente
        existe_pendiente = SolicitudListaCliente.objects.filter(
            cliente=cliente, resuelta=False,
        ).exists()
        if not existe_pendiente:
            try:
                SolicitudListaCliente.objects.create(
                    cliente=cliente,
                    mensaje_original=(texto_original or '')[:500],
                )
            except Exception:
                log.exception('No pude crear SolicitudListaCliente')
        else:
            log.info(
                'Cliente %s pidió lista de nuevo, ya hay solicitud pendiente — no dup.',
                cliente.id,
            )
        return {
            'action': 'reply_text',
            'text': (
                f'Hola {(cliente.nombre or "").split(" ")[0] or "amigo"}, '
                'todavía no tengo una lista personalizada para vos. '
                'Le aviso al dueño y te paso una en breve. ¡Gracias por escribir!'
            ),
        }

    cfg = get_config()
    modo = cfg.resolver_formato_lista(cliente=cliente)

    # Modo texto: armamos el cuerpo completo con la lista.
    if modo == 'texto':
        return {
            'action': 'reply_text',
            'text': _render_lista_como_texto(lista, cliente),
        }

    # Modo PDF / link / ambos requieren un share_url. Si la lista no
    # tiene link público activo, lo generamos al vuelo (igual que hace
    # el editor cuando aprietas "Enviar via bot").
    if not lista.link_activo:
        lista.compartir()  # genera token + expira_at usando ConfigGeneral

    share_url = ''
    if lista.share_token:
        from django.urls import reverse
        share_url = request.build_absolute_uri(
            reverse('lista_precios_publica_web', args=[lista.share_token])
        )

    primer_nombre = (cliente.nombre or '').split(' ')[0] or 'amigo'

    if modo == 'pdf':
        # PDF adjunto — armamos los bytes y los pasamos. El bot manda
        # con sendMedia. NOTA: si la API de auto-respond se mantiene
        # síncrona, el render del PDF puede demorar 1-2 segundos. Es OK.
        from articulo.tasks_difusion import _render_pdf
        try:
            pdf_bytes = _render_pdf(lista)
        except Exception:
            log.exception('Render PDF para auto-respond falló')
            pdf_bytes = None
        if pdf_bytes:
            import base64
            safe = ''.join(c if c.isalnum() else '_' for c in lista.nombre)[:40] or 'lista'
            return {
                'action': 'reply_media',
                'text': f'Hola {primer_nombre}, te paso la lista de precios.',
                'attachment': {
                    'mime': 'application/pdf',
                    'filename': f'{safe}.pdf',
                    'base64': base64.b64encode(pdf_bytes).decode('ascii'),
                },
            }
        # Fallback: si falla el render, mandamos solo link.
        modo = 'link'

    if modo == 'link':
        return {
            'action': 'reply_text',
            'text': (
                f'Hola {primer_nombre}, te paso la lista de precios:\n\n'
                f'{share_url}\n\nCualquier consulta avisame.'
            ),
        }

    # modo == 'ambos': PDF + link en el caption.
    from articulo.tasks_difusion import _render_pdf
    try:
        pdf_bytes = _render_pdf(lista)
    except Exception:
        log.exception('Render PDF para auto-respond falló (modo ambos)')
        pdf_bytes = None
    if pdf_bytes:
        import base64
        safe = ''.join(c if c.isalnum() else '_' for c in lista.nombre)[:40] or 'lista'
        return {
            'action': 'reply_media',
            'text': (
                f'Hola {primer_nombre}, te paso la lista de precios:\n\n'
                f'{share_url}\n\nTambién va adjunta como PDF.'
            ),
            'attachment': {
                'mime': 'application/pdf',
                'filename': f'{safe}.pdf',
                'base64': base64.b64encode(pdf_bytes).decode('ascii'),
            },
        }
    # Fallback al caso link.
    return {
        'action': 'reply_text',
        'text': (
            f'Hola {primer_nombre}, te paso la lista de precios:\n\n'
            f'{share_url}\n\nCualquier consulta avisame.'
        ),
    }


def _armar_respuesta_saldo(cliente: Cliente) -> dict:
    """Devuelve el saldo del cliente formateado para WhatsApp."""
    saldo = cliente.saldo or 0
    primer_nombre = (cliente.nombre or '').split(' ')[0] or 'amigo'

    if saldo == 0:
        texto = f'Hola {primer_nombre}, no tenés saldo pendiente. ¡Todo al día!'
    elif saldo > 0:
        texto = (
            f'Hola {primer_nombre}, tenés un saldo a favor de ${saldo:.2f}. '
            'Lo usamos en tu próximo pedido.'
        )
    else:
        texto = (
            f'Hola {primer_nombre}, tenés un saldo pendiente de '
            f'${abs(saldo):.2f}. Cuando puedas pasá a saldarlo, gracias.'
        )

    return {'action': 'reply_text', 'text': texto}


def procesar_mensaje_entrante(phone: str, text: str, request) -> dict:
    """
    Entry-point del auto-responder. Recibe el mensaje crudo del bot
    y devuelve qué hacer.

    Devuelve siempre un dict con `action`:
      - 'ignore'      → el bot no responde nada.
      - 'reply_text'  → el bot manda un texto.
      - 'reply_media' → el bot manda media + texto.

    NUNCA tira excepción — si algo falla, devuelve 'ignore' silencioso.
    Es preferible que el cliente no reciba respuesta automática a que
    reciba un mensaje de error técnico.
    """
    cfg = get_config()
    if not cfg.auto_responder_habilitado:
        return {'action': 'ignore', 'reason': 'auto_responder_off'}

    # Identificar cliente. Si no está en nuestra DB, no respondemos —
    # no exponemos info, no abrimos vector de spam.
    cliente = _identificar_cliente(phone)
    if not cliente:
        log.info('Auto-respond: mensaje de número desconocido %s', phone)
        return {'action': 'ignore', 'reason': 'cliente_desconocido'}

    texto_norm = _normalizar_texto(text or '')
    if not texto_norm:
        return {'action': 'ignore', 'reason': 'texto_vacio'}

    # Detectar intent.
    try:
        if _matchea_keyword(texto_norm, KEYWORDS_LISTA):
            return _armar_respuesta_lista(cliente, request, texto_original=text)
        if _matchea_keyword(texto_norm, KEYWORDS_SALDO):
            return _armar_respuesta_saldo(cliente)
    except Exception:
        log.exception('Auto-respond: error armando respuesta para %s', phone)
        return {'action': 'ignore', 'reason': 'error_armando_respuesta'}

    # Mensaje que no matchea keywords conocidos — lo dejamos para el
    # operador. El cliente no ve nada (el bot no escribe "no entendí"
    # ni nada — preferimos silencio antes que respuesta tonta).
    return {'action': 'ignore', 'reason': 'sin_keyword_match'}
