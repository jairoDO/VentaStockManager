"""
Agrega Articulo.stock_minimo (default 5) — umbral usado por
ArticuloVenta.save() para disparar AlertaStock(tipo='reponer')
cuando el stock cae al/debajo de ese número después de una venta.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('articulo', '0015_seed_palabras_y_colores'),
    ]

    operations = [
        migrations.AddField(
            model_name='articulo',
            name='stock_minimo',
            field=models.PositiveIntegerField(
                default=5,
                help_text=(
                    'Cuando el stock cae a este número o menos, se genera '
                    'una alerta "Reponer" para la administración.'
                ),
            ),
        ),
    ]
