"""
Agregar toggles operativos de Google Sheets a ConfiguracionGeneral.

Mueve la decisión "sincronizamos o no con Sheets" desde env vars
(SHEETS_SYNC_ENABLED, SHEETS_DELETE_SYNC_ENABLED) al singleton, así
Osvaldo lo puede prender/apagar desde /admin/configuracion/ sin
pedir redeploy.

Defaults False — matchea el estado deseado durante la migración a
Render (sync OFF hasta que decidamos si Sheets sigue siendo
fuente de verdad).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('configuracion', '0002_configuraciongeneral_lista_precios_link_dias'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuraciongeneral',
            name='sheets_sync_habilitado',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Master switch de la integración con Google Sheets. '
                    'Si está desactivado, ni el sync de pull (Sheets → DB) '
                    'ni el de delete bidireccional (DB → Sheets) funcionan. '
                    'Útil para apagar TODO durante una migración o cuando '
                    'Sheets deja de ser fuente de verdad.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='configuraciongeneral',
            name='sheets_delete_sync_habilitado',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Sincroniza el BORRADO de un artículo desde la DB '
                    'hacia el Sheet (vacía la fila en la planilla). '
                    'Requiere que el "master switch" de arriba también '
                    'esté en True. Necesita que el service-account sea '
                    'Editor del Sheet (no Viewer).'
                ),
            ),
        ),
    ]
