"""
Crea (o asegura) el Schedule de django-q2 para los recordatorios de
saldo deudor.

El cron lo dejamos en DAILY: el filtro de frecuencia
(`recordatorios_saldo_frecuencia_dias` en ConfiguracionGeneral) decide
realmente cada cuánto va a llegarle a un mismo cliente. Default de la
config es 7 días — el operador puede subirlo si quiere algo más laxo.

NO depende de cuándo cada cliente recibió su último recordatorio: cada
corrida vuelve a evaluar quiénes son candidatos y respeta la ventana.

Idempotente: si el Schedule ya existe, no crea uno nuevo.
"""

from django.db import migrations


def crear_schedule(apps, schema_editor):
    try:
        Schedule = apps.get_model('django_q', 'Schedule')
    except LookupError:
        # django-q2 no está instalado todavía en tests muy temprano.
        return

    func_path = 'cliente.tasks_recordatorios.recordatorios_saldo_scheduled'
    if Schedule.objects.filter(func=func_path).exists():
        return

    Schedule.objects.create(
        name='recordatorios_saldo_deudor',
        func=func_path,
        schedule_type='D',  # daily
        repeats=-1,
    )


def borrar_schedule(apps, schema_editor):
    try:
        Schedule = apps.get_model('django_q', 'Schedule')
    except LookupError:
        return
    Schedule.objects.filter(
        func='cliente.tasks_recordatorios.recordatorios_saldo_scheduled',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0009_recordatoriosaldoenviado'),
        # OJO: depender de la ÚLTIMA migración de django_q (no de
        # 0001_initial). El campo `name` se agregó al modelo Schedule
        # en una migración posterior; si dependemos solo de 0001 el
        # `apps.get_model('django_q', 'Schedule')` devuelve el estado
        # frozen sin ese campo y el create() explota con TypeError.
        ('django_q', '0018_task_success_index'),
    ]

    operations = [
        migrations.RunPython(crear_schedule, borrar_schedule),
    ]
