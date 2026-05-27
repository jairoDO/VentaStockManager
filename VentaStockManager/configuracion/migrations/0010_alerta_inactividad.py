# Generated manually 2026-05-27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('configuracion', '0009_schedule_purgar_auditlog'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuraciongeneral',
            name='alerta_inactividad_habilitada',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Si está prendido, una vez por día se generan alertas internas '
                    'para clientes que dejaron de comprar por más días que el umbral '
                    'de abajo. Solo aplica a clientes que ya compraron alguna vez. '
                    'No manda WhatsApp — es una alerta visible en el admin.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='configuraciongeneral',
            name='alerta_inactividad_dias',
            field=models.PositiveIntegerField(
                default=30,
                help_text=(
                    'Días sin comprar a partir de los cuales un cliente que solía '
                    'comprar se considera "inactivo" y se genera una alerta interna. '
                    'Default 30.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='configuraciongeneral',
            name='alerta_inactividad_ultima_corrida_at',
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                help_text='Cuándo corrió por última vez la detección de inactivos (read-only).',
            ),
        ),
    ]
