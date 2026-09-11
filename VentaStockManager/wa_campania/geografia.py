"""Utilidades geográficas para segmentar destinatarios de campañas."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from cliente.models import DireccionCliente


RADIO_MAXIMO_KM = 200.0


def normalizar_zona(filtro):
    """Devuelve ``(latitud, longitud, radio_km)`` o ``None`` si está incompleta."""
    try:
        latitud = float(filtro.get('centro_latitud'))
        longitud = float(filtro.get('centro_longitud'))
        radio_km = float(filtro.get('radio_km'))
    except (AttributeError, TypeError, ValueError):
        return None

    if not (-90 <= latitud <= 90 and -180 <= longitud <= 180):
        return None
    if not (0 < radio_km <= RADIO_MAXIMO_KM):
        return None
    return latitud, longitud, radio_km


def distancia_km(latitud_origen, longitud_origen, latitud_destino, longitud_destino):
    """Distancia Haversine en línea recta entre dos coordenadas."""
    radio_tierra_km = 6371.0
    latitud_origen_rad = radians(float(latitud_origen))
    latitud_destino_rad = radians(float(latitud_destino))
    diferencia_latitud = latitud_destino_rad - latitud_origen_rad
    diferencia_longitud = radians(float(longitud_destino) - float(longitud_origen))
    haversine = (
        sin(diferencia_latitud / 2) ** 2
        + cos(latitud_origen_rad)
        * cos(latitud_destino_rad)
        * sin(diferencia_longitud / 2) ** 2
    )
    return radio_tierra_km * 2 * asin(sqrt(haversine))


def ubicaciones_de_clientes(queryset):
    """
    Elige una dirección con coordenadas por cliente.

    Priorizamos la principal y luego la confirmada. Para la escala actual
    (cientos de clientes) resolver los IDs y una consulta de direcciones es
    más simple y suficientemente rápido, sin requerir PostGIS.
    """
    cliente_ids = list(queryset.order_by().values_list('pk', flat=True).distinct())
    if not cliente_ids:
        return {}

    direcciones = (
        DireccionCliente.objects
        .filter(
            cliente_id__in=cliente_ids,
            latitud__isnull=False,
            longitud__isnull=False,
        )
        .order_by(
            'cliente_id', '-es_principal', '-confirmada',
            '-actualizada_en', '-id',
        )
    )
    ubicaciones = {}
    for direccion in direcciones.iterator():
        if direccion.cliente_id in ubicaciones:
            continue
        ubicaciones[direccion.cliente_id] = {
            'latitud': float(direccion.latitud),
            'longitud': float(direccion.longitud),
            'direccion': direccion.direccion_texto,
            'localidad': direccion.localidad,
        }
    return ubicaciones


def aplicar_zona(queryset, filtro):
    """
    Aplica el radio al queryset y devuelve metadatos útiles para el mapa.

    Retorna ``(queryset_filtrado, ubicaciones, zona, sin_coordenadas)``.
    Si no hay zona válida, no filtra y devuelve todas las ubicaciones.
    """
    zona = normalizar_zona(filtro)
    ubicaciones = ubicaciones_de_clientes(queryset)
    total_clientes = queryset.order_by().values('pk').distinct().count()
    sin_coordenadas = max(0, total_clientes - len(ubicaciones))
    if zona is None:
        return queryset, ubicaciones, None, sin_coordenadas

    centro_latitud, centro_longitud, radio_km = zona
    incluidas = {}
    for cliente_id, ubicacion in ubicaciones.items():
        distancia = distancia_km(
            centro_latitud,
            centro_longitud,
            ubicacion['latitud'],
            ubicacion['longitud'],
        )
        if distancia <= radio_km:
            incluidas[cliente_id] = {**ubicacion, 'distancia_km': distancia}

    return (
        queryset.filter(pk__in=incluidas.keys()),
        incluidas,
        zona,
        sin_coordenadas,
    )
