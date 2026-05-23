"""
Purga registros de auditoría (django-auditlog.LogEntry) más antiguos
que `auditlog_retencion_dias` del singleton ConfiguracionGeneral.

Sin esta tarea, la tabla `auditlog_logentry` crece sin límite — en
un kiosko con cientos de ventas mensuales, son millones de filas en
1-2 años y degrada queries.

Uso:
    python manage.py purgar_auditlog_antiguos              # corre real
    python manage.py purgar_auditlog_antiguos --dry-run    # solo cuenta
    python manage.py purgar_auditlog_antiguos --dias 30    # override config

El comando se ejecuta automáticamente por django-q (ver
configuracion.tasks_auditlog.purgar_auditlog_scheduled) cada semana
o el día que el operador configure desde el admin.

Idempotente: correrlo dos veces seguidas, la segunda no borra nada
porque ya pasó el corte.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = (
        'Borra registros de auditoría (auditlog.LogEntry) más viejos '
        'que el número de días configurado en ConfiguracionGeneral. '
        'Default 180 (6 meses).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Solo cuenta cuántos se borrarían, sin tocar la DB.',
        )
        parser.add_argument(
            '--dias', type=int, default=None,
            help=(
                'Override de la retención en días. Si no se pasa, usa '
                'auditlog_retencion_dias del singleton.'
            ),
        )
        parser.add_argument(
            '--force', action='store_true',
            help=(
                'Ignora el flag `auditlog_purge_habilitado=False` del '
                'singleton. Útil para purgar a mano una vez aunque la '
                'task automática esté apagada.'
            ),
        )

    def handle(self, *args, **options):
        from auditlog.models import LogEntry
        from configuracion.models import get_config

        cfg = get_config()

        # Si está deshabilitado y no viene --force, salir sin tocar nada.
        if not cfg.auditlog_purge_habilitado and not options['force']:
            self.stdout.write(self.style.WARNING(
                'auditlog_purge_habilitado=False — purga DESACTIVADA en el '
                'singleton. Usá --force para correr de todas formas.'
            ))
            return

        dias = options['dias'] if options['dias'] else cfg.auditlog_retencion_dias
        if dias <= 0:
            self.stdout.write(self.style.ERROR(
                f'retención inválida: {dias} días. Tiene que ser > 0.'
            ))
            return

        corte = timezone.now() - timedelta(days=dias)

        # Queryset de los que se van a borrar — usamos timestamp del LogEntry.
        qs = LogEntry.objects.filter(timestamp__lt=corte)
        total = qs.count()

        if options['dry_run']:
            self.stdout.write(self.style.NOTICE(
                f'DRY RUN — borraría {total} registros con timestamp < {corte:%Y-%m-%d %H:%M}. '
                f'(retención: {dias} días)'
            ))
            return

        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                f'Nada para borrar — no hay LogEntry más viejos que {dias} días.'
            ))
            # Igual actualizamos el timestamp de "última corrida" para
            # que el operador vea que la task corrió.
            cfg.auditlog_ultima_purga_at = timezone.now()
            cfg.auditlog_ultima_purga_borrados = 0
            cfg.save(update_fields=['auditlog_ultima_purga_at', 'auditlog_ultima_purga_borrados'])
            return

        # Borrar. _raw_delete() saltea signals (más rápido en bulk grande)
        # pero queremos los signals para auditoría — usamos .delete() normal.
        deleted_count, _ = qs.delete()

        # Actualizar metadata en el singleton.
        cfg.auditlog_ultima_purga_at = timezone.now()
        cfg.auditlog_ultima_purga_borrados = deleted_count
        cfg.save(update_fields=['auditlog_ultima_purga_at', 'auditlog_ultima_purga_borrados'])

        self.stdout.write(self.style.SUCCESS(
            f'✓ Borrados {deleted_count} registros de auditoría más viejos '
            f'que {dias} días (corte: {corte:%Y-%m-%d %H:%M}).'
        ))
