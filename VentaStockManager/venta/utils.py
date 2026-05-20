"""
Utilidades de la app venta.

Concentramos acá lógica reutilizable que antes estaba duplicada
en `admin.py`, `models.py`, etc.

Lo importante de este módulo: el cálculo de totales **respeta los
descuentos** (por línea y global). El `ArticuloVenta.total` del
modelo NO los considera (es legacy: solo `cantidad * precio`), así
que cualquier reporte/PDF/UI nueva debe usar las funciones de acá.
"""

from decimal import Decimal, InvalidOperation


CIEN = Decimal('100')


def parse_precio(precio_raw):
    """
    Parsea el campo `ArticuloVenta.precio` que es CharField sucio.

    El dump heredado de PythonAnywhere tiene formatos mixtos:
    "1880", "1,880.00", "1 880.00" (espacio como separador de miles),
    "$1.880,00", "1.880", etc. El plan a futuro es migrar a Decimal
    pero mientras tanto necesitamos algo que NUNCA rompa, porque
    los métodos del list_display se ejecutan para CADA fila y una
    sola línea con dato corrupto rompe toda la página (500).

    Estrategia:
      1. Dejamos solo dígitos y puntos.
      2. Si quedaron múltiples puntos, asumimos el último es el
         decimal y los anteriores eran separadores de miles.
      3. Si no se puede convertir, devolvemos Decimal('0').

    Devuelve siempre un `Decimal`. Si querés float, casteá afuera.
    """
    if precio_raw is None:
        return Decimal('0')
    limpio = ''.join(c for c in str(precio_raw) if c.isdigit() or c == '.')
    if limpio.count('.') > 1:
        partes = limpio.split('.')
        limpio = ''.join(partes[:-1]) + '.' + partes[-1]
    if not limpio:
        return Decimal('0')
    try:
        return Decimal(limpio)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


# ---------------------------------------------------------------------------
# Cálculo de totales (respetando descuentos)
# ---------------------------------------------------------------------------

def precio_decimal_de(articulo_venta) -> Decimal:
    """Devuelve el precio unitario en Decimal, parseando legacy si hace falta."""
    pd = getattr(articulo_venta, 'precio_decimal', None)
    if pd is not None:
        return pd
    return parse_precio(getattr(articulo_venta, 'precio', None))


def subtotal_linea(articulo_venta) -> Decimal:
    """
    Subtotal de una línea de venta APLICANDO el descuento por línea.

    Fórmula: cantidad × precio × (1 − descuento_linea/100)
    """
    cantidad = Decimal(articulo_venta.cantidad or 0)
    precio = precio_decimal_de(articulo_venta)
    desc = articulo_venta.descuento_porcentaje or Decimal('0')
    return (cantidad * precio * (CIEN - desc) / CIEN).quantize(Decimal('0.01'))


def subtotal_venta_sin_desc_global(venta) -> Decimal:
    """
    Suma de los subtotales por línea (cada uno ya con su descuento de
    línea aplicado), ANTES de restar el descuento global.

    Esto es lo que se muestra como "Subtotal" en el PDF cuando hay
    descuento global, para que se vea claro el efecto del global.
    """
    total = Decimal('0')
    for av in venta.ventas.all():
        total += subtotal_linea(av)
    return total.quantize(Decimal('0.01'))


def total_venta(venta) -> Decimal:
    """
    Total final de la venta = subtotal × (1 − descuento_global/100).

    Es lo que se cobra. Función canónica — todo reporte / PDF / UI
    debe usar ESTA y no el `Venta.precio_total` legacy (que no aplica
    descuentos).
    """
    subtotal = subtotal_venta_sin_desc_global(venta)
    desc_global = venta.descuento_porcentaje or Decimal('0')
    return (subtotal * (CIEN - desc_global) / CIEN).quantize(Decimal('0.01'))
