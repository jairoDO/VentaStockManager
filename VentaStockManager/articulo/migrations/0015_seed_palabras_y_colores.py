"""
Data migration que completa lo que faltó en 0014:
  1) Asignar colores distintos a cada Categoría (no todas el default '#607d8b').
  2) Crear ReglaCategoria con `palabras_clave` para que cada categoría
     auto-asigne sus artículos cuando se corre el comando
     `aplicar_reglas_categoria`.

Idempotente:
  - Solo update el color si la Categoría tiene el default '#607d8b'
    (respeta lo que el operador haya editado a mano).
  - Solo crea ReglaCategoria si NO existe ya una con las MISMAS
    palabras_clave para esa categoría (anti-duplicado).

Palabras filtradas anti-ambigüedad:
  - Se descartaron palabras que aparecen en >=2 categorías del Excel
    (ej. "chocolate" estaba en CHOCOLATES Y GALLETAS — saca matches
    incorrectos). El comando aplicar_reglas_categoria igual maneja
    ambigüedad por prioridad, pero mejor evitarla de raíz.

Prioridades:
  - Default 100 para todas. Si querés que una categoría matchee
    primero (ej. CHUPETINES antes que CHICLES porque "Chupetin con
    chicle" debería caer en chupetines), bajá la prioridad de esa
    regla a 50 desde el admin.
"""
from django.db import migrations


# Palabras clave por categoría — extraídas del Excel real del operador
# filtrando palabras ambiguas (que aparecían en >1 categoría).
PALABRAS_POR_CATEGORIA: dict[str, list[str]] = {
    'MASTICABLE(caramelo)': ['masticable', 'caramelo', 'pico', 'métrico', 'billiken', 'yogur', 'miel', 'cristal', 'bocadito'],
    'GOMITASS': ['goma', 'piñatas', 'dóciles', 'regaliz', 'regalis', 'gragea', 'fantasía', 'gomet', 'regali'],
    'PASTILLAS': ['bull', 'yummy', 'pastilla', 'mentitas', 'freegells', 'mandarina', 'tutti', 'acido', 'osito'],
    'GALLETAS': ['galleta', 'obleas', 'saladix', 'vainilla', 'pamela', 'galletas', 'cubanito', 'formis', 'rellenas', 'jorgitos'],
    'ALFAJORES': ['triple', 'tatin', 'cordobés', 'nevares', 'fulbito', 'milka', 'fantoche', 'tucabon', 'muss', 'águila'],
    'SNACK': ['quento', 'papa', 'danal', 'mani', 'saborizado', 'cascarón', 'conitos', 'chedar', 'cebolla', 'kechun'],
    'Bebidas': ['placer', 'cola', 'baggio', 'manaos', 'pomelo', 'rumipal', 'petacon', 'multifruta', 'vida', 'pepsi'],
    'CHOCOLATES': ['hamlet', 'bonobon', 'bariloche', 'celofán', 'sapito', 'ducren', 'aireado', 'oblea', 'ducrem'],
    'VARIOS': ['encendedores', 'boli', 'bolitas', 'pilas', 'parches', 'ceda', 'gotita', 'ecole', 'velas'],
    'LIMPIEZA': ['duft', 'perfumina', 'papel', 'lavandina', 'aerosol', 'esponja', 'doncellas', 'detergente'],
    'HIGIENE PERSONAL': ['rexona', 'presto', 'dental', 'sedal'],
    'CHUPETINES': ['chupetin', 'evolution', 'tatoo', 'floky', 'amor'],
    'ALMACÉN': ['celestial', 'cajita', 'yerba', 'tomate', 'natura', 'arregui', 'flan', 'puré', 'huerta', 'aceite'],
    'ANALGÉSICOS 💊': ['diclofenac', 'ibuevanol', 'tafirol', 'actron', 'oxigenada', 'volúmenes', 'buscapina', 'cafia', 'curita', 'ibuprofeno'],
    'CIGARRILLOS.': ['marlboro', 'lucky', 'chesterfield', 'philip', 'morris', 'strike', 'parliament', 'rothmans', 'camel'],
    'CHICLES': ['topline', 'recargado', 'tenis', 'gumbal', 'agrupado', 'buubaloo', 'sandia', 'open', 'fierita'],
    'CONDIMENTO Y SABORES': ['condimento', 'pimienta', 'pimentón', 'orégano', 'comino'],
    'HELADOS': ['anana'],
    'Línea tasitas': ['fragolito'],
    'Línea postres': ['helada', 'crocante', 'lingoto'],
    'Línea familiar': ['familiar', 'tricolor'],
    'Tarros de 10 litros de agua': [],   # No hay palabras distintivas únicas — vacía
    'Tarros de 10 litros sabores comunes': ['americana', 'cielo', 'maracuya', 'ceresa'],
    'Tarros de 10 litros sabores especiales': ['granizado', 'granizada'],
    'Tarros de 10 litros SÚPER sabores': ['sembrado'],
    'INSUMOS PARA HELADERIA': ['cono', 'vasos', 'salsa', 'toping', 'cucharita', 'tergopol', 'polipapel'],
    'JUGOS TANG': ['tang'],
    'JUGOS CLIGHT': ['clight'],
    'JUGOS RINDE 2': ['rinde'],
    'JUGOS NOEL': ['noel'],
    'JUGOS JA!': [],  # Solo 2 artículos, sin palabra distintiva — vacía
    'PRODUCTOS NAVIDEnOS': ['budín', 'pozo', 'maní', 'navideño', 'sidra'],
    'PIROTECNIA': ['piro', 'cañón', 'candela', 'mortero', 'flower'],
    'INPORTADOS': ['confitero'],
    'EXTRAS': ['nacho', 'mecano', 'guido', 'guisero'],
}


