"""
Tasks asincrónicas para la difusión masiva de listas de precios.

Flujo:
  1. El operador aprieta "Enviar a los N seleccionados" en la pantalla
     de difundir → la view crea N `DifusionListaPreciosEnvio` pendientes
     (uno por cliente, con el `modo` ya resuelto en cascada) y encola
     `procesar_difusion(lista_id)` en django-q2.
  2. El worker procesa los pendientes en orden, espaciando cada envío
     con `DIFUSION_DELAY_SEGUNDOS` para no levantar sospechas en WhatsApp.
  3. La UI polea `/api/.../progreso/` cada 2s para mostrar barra en vivo.

Manejo de fallos:
  - Si el wa-bot está caído al empezar, marcamos TODOS los pendientes
    como fallidos con motivo claro. El operador puede re-disparar
    después (creando nuevos envíos).
  - Si UN envío falla (número inválido, timeout) lo marcamos como
    fallido y seguimos. NO se reintenta automáticamente.
"""
from __future__ import annotations

import io
import logging
import time

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from wa_campania import wa_client


log = logging.getLogger(__name__)


# Delay entre envíos en segundos. Compartimos el setting con
# wa_campania para que el rate limit sea consistente entre campañas
# y difusiones de listas (la cuenta de WhatsApp es la misma — si
# excedemos por un lado, el ban afecta todo).
def _delay() -> float:
    return float(getattr(settings, 'WHATSAPP_DELAY_SECONDS', 15))


def crear_envios_pendientes_difusion(
    lista,
    cliente_ids: list[int],
    modo_override: str = '',
    user=None,
) -> int:
    """
    Crea `DifusionListaPreciosEnvio` pendientes para cada cliente
    elegido. Resuelve el `modo` por cliente en cascada:
        override > cliente.preferencia > config.default_global.

    Devuelve cuántos envíos se crearon (puede ser menor a
    len(cliente_ids) si algún cliente no tiene whatsapp_number).
    """
    from cliente.models import Cliente
    from configuracion.models import get_config
    from .models import DifusionListaPreciosEnvio

    cfg = get_config()
    # Cargo todos los clientes en una sola query para resolver el modo.
    clientes = {
        c.id: c for c in Cliente.objects.filter(
            id__in=cliente_ids,
        ).only(
            'id', 'whatsapp_number', 'formato_preferido_lista_precios',
            'puede_recibir_whatsapp',
        )
    }

    nuevos = []
    necesita_link_publico = False
    for cid in cliente_ids:
        cliente = clientes.get(cid)
        if not cliente:
            continue
        if not cliente.whatsapp_number:
            continue  # skip silenciosamente — no hay forma de mandar
        modo = cfg.resolver_formato_lista(cliente=cliente, override=modo_override)
        if modo in (
            DifusionListaPreciosEnvio.MODO_LINK,
            DifusionListaPreciosEnvio.MODO_AMBOS,
        ):
            necesita_link_publico = True
        nuevos.append(DifusionListaPreciosEnvio(
            lista=lista,
            cliente=cliente,
            modo=modo,
            telefono_usado=cliente.whatsapp_number,
            status=DifusionListaPreciosEnvio.STATUS_PENDIENTE,
            creado_por=user if (user and user.is_authenticated) else None,
        ))

    if not nuevos:
        return 0

    # El link es parte del contenido del envío, por lo que debe existir
    # ANTES de crear la cola. No dependemos de que el operador haya
    # pasado previamente por el botón "Compartir link público": también
    # cubre el flujo descargar PDF → difundir.
    if necesita_link_publico and not lista.link_activo:
        lista.compartir()

    DifusionListaPreciosEnvio.objects.bulk_create(nuevos, batch_size=500)
    return len(nuevos)


