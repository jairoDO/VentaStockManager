"""
Agrega los campos de descuento persistido a `Venta` y `ArticuloVenta`.

Por qué persistimos los descuentos en vez de calcularlos del precio:
  - El precio del artículo (precio_minorista, precio_mayorista) puede
    cambiar mañana, pero la rebaja que el vendedor cerró HOY tiene que
    quedar congelada en la fila para que el comprobante histórico
    siga coincidiendo.
  - django-auditlog ya está registrado para Venta y ArticuloVenta, así
    que con tener el campo persistido el historial de quién/cuándo
    aplicó el descuento queda capturado automáticamente.

La dependencia apunta a `0002_articuloventa_precio_decimal_and_more`
porque la nueva pantalla de venta escribe `precio_decimal` directo
(source of truth) y necesita que ese campo exista. Si lo aplicás en
una base que no tiene la 0002 todavía, Django va a fallar al resolver
la dependencia — ese es el comportamiento que queremos.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('venta', '0002_articuloventa_precio_decimal_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='venta',
            name='descuento_porcentaje',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Descuento porcentual aplicado al total general de la venta (0-100)',
                max_digits=5,
            ),
        ),
        migrations.AddField(
            model_name='venta',
            name='descuento_motivo',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='articuloventa',
            name='descuento_porcentaje',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Descuento porcentual aplicado a esta línea (0-100)',
                max_digits=5,
            ),
        ),
    ]
