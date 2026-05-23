"""
Agrega el modelo Rubro (agrupador de categorías) y la FK opcional
Categoria.rubro.

Por qué nullable: las categorías existentes no tienen rubro asignado
todavía. El operador las clasificará a mano desde el admin (o con la
bulk action que viene aparte). SET_NULL para que borrar un rubro no
arrastre las categorías al vacío.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('articulo', '0012_solicitudlistacliente'),
    ]

    operations = [
        migrations.CreateModel(
            name='Rubro',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(max_length=80, unique=True)),
                ('descripcion', models.TextField(blank=True, default='')),
                ('color', models.CharField(default='#9CA3AF', help_text='Color hex (ej. #FF5733) para mostrar el rubro en el selector.', max_length=7)),
                ('orden', models.PositiveIntegerField(default=0, help_text='Para ordenar en el selector. Menor = aparece primero.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'rubro',
                'verbose_name_plural': 'rubros',
                'ordering': ('orden', 'nombre'),
            },
        ),
        migrations.AddField(
            model_name='categoria',
            name='rubro',
            field=models.ForeignKey(
                blank=True,
                null=True,
                help_text='Rubro al que pertenece (Golosinas, Bebidas, Almacén, etc.). Opcional.',
                on_delete=models.deletion.SET_NULL,
                related_name='categorias',
                to='articulo.rubro',
            ),
        ),
    ]
