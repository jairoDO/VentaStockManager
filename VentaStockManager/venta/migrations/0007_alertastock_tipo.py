"""
Agrega AlertaStock.tipo (insuficiente | reponer) y relaja
cantidad_pedida / cantidad_faltante a default=0 para que las
alertas tipo 'reponer' (que no tienen faltante) sean válidas.

`tipo` con default='insuficiente' preserva semántica de filas viejas.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('venta', '0006_alertastock'),
    ]

    operations = [
        migrations.AddField(
            model_name='alertastock',
            name='tipo',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('insuficiente', 'Stock insuficiente al vender'),
                    ('reponer', 'Stock bajo umbral (reponer)'),
                ],
                default='insuficiente',
                help_text=(
                    '"insuficiente": se vendió más de lo que había. '
                    '"reponer": el stock cayó al/debajo del stock_minimo del articulo.'
                ),
            ),
        ),
        migrations.AlterField(
            model_name='alertastock',
            name='cantidad_pedida',
            field=models.PositiveIntegerField(
                default=0,
                help_text=(
                    'Cuántas unidades pidió el operador en la venta. '
                    '0 para alertas tipo "reponer".'
                ),
            ),
        ),
        migrations.AlterField(
            model_name='alertastock',
            name='cantidad_faltante',
            field=models.PositiveIntegerField(
                default=0,
                help_text=(
                    'cantidad_pedida − stock_disponible_al_momento para "insuficiente". '
                    '0 para "reponer" (no hubo faltante, solo se cruzó el umbral).'
                ),
            ),
        ),
    ]
