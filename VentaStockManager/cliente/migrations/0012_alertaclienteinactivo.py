# Generated manually 2026-05-27

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0011_alter_cliente_formato_preferido_lista_precios'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='AlertaClienteInactivo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ultima_compra', models.DateField(blank=True, null=True)),
                ('dias_inactivo', models.PositiveIntegerField(default=0)),
                ('revisada', models.BooleanField(default=False)),
                ('revisada_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('cliente', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alertas_inactividad', to='cliente.cliente')),
                ('revisada_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='auth.user')),
            ],
            options={
                'verbose_name': 'alerta de cliente inactivo',
                'verbose_name_plural': 'alertas de clientes inactivos',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['cliente', 'revisada'], name='cliente_ale_cliente_a1b2c3_idx'),
                    models.Index(fields=['revisada'], name='cliente_ale_revisad_d4e5f6_idx'),
                ],
            },
        ),
    ]
