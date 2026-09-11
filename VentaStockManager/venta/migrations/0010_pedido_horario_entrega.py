from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0019_horarioatencioncliente'),
        ('venta', '0009_pedido_actualizado_por_pedido_asignado_en_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='horario_entrega_desde',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pedido',
            name='horario_entrega_hasta',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pedido',
            name='horario_entrega_2_desde',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pedido',
            name='horario_entrega_2_hasta',
            field=models.TimeField(blank=True, null=True),
        ),
    ]
