"""
Marca como archivadas las ventas con más de N meses (default 18).

Diseñado para correr como cron (django-q2 schedule semanal). Es
idempotente: las ventas ya archivadas no se vuelven a tocar. Solo
marca; NO borra data ni adjuntos.

Uso:
    python manage.py archivar_ventas_antiguas
    python manage.py archivar_ventas_antiguas --meses 24
    python manage.py archivar_ventas_antiguas --dry-run

Reporta cuántas ventas marcó.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from venta.models import Venta


class Command(BaseCommand):
    help = 'Marca como archivadas las ventas con fecha_compra > N meses.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--meses',
            type=int,
            default=None,
            help=(
                'Antigüedad mínima en meses para archivar. Si no se '
                'pasa, usa `VENTAS_RETENCION_MESES` del settings '
                '(default 18).'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No guarda nada, solo cuenta y reporta.',
        )

    def handle(self, *args, **options):
        # Orden de precedencia:
        #   1. Flag explícito `--meses` (override puntual del operador).
        #   2. Configuración runtime editable desde el admin.
        #   3. Setting/env var como último fallback.
        # Esto deja que Osvaldo cambie el valor desde el admin sin
        # reiniciar nada, pero podés override-arlo a mano si querés
        # correr un archivado custom una sola vez.
        if options['meses']:
            meses = options['meses']
        else:
            try:
                from configuracion.models import get_config
                meses = get_config().ventas_retencion_meses
            except Exception:
                # Si la app `configuracion` no está disponible
                # (por ejemplo migrate todavía no aplicado), caemos
                # al setting estático.
                meses = getattr(settings, 'VENTAS_RETENCION_MESES', 18)
        # 30 días/mes es aproximación intencional; preferimos
        # simpleza sobre precisión calendárica. Una venta de hace
        # exactamente 18 meses puede quedar dentro o fuera por unos
        # días, no es problema.
        umbral = timezone.now().date() - timedelta(days=meses * 30)

        qs = Venta.objects.filter(
            fecha_compra__lt=umbral,
            archivada_en__isnull=True,
        )
        total = qs.count()

        self.stdout.write(
            f'Ventas anteriores a {umbral} y no archivadas: {total}'
        )

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nada que hacer.'))
            return

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN — no se guarda.'))
            return

        # Update masivo: rápido y atómico. NO usamos save() para no
        # disparar el override que crea Pedido (ya existe) y para
        # mantener el `updated_at` de auditlog limpio (el cambio es
        # operacional, no editorial).
        actualizadas = qs.update(archivada_en=timezone.now())
        self.stdout.write(self.style.SUCCESS(
            f'Listo. Archivadas: {actualizadas} ventas.'
        ))
