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

Flags:
  --pisar      Sobreescribe whatsapp_number en TODOS los clientes con
               telefono (no solo los vacíos).
  --habilitar  Prende `puede_recibir_whatsapp=True` para los clientes
               que terminan con un whatsapp_number válido (no vacío).
               Por default este flag está APAGADO porque el opt-in es
               una decisión de consentimiento — usalo solo si el dueño
               del negocio confirmó que asume el riesgo de mandar WA a
               todos los clientes con teléfono.
  --dry        Simula sin persistir.

Uso:
    python manage.py backfill_whatsapp_number                       # completa vacíos
    python manage.py backfill_whatsapp_number --dry                  # simula
    python manage.py backfill_whatsapp_number --pisar                # pisa TODOS con telefono
    python manage.py backfill_whatsapp_number --pisar --habilitar    # + prende opt-in
    python manage.py backfill_whatsapp_number --habilitar            # solo prende opt-in
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
        parser.add_argument(
            '--habilitar',
            action='store_true',
            help=(
                'Habilita `puede_recibir_whatsapp=True` para los clientes '
                'que quedan con un whatsapp_number válido. Default OFF '
                'porque el opt-in es una decisión de consentimiento.'
            ),
        )

    def handle(self, *args, **options):
        dry = bool(options.get('dry'))
        pisar = bool(options.get('pisar'))
        habilitar = bool(options.get('habilitar'))
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
        if pisar and total:
            self.stdout.write(self.style.WARNING(
                f'Modo PISAR: se reescribirá whatsapp_number en {total} clientes con telefono '
                f'(incluso los que ya tenían WA cargado a mano).'
            ))

        # ------------------------------------------------------------------
        # Pasada 1: telefono → whatsapp_number
        # ------------------------------------------------------------------
        actualizados = 0
        sin_cambio = 0
        sin_match = 0
        # Para que la pasada de --habilitar no toque clientes a los que
        # acabamos de poner WA acá pero quedaron con habilitar=True ya.
        # (No es estrictamente necesario, es solo claridad.)
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
        if total:
            self.stdout.write(self.style.SUCCESS(
                f'{prefijo}WA number — Procesados: {total} | Actualizados: {actualizados} | '
                f'Ya estaban igual: {sin_cambio} | Sin match: {sin_match}'
            ))
            if sin_match:
                self.stdout.write(self.style.WARNING(
                    f'{sin_match} clientes tienen `telefono` pero el normalizador no pudo inferir un número WA válido '
                    f'(ambiguo, demasiado corto, etc.). Revisalos a mano en /admin/cliente/cliente/.'
                ))

        # ------------------------------------------------------------------
        # Pasada 2: habilitar opt-in para los que quedaron con WA válido
        # ------------------------------------------------------------------
        # Esta pasada es OPCIONAL (--habilitar). Aplica a TODOS los clientes
        # con whatsapp_number no vacío y opt-in en False, no solo a los que
        # tocamos en la pasada 1. Eso cubre clientes que ya tenían WA
        # cargado por otra vía pero seguían sin opt-in.
        #
        # OJO LEGAL: prender opt-in en masa salta el patrón de consentimiento
        # individual. El dueño del negocio asume el riesgo — es una decisión
        # explícita por usar este flag.
        if habilitar:
            opt_qs = Cliente.objects.filter(puede_recibir_whatsapp=False).exclude(whatsapp_number='')
            n_opt = opt_qs.count()
            if n_opt == 0:
                self.stdout.write(self.style.SUCCESS(
                    f'{prefijo}Opt-in: nadie más para habilitar (todos los clientes con WA ya están habilitados).'
                ))
            else:
                if dry:
                    self.stdout.write(self.style.WARNING(
                        f'{prefijo}Opt-in: {n_opt} clientes con WA válido pasarían a puede_recibir_whatsapp=True.'
                    ))
                else:
                    # bulk update — un solo UPDATE en la DB, no carga modelos.
                    # Auditlog NO captura update() (solo .save()). Si necesitás
                    # auditoría, hay que iterar; preferimos la performance acá
                    # porque el operador asume el cambio en masa explícitamente.
                    opt_qs.update(puede_recibir_whatsapp=True)
                    self.stdout.write(self.style.SUCCESS(
                        f'Opt-in: {n_opt} clientes habilitados (puede_recibir_whatsapp=True).'
                    ))

        # Caso especial: ningún cambio a hacer en ninguna pasada.
        if total == 0 and not habilitar:
            self.stdout.write(self.style.SUCCESS(
                'Nada para hacer: todos los clientes ya tienen whatsapp_number o no tienen telefono.'
            ))
