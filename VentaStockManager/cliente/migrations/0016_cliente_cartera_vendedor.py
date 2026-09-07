from django.db import migrations, models
import django.db.models.deletion


def sugerir_vendedor_segun_ultima_venta(apps, schema_editor):
    Cliente = apps.get_model('cliente', 'Cliente')
    Venta = apps.get_model('venta', 'Venta')

    # La base actual ronda cientos de clientes. La consulta por cliente es
    # deliberadamente simple y compatible tanto con PostgreSQL como SQLite.
    for cliente in Cliente.objects.iterator(chunk_size=200):
        ultima = (
            Venta.objects
            .filter(cliente_id=cliente.pk, vendedor_id__isnull=False)
            .order_by('-fecha_compra', '-pk')
            .values('vendedor_id')
            .first()
        )
        if ultima:
            Cliente.objects.filter(pk=cliente.pk).update(
                vendedor_sugerido_id=ultima['vendedor_id'],
            )


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0015_direccioncliente_and_more'),
        ('venta', '0009_pedido_actualizado_por_pedido_asignado_en_and_more'),
        ('vendedor', '0003_repartidor'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='asignacion_vendedor_confirmada',
            field=models.BooleanField(
                default=False,
                help_text='Indica que el administrador revisó la asignación.',
            ),
        ),
        migrations.AddField(
            model_name='cliente',
            name='vendedor_asignado',
            field=models.ForeignKey(
                blank=True,
                help_text='Vendedor responsable de atender a este cliente.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='clientes_asignados',
                to='vendedor.vendedor',
            ),
        ),
        migrations.AddField(
            model_name='cliente',
            name='vendedor_sugerido',
            field=models.ForeignKey(
                blank=True,
                help_text='Sugerencia calculada desde la venta más reciente.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='clientes_sugeridos',
                to='vendedor.vendedor',
            ),
        ),
        migrations.RunPython(
            sugerir_vendedor_segun_ultima_venta,
            migrations.RunPython.noop,
        ),
    ]
