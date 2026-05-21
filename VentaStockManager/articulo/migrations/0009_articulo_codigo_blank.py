"""
Permitir crear Articulos sin `codigo` (blank=True).

El campo se auto-completa en `Articulo.save()` con un código único
generado a partir de iniciales del nombre + 4 dígitos (con retry en
caso de colisión).

NO se agrega unique=True porque el dump legacy tiene duplicados; la
unicidad SOLO se garantiza para los códigos auto-generados.

NO hay data migration: los artículos existentes con codigo='' quedan
así hasta que sean editados, momento en el que save() les asigna
uno nuevo (efectivamente un backfill perezoso).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('articulo', '0008_listaprecios_tipo_ajuste'),
    ]

    operations = [
        migrations.AlterField(
            model_name='articulo',
            name='codigo',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
