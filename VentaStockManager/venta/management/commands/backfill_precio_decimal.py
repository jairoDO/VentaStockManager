"""
Backfill silencioso del campo `ArticuloVenta.precio_decimal`.

Recorre todos los `ArticuloVenta` con `precio_decimal IS NULL`
y los completa parseando el CharField `precio` con `parse_precio`.

Diseñado para ser idempotente: se puede correr N veces, solo
procesa los que todavía están en NULL.

Uso:
    python manage.py backfill_precio_decimal
    python manage.py backfill_precio_decimal --batch-size 500
    python manage.py backfill_precio_decimal --dry-run

Reporta al final:
  - Cuántos procesó
  - Cuántos quedaron en Decimal('0') (fila probablemente corrupta)
  - Una muestra de los precios crudos que cayeron a 0
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal

from venta.models import ArticuloVenta
from venta.utils import parse_precio


class Command(BaseCommand):
    help = 'Rellena precio_decimal en ArticuloVenta a partir del CharField legacy.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Cuántos registros procesar por bulk_update (default: 1000).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No guarda nada, solo cuenta y reporta.',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        dry_run = options['dry_run']

        qs = ArticuloVenta.objects.filter(precio_decimal__isnull=True).only(
            'id', 'precio'
        )
        total_pendiente = qs.count()
        self.stdout.write(
            f'Pendientes de backfill: {total_pendiente} ArticuloVenta'
        )
        if total_pendiente == 0:
            self.stdout.write(self.style.SUCCESS('Nada que hacer.'))
            return

        procesados = 0
        ceros = 0
        muestra_ceros = []  # primeros 10 precios crudos que parsearon a 0
        batch = []

        # iterator() evita cargar todo en memoria; chunk_size pacta
        # con el server fetch en lotes razonables.
        for av in qs.iterator(chunk_size=batch_size):
            decimal_val = parse_precio(av.precio)
            av.precio_decimal = decimal_val
            if decimal_val == Decimal('0') and av.precio:
                ceros += 1
                if len(muestra_ceros) < 10:
                    muestra_ceros.append((av.id, repr(av.precio)))
            batch.append(av)

            if len(batch) >= batch_size:
                if not dry_run:
                    ArticuloVenta.objects.bulk_update(
                        batch, ['precio_decimal'], batch_size=batch_size
                    )
                procesados += len(batch)
                self.stdout.write(f'  {procesados}/{total_pendiente}')
                batch = []

        # último lote
        if batch:
            if not dry_run:
                ArticuloVenta.objects.bulk_update(
                    batch, ['precio_decimal'], batch_size=batch_size
                )
            procesados += len(batch)

        suffix = ' (DRY RUN)' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'Listo{suffix}. Procesados: {procesados}. '
            f'Caídos a 0 (probable dato corrupto): {ceros}.'
        ))
        if muestra_ceros:
            self.stdout.write('Muestra de precios que parsearon a 0:')
            for av_id, precio_raw in muestra_ceros:
                self.stdout.write(f'  id={av_id} precio={precio_raw}')
