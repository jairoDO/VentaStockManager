"""
Backfill: derivar `whatsapp_number` desde el `telefono` legacy.

El campo `telefono` del dump heredado tiene formatos muy variados:
'1155551234', '11 5555 1234', '(011) 5555-1234', '+54 9 11 5555 1234',
'1554-5555', '00000000' (default falso), '' (vacío), 'no tiene', etc.

Best-effort: extraemos solo dígitos, asumimos prefijo Argentina si no
tiene país, y guardamos solo si el resultado tiene 11-13 dígitos
(longitud razonable de un número AR completo). Los que no pasan
quedan vacíos y el operador los completa a mano desde el admin del
cliente — preferible que falle silencioso a mandar mensajes a
números mal formateados.

Reglas AR aplicadas:
  - Si arranca con '54', asumimos que ya está internacional.
  - Si arranca con '0' (prefijo nacional), lo sacamos.
  - Si arranca con '15' (celular AR sin código de área), lo sacamos.
    Esto es ambiguo: si el número es '155551234' no sabemos qué
    código de área es. Lo descartamos para no inventar.
  - Si tiene 10 dígitos (área + número), le anteponemos '549' (AR
    móvil internacional) — la mayoría de los clientes de un kiosco
    son celulares. Si es fijo va a fallar y el operador lo corrige.
  - Si tiene 11+ dígitos sin el 54, le anteponemos '54' nomás.

Idempotente: solo procesa los que tienen whatsapp_number vacío.
"""

from django.db import migrations


def _normalizar_telefono_ar(telefono_raw: str) -> str:
    """
    Devuelve un número AR normalizado para WhatsApp, o '' si no se
    puede inferir con confianza.
    """
    if not telefono_raw:
        return ''
    # Quedarse solo con dígitos.
    digitos = ''.join(c for c in telefono_raw if c.isdigit())
    if not digitos:
        return ''
    # Placeholder del modelo legacy.
    if digitos == '00000000' or set(digitos) == {'0'}:
        return ''
    # Demasiado corto: probablemente un número local sin código de
    # área. No podemos inventar de dónde es.
    if len(digitos) < 8:
        return ''
    # Ya viene con 54: confiamos.
    if digitos.startswith('54'):
        if len(digitos) >= 11:
            return digitos
        return ''
    # Empieza con 0: sacarlo (prefijo nacional argentino).
    if digitos.startswith('0'):
        digitos = digitos.lstrip('0')
    # Empieza con 15 sin área: ambiguo, descartamos.
    if digitos.startswith('15') and len(digitos) <= 10:
        return ''
    # 10 dígitos → móvil AR sin internacional. Anteponemos 549.
    if len(digitos) == 10:
        return '549' + digitos
    # 11+ dígitos sin 54 → anteponemos 54 y rezamos.
    if len(digitos) >= 11:
        return '54' + digitos
    return ''


def backfill(apps, schema_editor):
    Cliente = apps.get_model('cliente', 'Cliente')
    procesados = 0
    completados = 0
    qs = Cliente.objects.filter(whatsapp_number='').only('id', 'telefono')
    for c in qs.iterator(chunk_size=500):
        normalizado = _normalizar_telefono_ar(c.telefono or '')
        if normalizado:
            c.whatsapp_number = normalizado
            c.save(update_fields=['whatsapp_number'])
            completados += 1
        procesados += 1
    print(f'  Backfill teléfono → WhatsApp: {completados}/{procesados} clientes normalizados.')


def vaciar(apps, schema_editor):
    Cliente = apps.get_model('cliente', 'Cliente')
    Cliente.objects.update(whatsapp_number='')


class Migration(migrations.Migration):

    dependencies = [
        ('cliente', '0005_cliente_whatsapp_number'),
    ]

    operations = [
        migrations.RunPython(backfill, vaciar),
    ]
