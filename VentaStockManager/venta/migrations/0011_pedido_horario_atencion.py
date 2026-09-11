from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0019_horarioatencioncliente'),
        ('venta', '0010_pedido_horario_entrega'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedido',
            name='horario_atencion',
            field=models.ForeignKey(
                blank=True,
                help_text='Horario del cliente utilizado al crear este pedido.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pedidos',
                to='cliente.horarioatencioncliente',
            ),
        ),
    ]
