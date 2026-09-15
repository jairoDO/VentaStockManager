"""Estimación y métricas de las rutas comerciales semanales."""

from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from cliente.models import Cliente, RutaSemanalCliente
from venta.models import Venta


def recalcular_rutas_sugeridas(dias=90):
    """Crea/actualiza sugerencias sin pisar las asignaciones manuales."""
    desde = timezone.now().date() - timedelta(days=dias)
    clientes = {
        cliente.pk: cliente.vendedor_asignado_id
        for cliente in Cliente.objects.only('id', 'vendedor_asignado_id')
    }
    ventas = Venta.objects.filter(fecha_compra__gte=desde).values_list(
        'cliente_id', 'vendedor_id', 'fecha_compra',
    )
    evidencia = defaultdict(list)
    for cliente_id, vendedor_id, fecha in ventas.iterator():
        evidencia[cliente_id].append((vendedor_id, fecha.weekday(), fecha))

    creadas = actualizadas = omitidas = 0
    with transaction.atomic():
        for cliente_id, registros in evidencia.items():
            vendedor_asignado_id = clientes.get(cliente_id)
            propios = [r for r in registros if r[0] == vendedor_asignado_id]
            considerados = propios or registros
            frecuencias = Counter((r[0], r[1]) for r in considerados)
            # En empate gana la combinación observada más recientemente.
            ultima_fecha = {
                clave: max(r[2] for r in considerados if (r[0], r[1]) == clave)
                for clave in frecuencias
            }
            (vendedor_id, dia), cantidad = max(
                frecuencias.items(), key=lambda item: (item[1], ultima_fecha[item[0]]),
            )
            confianza = Decimal(cantidad * 100 / len(considerados)).quantize(Decimal('0.01'))
            ruta = RutaSemanalCliente.objects.filter(cliente_id=cliente_id).first()
            if ruta and not ruta.sugerida:
                omitidas += 1
                continue
            _, creada = RutaSemanalCliente.objects.update_or_create(
                cliente_id=cliente_id,
                defaults={
                    'vendedor_id': vendedor_id,
                    'dia_semana': dia,
                    'sugerida': True,
                    'confianza': confianza,
                    'ventas_analizadas': len(considerados),
                },
            )
            creadas += int(creada)
            actualizadas += int(not creada)
    return {
        'creadas': creadas,
        'actualizadas': actualizadas,
        'manuales_preservadas': omitidas,
        'desde': desde,
    }


def estadisticas_visitas(anios=4):
    """Mediana/promedio de clientes únicos atendidos por vendedor y weekday."""
    desde = timezone.now().date() - timedelta(days=365 * anios)
    filas = (
        Venta.objects.filter(fecha_compra__gte=desde)
        .values('vendedor_id', 'fecha_compra')
        .annotate(clientes=Count('cliente_id', distinct=True))
    )
    muestras = defaultdict(list)
    for fila in filas:
        muestras[(fila['vendedor_id'], fila['fecha_compra'].weekday())].append(
            fila['clientes'],
        )
    return {
        clave: {
            'dias_con_ventas': len(valores),
            'promedio': round(sum(valores) / len(valores), 1),
            'mediana': round(median(valores), 1),
        }
        for clave, valores in muestras.items()
        if valores
    }