def _build_message(lista, cliente, share_url: str, incluir_link: bool) -> str:
    """
    Arma el texto del mensaje. El operador NO edita esto desde la UI de
    difundir (a diferencia de la pantalla vieja con wa.me). Si en algún
    momento queremos hacerlo configurable, se mueve a ConfiguracionGeneral
    como template.
    """
    primer_nombre = (cliente.nombre or '').split(' ')[0] or 'amigo'
    msg = f'Hola {primer_nombre}, te paso la lista de precios actualizada.'
    if incluir_link and share_url:
        msg += f'\n\n{share_url}'
    msg += '\n\nCualquier consulta avisame por acá.'
    return msg


def _render_lista_como_texto(lista, cliente) -> str:
    """
    Renderiza la lista de precios COMPLETA como texto plano, listo
    para mandar en el body del mensaje WhatsApp. Cada item ocupa una
    línea: nombre + precio. Ideal para listas chicas (< 50 items) —
    en listas grandes el mensaje queda muy largo y WhatsApp puede
    truncarlo o cobrar el operador como "spam" si lo recibe seguido.

    Reusa `precio_efectivo` con el descuento/aumento de la lista para
    que los números coincidan exactamente con el PDF y la versión web.
    """
    from .precios import precio_efectivo, cargar_precios_pactados

    items_qs = (
        lista.items
        .select_related('articulo')
        .order_by('orden', 'articulo__nombre')
    )
    items = list(items_qs)
    articulos = [i.articulo for i in items]
    pactados = cargar_precios_pactados(cliente, articulos)

    primer_nombre = (cliente.nombre or '').split(' ')[0] or 'amigo'
    lineas = [
        f'Hola {primer_nombre}, te paso la lista *{lista.nombre}*:',
        '',
    ]
    if not items:
        lineas.append('(lista vacía — todavía no tiene artículos cargados)')
    else:
        for it in items:
            articulo = it.articulo
            precio = precio_efectivo(
                articulo, cliente,
                descuento_lista=lista.descuento_porcentaje,
                precios_pactados_map=pactados,
                tipo_ajuste=lista.tipo_ajuste,
            )
            # Marcamos con (*) los precios pactados (mismo criterio que
            # el PDF) para que el cliente vea cuáles tienen acuerdo.
            marca = '(*) ' if pactados.get(articulo.id) is not None else ''
            nombre_visible = articulo.nombre
            if articulo.marca and articulo.marca != 'Generico':
                nombre_visible = f'{articulo.marca} {articulo.nombre}'
            # Nota opcional inline (la del ListaPreciosItem)
            nota = f' — {it.nota}' if it.nota else ''
            lineas.append(f'• {marca}{nombre_visible}{nota}: ${precio:.2f}')

    # Footer con info de ajuste (si lo tiene) y precios pactados.
    if lista.descuento_porcentaje and lista.descuento_porcentaje > 0:
        etiqueta = 'aumento' if lista.tipo_ajuste == 'aumento' else 'descuento'
        sufijo = (
            f' ({lista.descuento_motivo})' if lista.descuento_motivo else ''
        )
        lineas.append('')
        lineas.append(
            f'_{etiqueta.capitalize()} del {lista.descuento_porcentaje:g}% aplicado{sufijo}._'
        )
    if any(pactados.get(a.id) is not None for a in articulos):
        lineas.append('_(*) Precio acordado con vos._')

    lineas.append('')
    lineas.append('Cualquier consulta avisame por acá.')

    return '\n'.join(lineas)


def _render_pdf(lista) -> bytes:
    """
    Genera el PDF de la lista en bytes. Reusa el helper del módulo de
    views (single source of truth del formato) para que el PDF sea
    idéntico al que se baja desde el botón "Descargar PDF" del editor.
    """
    from .views_lista_precios import _render_pdf_lista
    response = _render_pdf_lista(lista)
    return response.content


