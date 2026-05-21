from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('articulo', '0006_listaprecios_listapreciositem_listaprecios_articulos_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='listaprecios',
            name='share_token',
            field=models.UUIDField(
                null=True,
                blank=True,
                unique=True,
                help_text=(
                    'UUID que se usa en la URL pública. NULL = link no '
                    'compartido o revocado.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='listaprecios',
            name='share_expira_at',
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text=(
                    'Fecha de expiración del link público. NULL = no expira '
                    '(no recomendado; el flujo normal usa el default de '
                    'ConfiguracionGeneral.lista_precios_link_dias).'
                ),
            ),
        ),
    ]
