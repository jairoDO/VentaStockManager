"""
Script de seed para testear el docker local.

Crea:
  - 3 Vendedores (con auth.User asociado).
  - 5 Articulos básicos.
  - 4 Clientes con saldos distintos (deuda grande, deuda chica, en 0,
    a favor).
  - 6 Ventas + Pedidos cubriendo: pendientes, pagados, parciales.
  - MovimientoCuenta para dejar los saldos en los valores objetivo.

Idempotente: usa get_or_create por username/codigo/etc, así que se
puede correr múltiples veces. Para resetear, hacer `docker compose
down -v` y volver a `up`.

Uso:
    docker compose exec web python /app/../seed_local.py
    # o desde el shell del container:
    python manage.py shell -c "exec(open('/app/../seed_local.py').read())"
"""
from decimal import Decimal
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db import transaction

from vendedor.models import Vendedor
from articulo.models import Articulo
from cliente.models import Cliente, CuentaCliente, MovimientoCuenta
from venta.models import Venta, ArticuloVenta, Pedido


HOY = date.today()


@transaction.atomic
def seed():
    print('=== Vendedores ===')
    vendedores_data = [
        # (username, password, nombre, apellido) — para testear el fix
        # del PDF: LUCAS2 con nombre/apellido distintos al username.
        ('LUCAS2', 'lucas123', 'Nahuel', 'Baes'),
        ('maria', 'maria123', 'María', 'López'),
        ('pedro', 'pedro123', '', 'Sin apellido'),  # vendedor "feo": sin nombre real
    ]
    vendedores = {}
    for username, pwd, nombre, apellido in vendedores_data:
        user, created = User.objects.get_or_create(
            username=username,
            defaults={'is_staff': True, 'email': f'{username}@local'},
        )
        if created:
            user.set_password(pwd)
            user.is_staff = True
            user.save()
        v, _ = Vendedor.objects.get_or_create(
            usuario=user,
            defaults={'nombre': nombre, 'apellido': apellido or 'Sin apellido'},
        )
        # actualizar nombre/apellido por si el get_or_create ya existía con otros.
        v.nombre = nombre
        v.apellido = apellido or 'Sin apellido'
        v.save()
        vendedores[username] = v
        print(f'  • {username} ({nombre} {apellido})')

    print('=== Articulos ===')
    articulos_data = [
        # (codigo, nombre, precio_minorista, precio_mayorista, stock, marca)
        ('A001', 'Agua bidón 6L sierra del norte', 1300, 1100, 50, 'Sierra del Norte'),
        ('A002', 'Pan lactal 500g', 800, 700, 30, 'Bimbo'),
        ('A003', 'Leche entera sachet 1L', 950, 850, 40, 'La Serenísima'),
        ('A004', 'Aceite girasol 1.5L', 2400, 2100, 25, 'Cocinero'),
        ('A005', 'Galletas dulces 200g', 600, 500, 60, 'Generico'),
    ]
    articulos = {}
    for codigo, nombre, pmin, pmay, stock, marca in articulos_data:
        a, _ = Articulo.objects.get_or_create(
            codigo=codigo,
            defaults={
                'nombre': nombre,
                'precio_minorista': Decimal(pmin),
                'precio_mayorista': Decimal(pmay),
                'stock': stock,
                'marca': marca,
                'stock_minimo': 5,
                'vencimiento': HOY + timedelta(days=365),
            },
        )
        articulos[codigo] = a
        print(f'  • {codigo} {nombre} (${pmin})')

    print('=== Clientes ===')
    clientes_data = [
        # (nombre, apellido, tel, direccion, sin_cuenta, saldo_inicial)
        # sin_cuenta=True → NO se crea CuentaCliente (testea el botón
        #                   "Crear cuenta corriente" del form de cobro).
        # saldo_inicial → solo aplica si sin_cuenta=False. Es el saldo
        #                  ANTES de las ventas del seed (las pendientes
        #                  suman deuda encima).
        ('Brisa',   'Luba',     '1144441111', 'Av. San Martín 123', False, Decimal('0')),
        ('Carlos',  'Pérez',    '1144442222', 'Belgrano 456',        False, Decimal('0')),
        ('Lucía',   'Gómez',    '1144443333', 'Mitre 789',           False, Decimal('0')),
        # Daniel: $2500 a favor (caso edge "queda a favor del cliente").
        ('Daniel',  'Ruiz',     '1144444444', 'Rivadavia 1010',      False, Decimal('2500')),
        # Sofía y Tomás: SIN cuenta corriente. La pantalla "Cobrar y
        # generar PDF" tiene que mostrar el botón "Crear cuenta".
        ('Sofía',   'Aguirre',  '1144445555', 'Sarmiento 222',       True,  Decimal('0')),
        ('Tomás',   'Méndez',   '1144446666', 'Alvear 33',           True,  Decimal('0')),
        # Valeria: con cuenta pero saldo 0 (cliente "al día" antes de
        # las ventas del seed).
        ('Valeria', 'Castro',   '1144447777', 'Belgrano 901',        False, Decimal('0')),
    ]
    clientes = {}
    for nombre, apellido, tel, direccion, sin_cuenta, saldo_inicial in clientes_data:
        c, created = Cliente.objects.get_or_create(
            nombre=nombre, apellido=apellido,
            defaults={'telefono': tel, 'direccion': direccion},
        )
        if created:
            c.direccion = direccion
            c.telefono = tel
            c.save()
        clientes[nombre] = c
        if sin_cuenta:
            # Garantizar que NO tenga cuenta — re-corridas del seed deben
            # mantener este cliente sin cuenta.
            CuentaCliente.objects.filter(cliente=c).delete()
            print(f'  • {nombre} {apellido} — SIN cuenta corriente')
        else:
            cuenta, _ = CuentaCliente.objects.get_or_create(cliente=c)
            saldo_actual = cuenta.saldo
            delta = saldo_inicial - saldo_actual
            if delta != 0:
                MovimientoCuenta.objects.create(
                    cuenta=cuenta,
                    tipo=MovimientoCuenta.TIPO_AJUSTE,
                    monto=delta,
                    descripcion=f'[seed_local] Saldo inicial ${saldo_inicial}',
                )
            print(f'  • {nombre} {apellido} — con cuenta, saldo inicial ${saldo_inicial}')

    print('=== Ventas + Pedidos ===')
    # (cliente_nombre, vendedor_username, dias_atras, items, marcar_pagado)
    # items = lista de (codigo_articulo, cantidad, precio_unitario)
    #
    # Convención del seed (= flujo real api_venta_guardar):
    #   - Venta PAGADA: crea PAGO de monto=total (no genera deuda).
    #   - Venta PENDIENTE: crea VENTA_A_CUENTA de monto=-total (entra
    #     como deuda en el saldo del cliente). Así cuando la
    #     administradora cobre desde la nueva pantalla, el preview de
    #     "saldo nuevo" tiene sentido.
    ventas_data = [
        # Brisa (con cuenta, debe): 2 pendientes + 1 pagada
        ('Brisa',   'LUCAS2', 2, [('A001', 2, 1300), ('A002', 1, 800)], False),
        ('Brisa',   'LUCAS2', 5, [('A003', 3, 950)], True),
        ('Brisa',   'LUCAS2', 0, [('A004', 1, 2400)], False),
        # Carlos (con cuenta): 1 pendiente
        ('Carlos',  'maria',  1, [('A004', 1, 2400), ('A005', 2, 600)], False),
        # Lucía (con cuenta, $0): 1 pagada
        ('Lucía',   'maria',  3, [('A001', 1, 1300), ('A005', 1, 600)], True),
        # Daniel (con cuenta, +$2500 a favor): 1 pendiente
        ('Daniel',  'pedro',  0, [('A002', 2, 800)], False),
        # Sofía (SIN cuenta): 1 pendiente — no se crea movimiento porque
        # no hay cuenta. El form va a mostrar el botón "Crear cuenta".
        ('Sofía',   'maria',  0, [('A003', 2, 950)], False),
        # Tomás (SIN cuenta): 1 pagada y 1 pendiente
        ('Tomás',   'LUCAS2', 1, [('A005', 5, 600)], True),
        ('Tomás',   'LUCAS2', 0, [('A001', 1, 1300)], False),
        # Valeria (con cuenta, $0): 1 pendiente con varios items
        ('Valeria', 'pedro',  0, [('A002', 3, 800), ('A003', 1, 950), ('A005', 4, 600)], False),
    ]
    ventas_creadas = 0
    for cli_nombre, vend_username, dias_atras, items, pagado in ventas_data:
        cliente = clientes[cli_nombre]
        vendedor = vendedores[vend_username]
        fecha = HOY - timedelta(days=dias_atras)
        venta = Venta.objects.create(
            cliente=cliente,
            vendedor=vendedor,
            fecha_compra=fecha,
            fecha_entrega=fecha,
        )
        for codigo, cant, precio in items:
            art = articulos[codigo]
            ArticuloVenta.objects.create(
                venta=venta,
                articulo=art,
                cantidad=cant,
                precio=str(precio),
                precio_decimal=Decimal(precio),
            )
        total = Decimal(sum(c * p for _, c, p in items))
        cuenta = CuentaCliente.objects.filter(cliente=cliente).first()
        if pagado:
            # Pagada al contado: NO se generan movimientos. La plata entra
            # y la mercadería sale; impacto neto en saldo = 0 (igual que
            # hace api_venta_guardar cuando monto_pagado == total).
            venta.pedido.pagado = True
            venta.pedido.save(update_fields=['pagado'])
            estado_str = '[PAGADO]'
        elif cuenta:
            # Pendiente, cliente tiene cuenta: la venta entra como deuda
            # en el saldo del cliente.
            MovimientoCuenta.objects.create(
                cuenta=cuenta,
                tipo=MovimientoCuenta.TIPO_VENTA_A_CUENTA,
                monto=-total,
                venta=venta,
                descripcion=f'[seed_local] Venta #{venta.id} a cuenta',
            )
            estado_str = '[pendiente, va a saldo]'
        else:
            # Pendiente, cliente SIN cuenta: no se crea movimiento. La
            # venta existe pero el cliente no tiene cuenta corriente
            # todavía — el operador la creará desde el form de cobro.
            estado_str = '[pendiente, cliente sin cuenta]'
        ventas_creadas += 1
        print(f'  • Venta #{venta.id} {cli_nombre} ({vend_username}) — ${total} '
              f'{estado_str} · Pedido #{venta.pedido.id}')

    print(f'\n✓ Seed completo: {ventas_creadas} ventas, {len(clientes)} clientes, '
          f'{len(vendedores)} vendedores, {len(articulos)} artículos.')
    print('\nSaldos finales:')
    for c in Cliente.objects.all().order_by('nombre'):
        cuenta = CuentaCliente.objects.filter(cliente=c).first()
        if cuenta is None:
            print(f'  • {c.nombre} {c.apellido}: SIN cuenta corriente')
        else:
            print(f'  • {c.nombre} {c.apellido}: ${cuenta.saldo}')


if __name__ == '__main__' or True:
    seed()
