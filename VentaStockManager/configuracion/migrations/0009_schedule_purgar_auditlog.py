"""
Crea (o asegura) el Schedule de django-q2 para purgar registros de
auditoría más viejos que `auditlog_retencion_dias` del singleton.

Frecuencia: semanal. La tabla auditlog_logentry no crece tan rápido
como para necesitar limpieza diaria — semanal alcanza para mantener
el tamaño acotado sin pegarle a la DB todo el tiempo.

Idempotente: si el Schedule ya existe, no crea uno nuevo.
"""

from django.db import migrations


def crear_schedule(apps, schema_editor):
    try:
        Schedule = apps.get_model('django_q', 'Schedule')
    except LookupError:
        # django-q2 no está instalado todavía en tests muy temprano.
        return

    func_path = 'configuracion.tasks_auditlog.purgar_auditlog_scheduled'
    if Schedule.objects.filter(func=func_path).exists():
        return

    Schedule.objects.create(
        name='purgar_auditlog_antiguos',
        func=func_path,
        schedule_type='W',  # Weekly — alcanza para mantener el tamaño bajo
        repeats=-1,
    )


def borrar_schedule(apps, schema_editor):
    try:
        Schedule = apps.get_model('django_q', 'Schedule')
    except LookupError:
        return
    Schedule.objects.filter(
        func='configuracion.tasks_auditlog.purgar_auditlog_scheduled',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('configuracion', '0008_auditlog_purge'),
        # Mismo gotcha que el otro Schedule: depender de la versión
        # con `name` en Schedule, no de 0001_initial.
        ('django_q', '0018_task_success_index'),
    ]

    operations = [
        migrations.RunPython(crear_schedule, borrar_schedule),
    ]
