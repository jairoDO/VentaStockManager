from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0018_cliente_creado_por'),
    ]

    operations = [
        migrations.CreateModel(
            name='HorarioAtencionCliente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('dia_semana', models.PositiveSmallIntegerField(choices=[(0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')])),
                ('desde_1', models.TimeField(verbose_name='Desde')),
                ('hasta_1', models.TimeField(verbose_name='Hasta')),
                ('desde_2', models.TimeField(blank=True, null=True, verbose_name='Segundo horario desde')),
                ('hasta_2', models.TimeField(blank=True, null=True, verbose_name='Segundo horario hasta')),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('cliente', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='horarios_atencion', to='cliente.cliente')),
            ],
            options={
                'verbose_name': 'horario de atención',
                'verbose_name_plural': 'horarios de atención',
                'ordering': ('dia_semana',),
            },
        ),
        migrations.AddConstraint(
            model_name='horarioatencioncliente',
            constraint=models.UniqueConstraint(fields=('cliente', 'dia_semana'), name='un_horario_por_cliente_y_dia'),
        ),
    ]
