"""
Backfill: crear una `CuentaCliente` por cada `Cliente` existente.

Esto garantiza la invariante de que TODO cliente tiene cuenta corriente
desde el día 0. La pantalla nueva asume que `cliente.cuenta` siempre
existe; si faltara, habría que andar haciendo `get_or_create` en todas
partes y se ensucia el código.

Migración data-only, idempotente (no rompe si se corre dos veces).
Reversible: si se hace rollback, se borran las CuentaCliente vacías
(las que tienen movimientos quedan — Django se va a quejar y hay que
arreglarlo a mano, pero eso es lo correcto).
"""

from django.db import migrations


def crear_cuentas(apps, schema_editor):
    Cliente = apps.get_model('cliente', 'Cliente')
    CuentaCliente = apps.get_model('cliente', 'CuentaCliente')
    # Solo creamos cuentas para clientes que no tienen — la unicidad
    # del OneToOne nos protege igual, pero ahorramos queries.
    ids_con_cuenta = set(
        CuentaCliente.objects.values_list('cliente_id', flat=True)
    )
    nuevos = [
        CuentaCliente(cliente=c)
        for c in Cliente.objects.exclude(id__in=ids_con_cuenta).only('id')
    ]
    if nuevos:
        CuentaCliente.objects.bulk_create(nuevos, batch_size=500)
    print(f'  Cuentas creadas: {len(nuevos)}')


def borrar_cuentas_vacias(apps, schema_editor):
    CuentaCliente = apps.get_model('cliente', 'CuentaCliente')
    # Solo borramos las que no tienen movimientos para no perder data.
    vacias = CuentaCliente.objects.filter(movimientos__isnull=True)
    count = vacias.count()
    vacias.delete()
    print(f'  Cuentas vacías borradas: {count}')


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0002_cuentacliente_movimientocuenta'),
    ]

    operations = [
        migrations.RunPython(crear_cuentas, borrar_cuentas_vacias),
    ]