# Colores distintivos por categoría — palette pensada para que cada
# una se distinga visualmente en el admin/grilla. Agrupados por rubro
# (tonos parecidos para categorías del mismo rubro).
COLORES_POR_CATEGORIA: dict[str, str] = {
    # Golosinas — tonos rosa/magenta
    'MASTICABLE(caramelo)': '#EC4899',
    'GOMITASS': '#F472B6',
    'PASTILLAS': '#DB2777',
    'ALFAJORES': '#BE185D',
    'CHOCOLATES': '#9D174D',
    'CHUPETINES': '#F9A8D4',
    'CHICLES': '#FB7185',
    # Galletas y Snacks — tonos ámbar/naranja
    'GALLETAS': '#F59E0B',
    'SNACK': '#FBBF24',
    # Bebidas — azules
    'Bebidas': '#3B82F6',
    'JUGOS TANG': '#60A5FA',
    'JUGOS CLIGHT': '#93C5FD',
    'JUGOS RINDE 2': '#2563EB',
    'JUGOS NOEL': '#1D4ED8',
    'JUGOS JA!': '#1E40AF',
    'BEBIDAS ALCOHOLICAS': '#1E3A8A',
    # Almacén — verdes
    'ALMACÉN': '#10B981',
    'CONDIMENTO Y SABORES': '#34D399',
    # Limpieza e Higiene — cyans
    'LIMPIEZA': '#06B6D4',
    'HIGIENE PERSONAL': '#22D3EE',
    # Helados — violetas
    'HELADOS': '#8B5CF6',
    'Línea tasitas': '#A78BFA',
    'Línea postres': '#C4B5FD',
    'Línea familiar': '#7C3AED',
    'Tarros de 10 litros de agua': '#6D28D9',
    'Tarros de 10 litros sabores comunes': '#5B21B6',
    'Tarros de 10 litros sabores especiales': '#4C1D95',
    'Tarros de 10 litros SÚPER sabores': '#3730A3',
    'INSUMOS PARA HELADERIA': '#A855F7',
    # Salud — rojo
    'ANALGÉSICOS 💊': '#EF4444',
    # Tabaco — gris cálido
    'CIGARRILLOS.': '#78716C',
    # Estacional — naranja
    'PRODUCTOS NAVIDEnOS': '#F97316',
    'PIROTECNIA': '#EA580C',
    # Otros — neutros
    'VARIOS': '#9CA3AF',
    'INPORTADOS': '#6B7280',
    'EXTRAS': '#4B5563',
}


# Color default que pone el modelo Categoria — solo updateamos los
# que tengan exactamente este valor (no pisamos los que el operador
# haya cambiado a mano).
COLOR_DEFAULT = '#607d8b'


def sembrar_palabras_y_colores(apps, schema_editor):
    Categoria = apps.get_model('articulo', 'Categoria')
    ReglaCategoria = apps.get_model('articulo', 'ReglaCategoria')

    for nombre_cat, palabras in PALABRAS_POR_CATEGORIA.items():
        try:
            cat = Categoria.objects.get(nombre=nombre_cat)
        except Categoria.DoesNotExist:
            # Si por algún motivo la categoría no se sembró en 0014
            # (raro pero defensivo), la salteamos. El operador puede
            # crearla a mano después.
            continue

        # 1) Color: solo update si tiene el default (no pisar manual).
        color_nuevo = COLORES_POR_CATEGORIA.get(nombre_cat)
        if color_nuevo and cat.color == COLOR_DEFAULT:
            cat.color = color_nuevo
            cat.save(update_fields=['color'])

        # 2) ReglaCategoria con las palabras_clave. Idempotente:
        #    - Si ya hay una regla con EXACTAMENTE estas palabras, no
        #      hacemos nada.
        #    - Si no hay reglas para esta categoría, creamos una.
        #    - Si hay reglas pero CON OTRAS palabras (el operador las
        #      curó a mano), las respetamos y NO sobreescribimos.
        if not palabras:
            continue
        if ReglaCategoria.objects.filter(categoria=cat).exists():
            # Ya hay al menos una regla → respetar y no tocar.
            continue
        ReglaCategoria.objects.create(
            categoria=cat,
            palabras_clave=palabras,
            prioridad=100,
            activa=True,
        )


def revertir(apps, schema_editor):
    """
    Reverse: borra solo las reglas que coinciden EXACTAMENTE con las
    palabras que sembramos. Si el operador agregó/modificó palabras,
    NO tocamos su regla (defensivo).
    """
    ReglaCategoria = apps.get_model('articulo', 'ReglaCategoria')
    Categoria = apps.get_model('articulo', 'Categoria')
    for nombre_cat, palabras in PALABRAS_POR_CATEGORIA.items():
        if not palabras:
            continue
        try:
            cat = Categoria.objects.get(nombre=nombre_cat)
        except Categoria.DoesNotExist:
            continue
        for r in ReglaCategoria.objects.filter(categoria=cat):
            if list(r.palabras_clave or []) == palabras:
                r.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articulo', '0014_seed_rubros_categorias'),
    ]

    operations = [
        migrations.RunPython(sembrar_palabras_y_colores, revertir),
    ]
