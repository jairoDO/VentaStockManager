from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('venta', '0011_pedido_horario_atencion'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='monto_efectivo_entrega',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=12),
        ),
        migrations.AddField(
            model_name='pedido',
            name='monto_transferencia_entrega',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=12),
        ),
        migrations.AddField(
            model_name='pedido',
            name='monto_cuenta_corriente_entrega',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=12),
        ),
        migrations.AddField(
            model_name='pedido',
            name='cobro_entrega_registrado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
