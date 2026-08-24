"""
Resolver de audiencia para una `Campania`.

Toma el dict `audiencia_filtro` que se guardó en la Campania y devuelve
un QuerySet de Cliente. Lo separamos del modelo porque el resolver toca
varias apps (cliente, venta, cuenta corriente) y queremos mantener el
modelo enfocado.

Reglas de combinación:
  - `solo_con_whatsapp_valido=True` siempre se aplica al final.
  - El resto son filtros AND. Si todos están en False/null, devuelve
    queryset vacío (excepto si `todos=True`).
  - `todos=True` bypassea cualquier otro filtro (es la red de
    seguridad: si el admin marca "todos", quiere todos).
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Exists, OuterRef, QuerySet, Sum
from django.utils import timezone

from cliente.models import Cliente, MovimientoCuenta
from venta.models import Venta


def resolver_clientes(filtro: dict) -> QuerySet[Cliente]:
    """
    Aplica los filtros del dict y devuelve los clientes destinatarios.
    """
    f = filtro or {}
    qs = Cliente.objects.all()

    # Selección manual: si hay IDs, representan la audiencia exacta.
    # Las reglas de consentimiento y número válido se siguen aplicando
    # más abajo; nunca se pueden saltear desde el widget.
    clientes_ids = f.get('clientes_ids') or []
    if clientes_ids:
        ids_validos = []
        for cliente_id in clientes_ids:
            try:
                ids_validos.append(int(cliente_id))
            except (TypeError, ValueError):
                continue
        qs = qs.filter(pk__in=ids_validos)

    if clientes_ids:
        pass
    elif f.get('todos'):
        # Bypass de los otros filtros, pero respetamos
        # `solo_con_whatsapp_valido` al final.
        pass
    else:
        # AND de filtros opcionales.
        condiciones_aplicadas = False

        dias = f.get('compraron_ultimos_dias')
        if dias:
            desde = timezone.now().date() - timedelta(days=int(dias))
            ventas_recientes = Venta.objects.filter(
                cliente=OuterRef('pk'),
                fecha_compra__gte=desde,
            )
            qs = qs.filter(Exists(ventas_recientes))
            condiciones_aplicadas = True

        # Saldo: lo calculamos con un annotate sumando MovimientoCuenta.
        # No usamos la property `Cliente.saldo` porque eso obliga a
        # iterar en Python; queremos quedarnos en SQL.
        if f.get('con_saldo_a_favor') or f.get('con_saldo_deudor'):
            qs = qs.annotate(
                saldo_calc=Sum('cuenta__movimientos__monto'),
            )
            if f.get('con_saldo_a_favor'):
                qs = qs.filter(saldo_calc__gt=0)
                condiciones_aplicadas = True
            if f.get('con_saldo_deudor'):
                qs = qs.filter(saldo_calc__lt=0)
                condiciones_aplicadas = True

        if not condiciones_aplicadas:
            # Ninguna condición + `todos` en False = no mandamos a
            # nadie. Esto previene el bug de "guardé filtros vacíos y
            # mandó a todos por error".
            return qs.none()

    # Filtro final: solo clientes con número usable.
    if f.get('solo_con_whatsapp_valido', True):
        qs = qs.exclude(whatsapp_number='')

    # Consentimiento: NUNCA pasamos por encima de `puede_recibir_whatsapp`.
    # Es no negociable, no depende del filtro del admin. Si un cliente
    # marcó que no, no recibe — punto. Esto cubre tanto el respeto al
    # cliente como las leyes anti-spam (ley AR de defensa del consumidor).
    qs = qs.filter(puede_recibir_whatsapp=True)

    return qs.order_by('id')
