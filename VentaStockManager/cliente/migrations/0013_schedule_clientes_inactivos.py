"""
Crea (o asegura) el Schedule de django-q2 para la detección diaria de
clientes inactivos.

Corre una vez por día (schedule_type='D'). El umbral de días lo decide
`alerta_inactividad_dias` en ConfiguracionGeneral, y el master flag
`alerta_inactividad_habilitada` puede apagar la generación sin tocar el
schedule (la task hace NO-OP).

Idempotente: si el Schedule ya existe, no crea uno nuevo.
"""

from django.db import migrations


FUNC_PATH = 'cliente.tasks_inactividad.clientes_inactivos_scheduled'


def crear_schedule(apps, schema_editor):
    try:
        Schedule = apps.get_model('django_q', 'Schedule')
    except LookupError:
        return

    if Schedule.objects.filter(func=FUNC_PATH).exists():
        return

    Schedule.objects.create(
        name='deteccion_clientes_inactivos',
        func=FUNC_PATH,
        schedule_type='D',  # daily
        repeats=-1,
    )


def borrar_schedule(apps, schema_editor):
    try:
        Schedule = apps.get_model('django_q', 'Schedule')
    except LookupError:
        return
    Schedule.objects.filter(func=FUNC_PATH).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0012_alertaclienteinactivo'),
        ('django_q', '0018_task_success_index'),
    ]

    operations = [
        migrations.RunPython(crear_schedule, borrar_schedule),
    ]