def procesar_difusion(lista_id: int) -> dict:
    """
    Worker entry-point. Procesa todos los `DifusionListaPreciosEnvio`
    pendientes de esta lista, en orden de creación.

    Devuelve totales para que django-q2 los guarde en el `result` del
    task (sirve para auditoría en /admin/django_q/).
    """
    from .models import DifusionListaPreciosEnvio, ListaPrecios

    try:
        lista = ListaPrecios.objects.select_related('cliente').get(pk=lista_id)
    except ListaPrecios.DoesNotExist:
        return {'ok': False, 'error': f'Lista {lista_id} no existe.'}

    # Chequeo upfront: si el wa-bot está caído, marcamos todos los
    # pendientes como fallidos y cortamos.
    ok, motivo = wa_client.is_ready()
    if not ok:
        DifusionListaPreciosEnvio.objects.filter(
            lista=lista,
            status=DifusionListaPreciosEnvio.STATUS_PENDIENTE,
        ).update(
            status=DifusionListaPreciosEnvio.STATUS_FALLIDO,
            error_msg=f'wa-bot no disponible: {motivo}',
            sent_at=timezone.now(),
        )
        return {'ok': False, 'error': f'wa-bot no disponible: {motivo}'}

    # Garantía defensiva adicional: el link pudo vencer entre el momento
    # de encolar y el momento en que el worker comenzó a procesar. Si hay
    # al menos un pendiente que necesita link, lo creamos/renovamos antes
    # de construir el mensaje.
    pendientes_base = DifusionListaPreciosEnvio.objects.filter(
        lista=lista,
        status=DifusionListaPreciosEnvio.STATUS_PENDIENTE,
    )
    necesita_link_publico = pendientes_base.filter(
        modo__in=(
            DifusionListaPreciosEnvio.MODO_LINK,
            DifusionListaPreciosEnvio.MODO_AMBOS,
        ),
    ).exists()
    if necesita_link_publico and not lista.link_activo:
        lista.compartir()

    # Armar la URL pública una vez, usando el token que acabamos de
    # garantizar. Esta ruta no requiere login.
    share_url = ''
    if lista.link_activo and lista.share_token:
        # No tenemos `request` acá, así que armamos URL con SITE_URL del
        # settings (o un default razonable). Para producción esto va a
        # ser el dominio de Render.
        base = getattr(settings, 'PUBLIC_SITE_URL', '') or ''
        if not base:
            # Fallback al hardcodeo histórico — el operador puede
            # configurar PUBLIC_SITE_URL después.
            base = 'http://localhost:8000'
        path = reverse('lista_precios_publica_web', args=[lista.share_token])
        share_url = base.rstrip('/') + path

    # Pre-renderizo el PDF una sola vez por difusión (no por cliente):
    # como el contenido es el mismo, ahorramos N renders pesados de
    # reportlab. Lo cacheamos en memoria del worker.
    pdf_bytes_cache = None

    delay = _delay()
    enviados = 0
    fallidos = 0

    pendientes = pendientes_base.select_related('cliente').order_by('created_at')

    for envio in pendientes:
        # Re-fetch defensivo: si dos workers procesaron en paralelo
        # y otro ya lo marcó como enviando/enviado, salteamos.
        envio.refresh_from_db()
        if envio.status != DifusionListaPreciosEnvio.STATUS_PENDIENTE:
            continue

        envio.status = DifusionListaPreciosEnvio.STATUS_ENVIANDO
        envio.save(update_fields=['status'])

        # Verificación pre-envío: chequear que el número EXISTA en
        # WhatsApp antes de mandar. Sin esto, números mal cargados
        # (ej. sin código país) reciben "enviado" silenciosamente
        # porque Baileys acepta el JID pero WhatsApp no entrega.
        # Caso real: un cliente con '3513452496' (sin 549 adelante)
        # nunca recibía las difusiones pero la tabla decía "enviado".
        check = wa_client.exists(envio.telefono_usado)
        if not check.get('ok'):
            # No pudimos chequear (red, etc). Logueamos pero intentamos
            # mandar igual — el chequeo es defensivo, no bloqueante.
            log.warning(
                'exists() falló para %s: %s — mando igual sin verificar.',
                envio.telefono_usado, check.get('error'),
            )
        elif not check.get('exists'):
            # Confirmado: el número NO está en WhatsApp (o es self).
            # Marcamos fallido con motivo claro y NO mandamos (no
            # perdamos un slot del rate limit en algo que no va a
            # entregar).
            #
            # `reason='self'` viene cuando el destinatario es el mismo
            # número con el que el bot está vinculado — WhatsApp acepta
            # el envío pero el mensaje va a "Mensajes contigo" en
            # silencio y NO genera notificación. El operador ve "enviado"
            # falso y se confunde. Caso real: probar con tu propio
            # número de testing cuando el bot está vinculado con ese.
            if check.get('reason') == 'self':
                envio.error_msg = check.get('message') or (
                    'No se puede mandar al mismo número con el que '
                    'está vinculado el bot. Probá con otro número.'
                )
            else:
                envio.error_msg = (
                    f'El número {envio.telefono_usado} no está registrado '
                    f'en WhatsApp. Revisá que sea el número correcto.'
                )
            envio.status = DifusionListaPreciosEnvio.STATUS_FALLIDO
            envio.sent_at = timezone.now()
            envio.save(update_fields=['status', 'error_msg', 'sent_at'])
            fallidos += 1
            continue

        modo = envio.modo
        cliente = envio.cliente

        # Branch por modo. Cuatro posibilidades:
        #   - 'texto': renderizamos la lista completa como texto plano
        #     y la mandamos en el body. No necesita link público ni PDF.
        #   - 'link':  send-text con mensaje + share_url.
        #   - 'pdf':   send-media con PDF, sin link.
        #   - 'ambos': send-media con PDF + share_url en el caption.
        if modo == envio.MODO_TEXTO:
            mensaje_completo = _render_lista_como_texto(lista, cliente)
            resultado = wa_client.send_text(envio.telefono_usado, mensaje_completo)
        else:
            incluir_link = modo in (envio.MODO_LINK, envio.MODO_AMBOS)
            incluir_pdf = modo in (envio.MODO_PDF, envio.MODO_AMBOS)

            mensaje = _build_message(lista, cliente, share_url, incluir_link)

            if incluir_pdf:
                if pdf_bytes_cache is None:
                    try:
                        pdf_bytes_cache = _render_pdf(lista)
                    except Exception as exc:
                        log.exception('Render PDF lista %s falló: %s', lista.id, exc)
                        pdf_bytes_cache = b''
                if pdf_bytes_cache:
                    # Filename razonable: ej. "Lista_marzo.pdf"
                    safe = ''.join(c if c.isalnum() else '_' for c in lista.nombre)[:40] or 'lista'
                    filename = f'{safe}.pdf'
                    resultado = wa_client.send_media(
                        envio.telefono_usado,
                        mensaje,
                        pdf_bytes_cache,
                        mime='application/pdf',
                        filename=filename,
                    )
                else:
                    # Fallback: el render falló, mandamos solo texto+link
                    # para no quedarnos sin enviar nada útil.
                    resultado = wa_client.send_text(envio.telefono_usado, mensaje)
            else:
                # Solo link.
                resultado = wa_client.send_text(envio.telefono_usado, mensaje)

        if resultado.get('ok'):
            envio.status = DifusionListaPreciosEnvio.STATUS_ENVIADO
            envio.error_msg = ''
            enviados += 1
        else:
            envio.status = DifusionListaPreciosEnvio.STATUS_FALLIDO
            envio.error_msg = str(resultado.get('error') or 'sin detalle')
            fallidos += 1
        envio.sent_at = timezone.now()
        envio.save(update_fields=['status', 'error_msg', 'sent_at'])

        # Rate limit. En tests delay=0.
        if delay > 0:
            time.sleep(delay)

    return {'ok': True, 'enviados': enviados, 'fallidos': fallidos}
