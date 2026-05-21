"""
Helpers para calcular el precio efectivo de un artículo para un
cliente dado, respetando la cadena completa:

    precio_efectivo = aplicar_descuento_lista(
        aplicar_precio_pactado_si_existe(precio_minorista_articulo)
    )

La función es UN solo lugar para que la pantalla custom de lista de
precios y el PDF generen siempre los MISMOS números (sino tendríamos
listas online que no coinciden con el PDF impreso, drama clásico).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional


CIEN = Decimal('100')


def precio_efectivo(
    articulo,
    cliente,
    descuento_lista: Optional[Decimal] = None,
    *,
    precios_pactados_map: Optional[dict] = None,
    tipo_ajuste: str = 'descuento',
) -> Decimal:
    """
    Devuelve el precio que se le cobra a `cliente` por `articulo`,
    aplicando en orden:

      1. Si hay `PrecioCliente(cliente, articulo)` → ese precio.
         Si no, `articulo.precio_minorista` (fallback para listas
         de precios — el mayorista solo aplica cuando hay umbral
         de cantidad, lo cual no tiene sentido en una lista parcial).

      2. Sobre eso, aplica el ajuste de la lista:
           - tipo_ajuste='descuento' → factor = (100 - pct) / 100
           - tipo_ajuste='aumento'   → factor = (100 + pct) / 100

    `precios_pactados_map` (opcional): si vas a calcular el precio
    de MUCHOS artículos del mismo cliente (como en la pantalla
    custom o el PDF), pasá un dict `{articulo_id: PrecioCliente}`
    pre-cargado para evitar N+1. Si no lo pasás, hacemos una
    query por artículo.

    `tipo_ajuste`: viene del modelo `ListaPrecios.tipo_ajuste`. Default
    'descuento' por compatibilidad con callers viejos.
    """
    # Resolver precio base (con PrecioCliente o minorista).
    precio_base: Optional[Decimal] = None

    if precios_pactados_map is not None:
        # Modo bulk: el caller ya cargó los pactados.
        pactado = precios_pactados_map.get(articulo.id)
        if pactado is not None:
            precio_base = pactado.precio_unitario
    else:
        # Modo lento (1 query): solo usar si es un cálculo individual.
        from cliente.models import PrecioCliente
        pactado = (
            PrecioCliente.objects
            .filter(cliente=cliente, articulo=articulo)
            .first()
        )
        if pactado is not None:
            precio_base = pactado.precio_unitario

    if precio_base is None:
        precio_base = articulo.precio_minorista or Decimal('0')

    # Aplicar ajuste de la lista, si vino.
    if descuento_lista is None:
        descuento_lista = Decimal('0')
    if descuento_lista <= 0:
        return precio_base.quantize(Decimal('0.01'))

    # El campo conceptualmente es "magnitud del ajuste", siempre positivo.
    # El signo lo da `tipo_ajuste`.
    if tipo_ajuste == 'aumento':
        factor = (CIEN + descuento_lista) / CIEN
    else:
        factor = (CIEN - descuento_lista) / CIEN
    return (precio_base * factor).quantize(Decimal('0.01'))


def cargar_precios_pactados(cliente, articulos) -> dict:
    """
    Devuelve un dict `{articulo_id: PrecioCliente}` con todos los
    precios pactados que ese cliente tiene sobre los artículos del
    queryset/list. Una sola query.

    Útil como precomputación antes de llamar a `precio_efectivo` en
    loop para muchos artículos (ej. al armar el PDF de una lista).
    """
    from cliente.models import PrecioCliente

    if not cliente or not articulos:
        return {}

    ids = [a.id for a in articulos]
    qs = PrecioCliente.objects.filter(cliente=cliente, articulo_id__in=ids)
    return {p.articulo_id: p for p in qs}
