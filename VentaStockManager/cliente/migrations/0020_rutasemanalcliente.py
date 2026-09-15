from django.db import migrations, models
import django.db.models.deletion
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal


def poblar_rutas_iniciales(apps, schema_editor):
    Venta = apps.get_model('venta', 'Venta')
    Cliente = apps.get_model('cliente', 'Cliente')
    Ruta = apps.get_model('cliente', 'RutaSemanalCliente')
    desde = date.today() - timedelta(days=90)
    vendedores_asignados = dict(
        Cliente.objects.values_list('id', 'vendedor_asignado_id'),
    )
    evidencia = defaultdict(list)
    for cliente_id, vendedor_id, fecha in Venta.objects.filter(
        fecha_compra__gte=desde,
    ).values_list('cliente_id', 'vendedor_id', 'fecha_compra').iterator():
        evidencia[cliente_id].append((vendedor_id, fecha.weekday(), fecha))

    rutas = []
    for cliente_id, registros in evidencia.items():
        vendedor_asignado_id = vendedores_asignados.get(cliente_id)
        propios = [registro for registro in registros if registro[0] == vendedor_asignado_id]
        considerados = propios or registros
        frecuencias = Counter((registro[0], registro[1]) for registro in considerados)
        ultima_fecha = {
            clave: max(r[2] for r in considerados if (r[0], r[1]) == clave)
            for clave in frecuencias
        }
        (vendedor_id, dia), cantidad = max(
            frecuencias.items(), key=lambda item: (item[1], ultima_fecha[item[0]]),
        )
        rutas.append(Ruta(
            cliente_id=cliente_id,
            vendedor_id=vendedor_id,
            dia_semana=dia,
            sugerida=True,
            confianza=Decimal(cantidad * 100 / len(considerados)).quantize(Decimal('0.01')),
            ventas_analizadas=len(considerados),
        ))
    Ruta.objects.bulk_create(rutas, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0019_horarioatencioncliente'),
        ('vendedor', '0003_repartidor'),
    ]

    operations = [
        migrations.CreateModel(
            name='RutaSemanalCliente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('dia_semana', models.PositiveSmallIntegerField(choices=[(0, 'Lunes'), (1, 'Martes'), (2, 'Miércoles'), (3, 'Jueves'), (4, 'Viernes'), (5, 'Sábado'), (6, 'Domingo')])),
                ('sugerida', models.BooleanField(default=True, help_text='Fue estimada desde las ventas y todavía puede revisarse.')),
                ('confianza', models.DecimalField(decimal_places=2, default=0, help_text='Porcentaje de las compras recientes que coincidieron con este día.', max_digits=5)),
                ('ventas_analizadas', models.PositiveIntegerField(default=0)),
                ('actualizada_en', models.DateTimeField(auto_now=True)),
                ('cliente', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='ruta_semanal', to='cliente.cliente')),
                ('vendedor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rutas_semanales', to='vendedor.vendedor')),
            ],
            options={
                'verbose_name': 'Ruta semanal de clientes',
                'verbose_name_plural': 'Rutas semanales de clientes',
                'ordering': ('vendedor_id', 'dia_semana', 'cliente__nombre'),
            },
        ),
        migrations.AddIndex(
            model_name='rutasemanalcliente',
            index=models.Index(fields=['vendedor', 'dia_semana'], name='cliente_rut_vendedo_c713ac_idx'),
        ),
        migrations.RunPython(poblar_rutas_iniciales, migrations.RunPython.noop),
    ]
