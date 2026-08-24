"""
Task asincrónica para procesar una Campania.

La idea es:
  1. El admin aprieta "Enviar campaña" → se llaman a
     `crear_envios_pendientes(campania)` para "explotar" la campaña en
     uno-por-cliente.
  2. Acto seguido se encola `enviar_campania(campania_id)` en django-q2.
  3. El worker procesa los EnvioWhatsapp pendientes uno por uno, con
     delay configurable entre cada uno para no levantar sospechas en
     WhatsApp.

Manejo de fallos:
  - Si un envío falla (timeout, número inválido, lo que sea), lo
    marcamos como `fallido` con el error y seguimos con el siguiente.
    NO frenamos toda la campaña por un fallo individual.
  - Si el wa-bot está caído al comenzar, marcamos la campaña como
    finalizada (con todos los envíos fallidos por "bot no disponible")
    para que el admin pueda reintentarla creando una nueva campaña.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from wa_campania import wa_client
from wa_campania.audiencia import resolver_clientes
from wa_campania.models import Campania, EnvioWhatsapp

log = logging.getLogger(__name__)


def crear_envios_pendientes(campania: Campania) -> int:
    """
    Resuelve la audiencia y crea `EnvioWhatsapp` pendientes (uno por
    cliente). No envía nada — solo arma la cola.

    Devuelve cuántos envíos se crearon (ignorando duplicados por la
    constraint unique).
    """
    clientes = resolver_clientes(campania.audiencia_filtro)
    nuevos = []
    for c in clientes.only('id', 'whatsapp_number').iterator():
        nuevos.append(EnvioWhatsapp(
            campania=campania,
            cliente=c,
            telefono_usado=c.whatsapp_number or '',
            status=EnvioWhatsapp.STATUS_PENDIENTE,
        ))
    if not nuevos:
        return 0
    # `ignore_conflicts=True` protege contra el caso "el admin
    # apretó enviar dos veces": la constraint UNIQUE(campania, cliente)
    # rebota los duplicados sin lanzar excepción.
    EnvioWhatsapp.objects.bulk_create(nuevos, batch_size=500, ignore_conflicts=True)
    return campania.envios.filter(status=EnvioWhatsapp.STATUS_PENDIENTE).count()


def preparar_reenvio_fallidos(campania_id: int) -> int:
    """
    Pasa a pendiente solo los envíos fallidos de una campaña finalizada.

    El bloqueo de fila + cambio inmediato a ENVIANDO evita que dos clics
    simultáneos creen dos tasks para la misma campaña.
    """
    with transaction.atomic():
        campania = Campania.objects.select_for_update().get(pk=campania_id)
        if campania.estado != Campania.ESTADO_FINALIZADA:
            return 0

        fallidos = campania.envios.filter(status=EnvioWhatsapp.STATUS_FALLIDO)
        cantidad = fallidos.count()
        if cantidad == 0:
            return 0

        fallidos.update(
            status=EnvioWhatsapp.STATUS_PENDIENTE,
            error_msg='',
            sent_at=None,
        )
        campania.estado = Campania.ESTADO_ENVIANDO
        campania.enviada_at = None
        campania.save(update_fields=['estado', 'enviada_at'])
        return cantidad


def _render_mensaje(template_str: str, cliente) -> str:
    """
    Sustituye variables del template. Soportadas: `{{nombre}}`,
    `{{apellido}}`, `{{saldo}}`.

    Usamos `str.replace` simple en vez de `string.Template` porque
    el `$` de Template choca con los `$` que el operador usa para
    indicar precios en el mensaje ("Promo $5000" rompía).

    Si el admin escribe `{{telefono}}` por error, queda literal en
    el mensaje — preferible a un crash silencioso.
    """
    saldo = cliente.saldo or 0
    return (
        template_str
        .replace('{{nombre}}', cliente.nombre or '')
        .replace('{{apellido}}', cliente.apellido or '')
        .replace('{{saldo}}', f'{saldo:.2f}')
    )


def enviar_campania(campania_id: int) -> dict:
    """
    Worker entry-point. Procesa todos los `EnvioWhatsapp` pendientes
    de la campaña, en orden de creación.

    Devuelve un dict con totales para que django-q2 lo guarde en el
    `result` de la task (útil para debuggear desde el admin de
    django-q2 más adelante).
    """
    try:
        campania = Campania.objects.get(pk=campania_id)
    except Campania.DoesNotExist:
        return {'ok': False, 'error': f'Campania {campania_id} no existe'}

    # Marcamos enviando para que el admin vea progreso en vivo.
    campania.estado = Campania.ESTADO_ENVIANDO
    campania.save(update_fields=['estado'])

    ok, motivo = wa_client.is_ready()
    if not ok:
        # Marcamos todos los pendientes como fallidos y cerramos.
        EnvioWhatsapp.objects.filter(
            campania=campania,
            status=EnvioWhatsapp.STATUS_PENDIENTE,
        ).update(
            status=EnvioWhatsapp.STATUS_FALLIDO,
            error_msg=f'wa-bot no disponible: {motivo}',
            sent_at=timezone.now(),
        )
        campania.estado = Campania.ESTADO_FINALIZADA
        campania.enviada_at = timezone.now()
        campania.save(update_fields=['estado', 'enviada_at'])
        return {'ok': False, 'error': f'wa-bot no disponible: {motivo}'}

    # Cargamos el adjunto una sola vez si existe — así no leemos del
    # disco para cada envío.
    adjunto_bytes = None
    adjunto_mime = None
    adjunto_name = None
    if campania.adjunto:
        try:
            adjunto_bytes = campania.adjunto.read()
            adjunto_name = campania.adjunto.name.split('/')[-1]
            ext = adjunto_name.lower().rsplit('.', 1)[-1] if '.' in adjunto_name else ''
            adjunto_mime = {
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'png': 'image/png',
                'pdf': 'application/pdf',
            }.get(ext, 'application/octet-stream')
        except Exception as exc:
            log.error('No se pudo leer el adjunto de campaña %s: %s', campania_id, exc)

    delay = getattr(settings, 'WHATSAPP_DELAY_SECONDS', 4)
    enviados = 0
    fallidos = 0

    pendientes = campania.envios.filter(
        status=EnvioWhatsapp.STATUS_PENDIENTE,
    ).select_related('cliente')

    for envio in pendientes.iterator():
        cliente = envio.cliente
        if not envio.telefono_usado:
            envio.status = EnvioWhatsapp.STATUS_FALLIDO
            envio.error_msg = 'Cliente sin whatsapp_number'
            envio.sent_at = timezone.now()
            envio.save(update_fields=['status', 'error_msg', 'sent_at'])
            fallidos += 1
            continue

        mensaje = _render_mensaje(campania.mensaje, cliente)
        envio.mensaje_renderizado = mensaje
        envio.status = EnvioWhatsapp.STATUS_ENVIANDO
        envio.save(update_fields=['mensaje_renderizado', 'status'])

        if adjunto_bytes:
            resultado = wa_client.send_media(
                envio.telefono_usado,
                mensaje,
                adjunto_bytes,
                adjunto_mime or 'application/octet-stream',
                adjunto_name,
            )
        else:
            resultado = wa_client.send_text(envio.telefono_usado, mensaje)

        if resultado.get('ok'):
            envio.status = EnvioWhatsapp.STATUS_ENVIADO
            envio.error_msg = ''
            enviados += 1
        else:
            envio.status = EnvioWhatsapp.STATUS_FALLIDO
            envio.error_msg = str(resultado.get('error') or 'sin detalle')
            fallidos += 1
        envio.sent_at = timezone.now()
        envio.save(update_fields=['status', 'error_msg', 'sent_at'])

        # Si el bot se cayó durante una campaña grande, no hacemos cientos
        # de requests condenadas a fallar. Cerramos rápidamente la campaña
        # y dejamos todos los pendientes como fallidos para que el operador
        # pueda usar "Reenviar fallidos" después de reconectar.
        if not resultado.get('ok'):
            bot_ready, bot_reason = wa_client.is_ready()
            if not bot_ready:
                restantes = campania.envios.filter(
                    status=EnvioWhatsapp.STATUS_PENDIENTE,
                ).update(
                    status=EnvioWhatsapp.STATUS_FALLIDO,
                    error_msg=f'wa-bot no disponible: {bot_reason}',
                    sent_at=timezone.now(),
                )
                fallidos += restantes
                break

        # Rate limit. Si el delay es 0 (testing), no esperamos.
        if delay > 0:
            time.sleep(delay)

    campania.estado = Campania.ESTADO_FINALIZADA
    campania.enviada_at = timezone.now()
    campania.save(update_fields=['estado', 'enviada_at'])
    return {'ok': True, 'enviados': enviados, 'fallidos': fallidos}
