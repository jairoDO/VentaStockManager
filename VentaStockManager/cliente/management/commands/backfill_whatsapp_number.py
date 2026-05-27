"""
Re-corre el backfill de `Cliente.telefono` → `Cliente.whatsapp_number`
para los clientes que aún tienen `whatsapp_number=''`.

Lo necesitamos porque:

  - La migración `0006_backfill_whatsapp_number` corrió una sola vez
    (al deploy). Clientes cargados DESPUÉS pasaron por el path legacy
    sin auto-normalización (hasta que agregamos Cliente.save() en
    2026-05).

  - Bug reportado: en la pantalla "Difundir lista" no aparecían clientes
    con `telefono` cargado pero `whatsapp_number=''`. Este comando
    barre todos los pendientes.

Idempotente: corrérlo N veces hace exactamente el mismo trabajo.
Solo toca filas con `whatsapp_number=''`. NO modifica el opt-in
(`puede_recibir_whatsapp`) — eso requiere consentimiento explícito
y lo decide el admin a mano.

Uso:
    python manage.py backfill_whatsapp_number              # completa vacíos
    python manage.py backfill_whatsapp_number --dry        # solo simula
    python manage.py backfill_whatsapp_number --pisar      # pisa TODOS con telefono
    python manage.py backfill_whatsapp_number --pisar --dry  # simula el pisar
"""

from django.core.management.base import BaseCommand

from cliente.models import Cliente
from cliente.phone_utils import normalizar_telefono_ar


class Command(BaseCommand):
    help = 'Backfill whatsapp_number desde telefono para clientes legacy.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry',
            action='store_true',
            help='No persiste cambios, solo cuenta cuántos serían afectados.',
        )
        parser.add_argument(
            '--pisar',
            action='store_true',
            help=(
                'Procesa TODOS los clientes con telefono (no solo los que '
                'tienen whatsapp_number vacío) y SOBREESCRIBE el '
                'whatsapp_number con el derivado del telefono. Sin este '
                'flag, solo completa los whatsapp_number vacíos.'
            ),
        )

    def handle(self, *args, **options):
        dry = bool(options.get('dry'))
        pisar = bool(options.get('pisar'))
        # Base: clientes con telefono no vacío. `telefono='00000000'` o
        # solo ceros queda incluido pero el normalizer devuelve '' y
        # skip-eamos.
        qs = (
            Cliente.objects
            .exclude(telefono__isnull=True)
            .exclude(telefono='')
            .only('id', 'telefono', 'whatsapp_number')
        )
        if not pisar:
            # Modo default: solo completar los whatsapp_number vacíos,
            # nunca pisar lo cargado a mano.
            qs = qs.filter(whatsapp_number='')

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nada para hacer: todos los clientes ya tienen whatsapp_number o no tienen telefono.'))
            return

        if pisar:
            self.stdout.write(self.style.WARNING(
                f'Modo PISAR: se reescribirá whatsapp_number en {total} clientes con telefono '
                f'(incluso los que ya tenían WA cargado a mano).'
            ))

        actualizados = 0
        sin_cambio = 0
        sin_match = 0
        for c in qs.iterator(chunk_size=500):
            normalizado = normalizar_telefono_ar(c.telefono or '')
            if not normalizado:
                sin_match += 1
                continue
            # Si ya está igual, no toques (evita ruido de auditoría).
            if c.whatsapp_number == normalizado:
                sin_cambio += 1
                continue
            if dry:
                actualizados += 1
                continue
            c.whatsapp_number = normalizado
            # update_fields para no disparar el save() entero (que ya
            # incluiría esta lógica, pero también auditoría más pesada).
            c.save(update_fields=['whatsapp_number'])
            actualizados += 1

        prefijo = '[DRY-RUN] ' if dry else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefijo}Procesados: {total} | Actualizados: {actualizados} | '
            f'Ya estaban igual: {sin_cambio} | Sin match: {sin_match}'
        ))
        if sin_match:
            self.stdout.write(self.style.WARNING(
                f'{sin_match} clientes tienen `telefono` pero el normalizador no pudo inferir un número WA válido '
                f'(ambiguo, demasiado corto, etc.). Revisalos a mano en /admin/cliente/cliente/.'
            ))
