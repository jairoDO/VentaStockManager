"""
Crea (si no existe) un Schedule de django-q2 para archivar ventas
antiguas semanalmente.

Idempotente: si el Schedule ya está creado (por ejemplo si la migración
se aplica dos veces, o si el operador ya lo manipuló desde el admin),
NO lo pisamos. Solo creamos uno nuevo si la tabla no tiene ninguno
con `name='archivar_ventas_antiguas'`.

Reversible: si se hace rollback, borramos el Schedule (no su histórico
de tasks ejecutadas, eso queda).

La frecuencia default es semanal (cada 7 días). Cualquiera la puede
ajustar desde `/admin/django_q/schedule/` sin tocar código — por eso
NO marcamos `next_run` exacto, dejamos que django-q calcule.
"""

from datetime import timedelta

from django.db import migrations
from django.utils import timezone


SCHEDULE_NAME = 'archivar_ventas_antiguas'
SCHEDULE_FUNC = 'venta.tasks.archivar_ventas_antiguas_scheduled'


def crear_schedule(apps, schema_editor):
    Schedule = apps.get_model('django_q', 'Schedule')
    if Schedule.objects.filter(name=SCHEDULE_NAME).exists():
        print(f'  Schedule "{SCHEDULE_NAME}" ya existe, no se crea otro.')
        return
    # `next_run` arranca mañana a las 3am: el archivado es liviano
    # pero por las dudas evitamos la franja diurna donde el operador
    # pueda estar usando la app.
    ahora = timezone.now()
    proxima = (ahora + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
    Schedule.objects.create(
        name=SCHEDULE_NAME,
        func=SCHEDULE_FUNC,
        # django-q2 schedule_type='W' = weekly. Otros valores: 'O' once,
        # 'I' minutes, 'H' hourly, 'D' daily, 'M' monthly, 'Q' quarterly,
        # 'Y' yearly, 'C' cron.
        schedule_type='W',
        repeats=-1,  # repetir indefinidamente
        next_run=proxima,
    )
    print(f'  Schedule "{SCHEDULE_NAME}" creado. Próxima ejecución: {proxima}')


def borrar_schedule(apps, schema_editor):
    Schedule = apps.get_model('django_q', 'Schedule')
    n = Schedule.objects.filter(name=SCHEDULE_NAME).delete()[0]
    print(f'  Schedule "{SCHEDULE_NAME}" borrado ({n} fila/s).')


class Migration(migrations.Migration):

    dependencies = [
        ('venta', '0004_venta_archivada_en'),
        # Necesitamos que django_q tenga sus tablas creadas, sino
        # `apps.get_model('django_q', 'Schedule')` falla.
        ('django_q', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(crear_schedule, borrar_schedule),
    ]
