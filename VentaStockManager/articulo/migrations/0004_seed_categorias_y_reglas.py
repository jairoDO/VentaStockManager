"""
Seed inicial de categorías típicas de un kiosco mayorista argentino.

Crea las categorías y las reglas de auto-asignación con keywords
razonables. Osvaldo después puede:
  - Editar / borrar categorías que no le sirvan
  - Agregar keywords a las reglas (ej. una marca específica)
  - Crear categorías propias

Idempotente: usa get_or_create. Si la migration se aplica dos veces
no duplica nada.

Si querés revertir, hace falta entender que las reglas borradas no
desasignan las categorías ya asignadas a artículos (eso queda en
`Articulo.categoria` y es independiente). Por eso el rollback acá
solo borra reglas/categorías sembradas, no toca artículos.
"""

from django.db import migrations


SEED = [
    {
        'nombre': 'Golosinas',
        'color': '#e91e63',
        'descripcion': 'Alfajores, chupetines, chicles, gomitas y similares.',
        'keywords': [
            'alfajor', 'chupetin', 'chupetín', 'chicle', 'gomita',
            'caramelo', 'oblea', 'turron', 'turrón', 'marroc',
            'bombon', 'bombón', 'bon bon', 'mantecol', 'jamoncito',
            'pastilla', 'sugus', 'mogul', 'rocklet', 'beldent',
            'topline', 'baton', 'flynn paff',
        ],
    },
    {
        'nombre': 'Bebidas',
        'color': '#2196f3',
        'descripcion': 'Gaseosas, jugos, aguas, isotónicas.',
        'keywords': [
            'coca', 'pepsi', 'sprite', 'fanta', '7up', 'seven up',
            'pritty', 'manaos', 'paso de los toros', 'mirinda',
            'jugo', 'gaseosa', 'agua', 'soda', 'tonica', 'tónica',
            'cunnington', 'powerade', 'gatorade', 'levite', 'aquarius',
            'baggio', 'ades', 'cepita', 'villa del sur',
        ],
    },
    {
        'nombre': 'Galletitas',
        'color': '#ff9800',
        'descripcion': 'Galletitas dulces y saladas.',
        'keywords': [
            'galletit', 'galleta', 'oreo', 'chocolinas', 'criollitas',
            'manon', 'mañanitas', 'cerealitas', 'don satur', 'pepitos',
            'sonrisas', 'merengadas', 'rumba', 'vocaciones', 'okebon',
            'opera', 'tita', 'rhodesia', 'pituka', 'pepito',
            'sport', 'club social', 'lincoln', 'rumba',
        ],
    },
    {
        'nombre': 'Snacks',
        'color': '#ffc107',
        'descripcion': 'Papas fritas, palitos, chizitos, snacks salados.',
        'keywords': [
            'papas frit', 'lays', 'pringles', 'doritos', 'pehuamar',
            'krachitos', 'palitos', 'palitos salados', 'chizitos',
            'chizito', 'cheetos', '3d', 'mani', 'maní', 'pop',
            'pochoclo', 'pororó',
        ],
    },
    {
        'nombre': 'Lácteos',
        'color': '#4caf50',
        'descripcion': 'Yogur, leche, queso, postres lácteos.',
        'keywords': [
            'yogur', 'yoghurt', 'leche', 'queso', 'manteca', 'crema',
            'postre', 'dulce de leche', 'flan', 'serenito', 'ser',
            'la serenisima', 'sancor', 'ilolay', 'milkaut',
        ],
    },
    {
        'nombre': 'Limpieza',
        'color': '#00bcd4',
        'descripcion': 'Productos de limpieza e higiene.',
        'keywords': [
            'lavandina', 'ayudin', 'ayudín', 'detergente', 'jabon',
            'jabón', 'cif', 'mr musculo', 'clorox', 'magistral',
            'algodon', 'algodón', 'esponja', 'lampazo', 'trapo',
            'papel higienico', 'papel higiénico', 'rollo cocina',
        ],
    },
    {
        'nombre': 'Cigarrillos',
        'color': '#795548',
        'descripcion': 'Cigarrillos y tabaco.',
        'keywords': [
            'cigarrillo', 'marlboro', 'philip morris', 'lucky strike',
            'parisiennes', 'jockey', 'camel', 'pall mall',
            'tabaco', 'pucho',
        ],
    },
    {
        'nombre': 'Panificados',
        'color': '#9c27b0',
        'descripcion': 'Pan, prepizzas, facturas, productos de panadería.',
        'keywords': [
            'pan ', 'pan,', 'pan rallado', 'rebozador', 'prepizza',
            'tortilla', 'tostada', 'budín', 'budin', 'factura',
            'medialuna', 'criollo', 'criolla', 'facturita',
            'bizcocho', 'bizcochito',
        ],
    },
    {
        'nombre': 'Otros',
        'color': '#9e9e9e',
        'descripcion': 'Categoría fallback para artículos que no encajan en otras.',
        # Sin keywords: esta categoría no se asigna automáticamente.
        # Osvaldo la puede usar manualmente.
        'keywords': [],
    },
]


def crear_seed(apps, schema_editor):
    Categoria = apps.get_model('articulo', 'Categoria')
    ReglaCategoria = apps.get_model('articulo', 'ReglaCategoria')

    for entry in SEED:
        cat, _ = Categoria.objects.get_or_create(
            nombre=entry['nombre'],
            defaults={
                'color': entry['color'],
                'descripcion': entry['descripcion'],
            },
        )
        if entry['keywords']:
            # Una sola regla por categoría con todos los keywords
            # adentro. Más simple que muchas reglas chiquitas. Si el
            # operador quiere prioridades distintas, después puede
            # partirla en varias.
            ReglaCategoria.objects.get_or_create(
                categoria=cat,
                defaults={
                    'palabras_clave': entry['keywords'],
                    'prioridad': 100,
                    'activa': True,
                },
            )
    print(f'  Seed: {Categoria.objects.count()} categorías, {ReglaCategoria.objects.count()} reglas.')


def borrar_seed(apps, schema_editor):
    Categoria = apps.get_model('articulo', 'Categoria')
    ReglaCategoria = apps.get_model('articulo', 'ReglaCategoria')
    nombres = [e['nombre'] for e in SEED]
    # OJO: solo borramos las reglas/categorías que hayamos sembrado
    # (por nombre exacto). Si Osvaldo creó las suyas con esos mismos
    # nombres, se las vamos a borrar — pero ese es un risk razonable
    # en una migration de rollback.
    ReglaCategoria.objects.filter(categoria__nombre__in=nombres).delete()
    Categoria.objects.filter(nombre__in=nombres).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('articulo', '0003_categoria_reglacategoria_articulo_categoria'),
    ]

    operations = [
        migrations.RunPython(crear_seed, borrar_seed),
    ]
