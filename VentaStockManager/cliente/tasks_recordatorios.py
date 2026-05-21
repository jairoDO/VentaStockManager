"""
Recordatorios automáticos de saldo deudor por WhatsApp.

Schedule django-q2 que escanea clientes con saldo negativo (deuda) +
sin compras recientes, y les manda un WhatsApp recordando el pendiente.

Reglas de elegibilidad (TODAS tienen que cumplirse):
  - `puede_recibir_whatsapp=True` (opt-in legal)
  - `whatsapp_number` no vacío
  - `saldo < 0` (debe plata)
  - `abs(saldo) >= recordatorios_saldo_monto_minimo`
  - última compra hace >= `recordatorios_saldo_dias_inactividad` días
    (o nunca compró)
  - último recordatorio enviado >= `recordatorios_saldo_frecuencia_dias`
    días atrás (o nunca le mandamos)
  - el master flag `recordatorios_saldo_habilitado` está prendido

Idempotencia: la task se puede correr N veces por día y solo manda a
los que NO recibieron recordatorio en la última ventana de frecuencia.
Pensado para correr diario aunque el operador haya seteado frecuencia=7
(es el campo de frecuencia el que decide, no el cron).
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Iterator

from django.db.models import Max, Sum
from django.utils import timezone

from wa_campania import wa_client


log = logging.getLogger(__name__)


def _render(template: str, cliente, saldo: Decimal, dias: int | None) -> str:
    """
    Sustituye variables del template. Misma filosofía que wa_campania:
    str.replace simple para no chocar con $ de precios.
    """
    saldo_abs = abs(saldo)
    dias_str = str(dias) if dias is not None else '—'
    return (
        template
        .replace('{{nombre}}', cliente.nombre or '')
        .replace('{{apellido}}', cliente.apellido or '')
        .replace('{{saldo}}', f'{saldo:.2f}')
        .replace('{{saldo_abs}}', f'{saldo_abs:.2f}')
        .replace('{{dias}}', dias_str)
    )


def _clientes_elegibles(config) -> Iterator:
    """
    Devuelve un iterator de Clientes que cumplen TODAS las condiciones
    de elegibilidad. Calcula saldo y última-compra con annotates para
    evitar N+1.

    NO incluye el chequeo de "último recordatorio" — eso se hace por
    cliente en el loop principal porque requiere mirar
    RecordatorioSaldoEnviado por cada uno (y mantenerlo annotate-able
    suma complejidad). Es un chequeo O(N) extra que vale la simplicidad.
    """
    from cliente.models import Cliente
    from datetime import timedelta

    hoy = timezone.now().date()
    desde_inactivo = hoy - timedelta(days=config.recordatorios_saldo_dias_inactividad)

    qs = (
        Cliente.objects
        .filter(
            puede_recibir_whatsapp=True,
        )
        .exclude(whatsapp_number='')
        .annotate(
            saldo_calc=Sum('cuenta__movimientos__monto'),
            ultima_compra=Max('ventas__fecha_compra'),
        )
        # saldo_calc IS NULL si el cliente no tiene movimientos —
        # eso significa saldo=0, no es deudor, se excluye.
        .filter(saldo_calc__lt=0)
    )

    # Filtro de monto mínimo (en Python porque saldo_calc es negativo
    # y monto_minimo es positivo).
    monto_min = config.recordatorios_saldo_monto_minimo or Decimal('0')
    if monto_min > 0:
        qs = qs.filter(saldo_calc__lt=-monto_min)

    # Filtro de inactividad: o nunca compró, o la última compra es
    # anterior al umbral.
    from django.db.models import Q
    qs = qs.filter(Q(ultima_compra__lt=desde_inactivo) | Q(ultima_compra__isnull=True))

    return qs.iterator()


def _ya_recibio_recordatorio_reciente(cliente, frecuencia_dias: int) -> bool:
    """
    True si al cliente le mandamos un recordatorio dentro de los
    últimos `frecuencia_dias`. Decide si saltearlo en esta corrida.
    """
    from cliente.models import RecordatorioSaldoEnviado
    from datetime import timedelta

    umbral = timezone.now() - timedelta(days=frecuencia_dias)
    return RecordatorioSaldoEnviado.objects.filter(
        cliente=cliente,
        created_at__gte=umbral,
        status=RecordatorioSaldoEnviado.STATUS_ENVIADO,
    ).exists()


# Delay entre envíos. Mismo principio que la difusión de listas:
# compartir setting global del proyecto para que el rate limit aplique
# a TODA la integración con WA.
def _delay() -> float:
    from django.conf import settings
    return float(getattr(settings, 'WHATSAPP_RATE_LIMIT_SECONDS', 3.5))


def procesar_recordatorios_saldo(force_send: bool = False) -> dict:
    """
    Worker entry-point.

    Devuelve un dict con métricas para auditoría:
      {ok, candidatos, enviados, skipped_frecuencia, fallidos, motivo_skip?}

    Si `force_send=True`, ignora el chequeo de frecuencia (útil para
    testing manual desde panel-tareas — el operador sabe lo que hace).
    """
    from configuracion.models import get_config
    from cliente.models import RecordatorioSaldoEnviado

    config = get_config()

    if not config.recordatorios_saldo_habilitado:
        msg = 'recordatorios_saldo_habilitado=False — NO-OP.'
        log.info(msg)
        return {'ok': True, 'candidatos': 0, 'enviados': 0, 'motivo_skip': msg}

    # Chequeo del bot antes de empezar. Si está caído, no tiene sentido
    # ni siquiera consultar la DB — abortamos rápido.
    ready, motivo = wa_client.is_ready()
    if not ready:
        msg = f'wa-bot no disponible: {motivo}'
        log.warning(msg)
        # Marcamos timestamp igual para que se pueda ver "se intentó".
        config.recordatorios_saldo_ultima_corrida_at = timezone.now()
        config.save(update_fields=['recordatorios_saldo_ultima_corrida_at'])
        return {'ok': False, 'enviados': 0, 'error': msg}

    delay = _delay()
    candidatos = 0
    enviados = 0
    skipped = 0
    fallidos = 0

    hoy = timezone.now().date()

    for cliente in _clientes_elegibles(config):
        candidatos += 1

        if not force_send and _ya_recibio_recordatorio_reciente(
            cliente, config.recordatorios_saldo_frecuencia_dias,
        ):
            skipped += 1
            continue

        # Calcular dias_desde_ultima_compra para el render del template
        # y el snapshot de auditoría.
        if cliente.ultima_compra:
            dias = (hoy - cliente.ultima_compra).days
        else:
            dias = None

        mensaje = _render(
            config.recordatorios_saldo_template,
            cliente,
            cliente.saldo_calc,  # viene del annotate
            dias,
        )
        resultado = wa_client.send_text(cliente.whatsapp_number, mensaje)

        if resultado.get('ok'):
            enviados += 1
            RecordatorioSaldoEnviado.objects.create(
                cliente=cliente,
                saldo_snapshot=cliente.saldo_calc,
                dias_desde_ultima_compra=dias,
                status=RecordatorioSaldoEnviado.STATUS_ENVIADO,
            )
        else:
            fallidos += 1
            RecordatorioSaldoEnviado.objects.create(
                cliente=cliente,
                saldo_snapshot=cliente.saldo_calc,
                dias_desde_ultima_compra=dias,
                status=RecordatorioSaldoEnviado.STATUS_FALLIDO,
                error_msg=str(resultado.get('error') or 'sin detalle'),
            )

        if delay > 0:
            time.sleep(delay)

    config.recordatorios_saldo_ultima_corrida_at = timezone.now()
    config.save(update_fields=['recordatorios_saldo_ultima_corrida_at'])

    return {
        'ok': True,
        'candidatos': candidatos,
        'enviados': enviados,
        'skipped_frecuencia': skipped,
        'fallidos': fallidos,
    }


# ---------------------------------------------------------------------------
# Wrapper para django-q2 Schedule / panel de tareas
# ---------------------------------------------------------------------------
def recordatorios_saldo_scheduled() -> str:
    """
    Entry-point para django-q2 Schedule + panel de tareas manuales.
    Devuelve un string-summary para que se vea cómodo en
    `/admin/django_q/success/`.
    """
    log.info('Schedule recordatorios_saldo arranca')
    res = procesar_recordatorios_saldo(force_send=False)
    if not res.get('ok'):
        return f'FAIL: {res.get("error") or res.get("motivo_skip")}'
    return (
        f'OK. candidatos={res.get("candidatos", 0)} '
        f'enviados={res.get("enviados", 0)} '
        f'skipped_frecuencia={res.get("skipped_frecuencia", 0)} '
        f'fallidos={res.get("fallidos", 0)}'
    )
