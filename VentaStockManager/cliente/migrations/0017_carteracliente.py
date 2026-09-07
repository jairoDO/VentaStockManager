from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0016_cliente_cartera_vendedor'),
    ]

    operations = [
        migrations.CreateModel(
            name='CarteraCliente',
            fields=[],
            options={
                'verbose_name': 'Asignar clientes a vendedores',
                'verbose_name_plural': 'Asignar clientes a vendedores',
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('cliente.cliente',),
        ),
    ]
