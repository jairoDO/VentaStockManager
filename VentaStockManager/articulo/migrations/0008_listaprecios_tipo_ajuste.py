"""
Agregar `tipo_ajuste` ('descuento'|'aumento') a ListaPrecios.

Por qué:
  La lista soportaba solo DESCUENTO global. El operador pidió poder
  aplicar también AUMENTOS porcentuales (ej. "lista marzo +5% por
  ajuste de costos") sin tener que modificar artículo por artículo.

  Mantenemos `descuento_porcentaje` como nombre del campo (no
  rompemos compatibilidad con datos viejos / APIs) pero ahora el
  número se interpreta junto con `tipo_ajuste`:
    - tipo_ajuste='descuento' (default) → precio * (1 - pct/100)
    - tipo_ajuste='aumento'             → precio * (1 + pct/100)

Default 'descuento' garantiza que las listas existentes sigan
comportándose IGUAL que antes — la migración no cambia ningún número.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('articulo', '0007_listaprecios_share_token_listaprecios_share_expira_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='listaprecios',
            name='tipo_ajuste',
            field=models.CharField(
                choices=[('descuento', 'Descuento'), ('aumento', 'Aumento')],
                default='descuento',
                help_text=(
                    'Cómo se interpreta el % global: descuento (resta) o '
                    'aumento (suma). El número en sí siempre es positivo '
                    '(0–100).'
                ),
                max_length=10,
            ),
        ),
    ]
