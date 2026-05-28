"""
Crea el `Pedido` faltante para las ventas que no lo tienen.

Por qué hace falta:
  - Las ventas importadas del dump de PythonAnywhere pueden haber quedado
    sin su `Pedido` 1:1 (si la tabla de pedidos no se cargó completa, o
    si la venta se creó por un path legacy que no lo generaba).
  - Una venta sin Pedido NO aparece en la bandeja de Pedidos del admin
    ni se le puede generar el PDF/comanda. Editarla en la pantalla nueva
    ahora sí lo crea (ver Venta.save), pero este comando barre de una
    todas las que ya están en la DB.

Idempotente: solo crea pedidos para ventas que no tienen. Correrlo N
veces hace exactamente el mismo trabajo. No toca estado/pagado de
pedidos existentes.

NO fuerza pedido.id == venta.id: deja que Postgres asigne el id para
evitar colisiones con ids de pedidos legacy.

Uso:
    python manage.py crear_pedidos_faltantes          # ejecuta
    python manage.py crear_pedidos_faltantes --dry     # solo cuenta
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from venta.models import Venta, Pedido


class Command(BaseCommand):
    help = 'Crea el Pedido faltante para las ventas que no lo tienen.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry',
            action='store_true',
            help='No persiste cambios, solo cuenta cuántas ventas quedarían afectadas.',
        )

    def handle(self, *args, **options):
        dry = bool(options.get('dry'))

        # Ventas sin pedido asociado. `pedido` es el related_name del
        # OneToOne (venta/models.py). isnull sobre el reverse 1:1 filtra
        # las que no tienen fila en Pedido.
        qs = (
            Venta.objects
            .filter(pedido__isnull=True)
            .only('id')
            .order_by('id')
        )

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nada para hacer: todas las ventas ya tienen su pedido.'))
            return

        if dry:
            self.stdout.write(self.style.WARNING(
                f'[DRY-RUN] {total} ventas sin pedido. Se crearían {total} pedidos.'
            ))
            return

        creados = 0
        # Lotes para no abrir una transacción gigante en una DB remota.
        ids = list(qs.values_list('id', flat=True))
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            with transaction.atomic():
                nuevos = [Pedido(venta_id=vid) for vid in lote]
                Pedido.objects.bulk_create(nuevos)
                creados += len(nuevos)
            self.stdout.write(f'  … {creados}/{total} pedidos creados')

        self.stdout.write(self.style.SUCCESS(
            f'Listo. Pedidos creados: {creados} (de {total} ventas sin pedido).'
        ))
