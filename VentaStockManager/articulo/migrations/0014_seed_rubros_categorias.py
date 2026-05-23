"""
Data migration: seedea Rubros + Categorías con el mapeo razonable
para "Golosinas Insa" (mayo 2026).

Idempotente: usa get_or_create por nombre. Si el operador ya creó
algo a mano, NO lo pisa.

Estructura sembrada:
  10 Rubros, 34 Categorías. Las categorías que ya existen sin rubro
  reciben el rubro del mapeo. Los artículos no se tocan en esta
  migración — se cargan aparte con el comando cargar_lista_precios_xlsx.

Por qué data migration y no fixture:
  - Las fixtures requieren un comando manual (loaddata) que NO corre
    en el flujo de deploy de Render.
  - Una data migration corre automáticamente en `python manage.py
    migrate`, que Render ya ejecuta en cada deploy.
  - Si en el futuro queremos cambiar el mapeo, hacemos otra migración
    (idempotente igual: get_or_create + actualización si hace falta).
"""
from django.db import migrations


# Mapeo categoría → rubro. MISMO que en cargar_lista_precios_xlsx.py
# para mantener consistencia. Si cambia uno, cambiar el otro.
RUBROS_CON_CATEGORIAS = {
    'Golosinas': {
        'color': '#EC4899',
        'orden': 1,
        'categorias': [
            'MASTICABLE(caramelo)',
            'GOMITASS',
            'PASTILLAS',
            'ALFAJORES',
            'CHOCOLATES',
            'CHUPETINES',
            'CHICLES',
        ],
    },
    'Galletas y Snacks': {
        'color': '#F59E0B',
        'orden': 2,
        'categorias': ['GALLETAS', 'SNACK'],
    },
    'Bebidas': {
        'color': '#3B82F6',
        'orden': 3,
        'categorias': [
            'Bebidas',
            'JUGOS TANG',
            'JUGOS CLIGHT',
            'JUGOS RINDE 2',
            'JUGOS NOEL',
            'JUGOS JA!',
            'BEBIDAS ALCOHOLICAS',
        ],
    },
    'Almacén': {
        'color': '#10B981',
        'orden': 4,
        'categorias': ['ALMACÉN', 'CONDIMENTO Y SABORES'],
    },
    'Limpieza e Higiene': {
        'color': '#06B6D4',
        'orden': 5,
        'categorias': ['LIMPIEZA', 'HIGIENE PERSONAL'],
    },
    'Helados': {
        'color': '#8B5CF6',
        'orden': 6,
        'categorias': [
            'HELADOS',
            'Línea tasitas',
            'Línea postres',
            'Línea familiar',
            'Tarros de 10 litros de agua',
            'Tarros de 10 litros sabores comunes',
            'Tarros de 10 litros sabores especiales',
            'Tarros de 10 litros SÚPER sabores',
            'INSUMOS PARA HELADERIA',
        ],
    },
    'Salud': {
        'color': '#EF4444',
        'orden': 7,
        'categorias': ['ANALGÉSICOS 💊'],
    },
    'Tabaco': {
        'color': '#78716C',
        'orden': 8,
        'categorias': ['CIGARRILLOS.'],
    },
    'Estacional': {
        'color': '#F97316',
        'orden': 9,
        'categorias': ['PRODUCTOS NAVIDEnOS', 'PIROTECNIA'],
    },
    'Otros': {
        'color': '#9CA3AF',
        'orden': 99,
        'categorias': ['VARIOS', 'INPORTADOS', 'EXTRAS'],
    },
}


def sembrar(apps, schema_editor):
    """
    Corre en forward. Crea rubros y categorías que no existan, y
    asigna rubro a las categorías que ya existían sin uno.
    """
    Rubro = apps.get_model('articulo', 'Rubro')
    Categoria = apps.get_model('articulo', 'Categoria')

    for rubro_nombre, conf in RUBROS_CON_CATEGORIAS.items():
        rubro, _ = Rubro.objects.get_or_create(
            nombre=rubro_nombre,
            defaults={
                'color': conf['color'],
                'orden': conf['orden'],
            },
        )
        for cat_nombre in conf['categorias']:
            cat, creada = Categoria.objects.get_or_create(
                nombre=cat_nombre,
                # Solo asignamos rubro al CREAR (defaults). Si la
                # categoría ya existía con OTRO rubro, respetamos lo
                # que decidió el operador a mano.
                defaults={'rubro': rubro},
            )
            # Caso común: la categoría ya existía (la creó el comando
            # cargar_lista_precios viejo) pero NO tenía rubro. Se lo
            # asignamos ahora.
            if not creada and cat.rubro_id is None:
                cat.rubro = rubro
                cat.save(update_fields=['rubro'])


def revertir(apps, schema_editor):
    """
    Reverse: borra los rubros sembrados. Las categorías quedan con
    rubro=NULL por el on_delete=SET_NULL del modelo. No tocamos las
    categorías porque pueden haberlas modificado a mano.
    """
    Rubro = apps.get_model('articulo', 'Rubro')
    Rubro.objects.filter(nombre__in=RUBROS_CON_CATEGORIAS.keys()).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articulo', '0013_rubro'),
    ]

    operations = [
        migrations.RunPython(sembrar, revertir),
    ]
