from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('configuracion', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuraciongeneral',
            name='lista_precios_link_dias',
            field=models.PositiveIntegerField(
                default=7,
                help_text=(
                    'Cantidad de días que dura un link público de lista de '
                    'precios desde que se comparte. Se aplica al momento de '
                    'apretar "Compartir link público"; cambiar este valor NO '
                    'modifica retroactivamente los links ya emitidos.'
                ),
            ),
        ),
    ]
