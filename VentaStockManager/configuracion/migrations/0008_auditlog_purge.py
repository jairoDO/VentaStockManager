"""
Sumar campos al singleton para la purga periódica del audit log de
django-auditlog. Sin esto la tabla auditlog_logentry crece sin límite.

Default: retención de 180 días (6 meses), purga habilitada por defecto.
El operador puede ajustar desde /admin/configuracion/configuraciongeneral/.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('configuracion', '0007_configuraciongeneral_auto_responder_habilitado'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuraciongeneral',
            name='auditlog_retencion_dias',
            field=models.PositiveIntegerField(
                default=180,
                help_text=(
                    'Cantidad de días que se mantienen los registros de '
                    'auditoría (historial de cambios) antes de borrarlos. '
                    'Default: 180 (6 meses). Subilo si necesitás auditar '
                    'más para atrás; bajalo si la tabla crece muy rápido '
                    'y querés ahorrar espacio.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='configuraciongeneral',
            name='auditlog_purge_habilitado',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Si está prendido, la task de purga corre periódicamente '
                    'y borra registros viejos de auditoría. Apagalo solo si '
                    'querés guardar el historial completo (consume espacio '
                    'en disco).'
                ),
            ),
        ),
        migrations.AddField(
            model_name='configuraciongeneral',
            name='auditlog_ultima_purga_at',
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                help_text='Cuándo corrió por última vez la task de purga (read-only).',
            ),
        ),
        migrations.AddField(
            model_name='configuraciongeneral',
            name='auditlog_ultima_purga_borrados',
            field=models.PositiveIntegerField(
                default=0,
                editable=False,
                help_text='Cuántos registros borró la última corrida (read-only).',
            ),
        ),
    ]
