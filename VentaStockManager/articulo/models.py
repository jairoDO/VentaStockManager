
import random
import string
from django.db import models
import random
from django.utils.html import format_html


# ---------------------------------------------------------------------------
# Categorías
# ---------------------------------------------------------------------------
class Categoria(models.Model):
    """
    Agrupador de artículos para reportes, listas de precios filtradas
    y descuentos por grupo.

    Las categorías son metadata local de la app — no se sincronizan
    con Google Sheets. Si en el futuro decidimos migrar Sheets a la
    app, las categorías ya están listas y se exportan a una columna
    extra de la planilla.
    """

    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.TextField(blank=True, default='')
    # Color hex (con `#`) para badges en el admin. Default neutro.
    color = models.CharField(
        max_length=7,
        default='#607d8b',
        help_text='Color hex (ej. #2196f3) para mostrar la categoría en badges.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']
        verbose_name = 'categoría'
        verbose_name_plural = 'categorías'

    def __str__(self):
        return self.nombre


class ListaPrecios(models.Model):
    """
    Lista de precios personalizada para un cliente. Pensada para
    generar un PDF y compartírselo (por WhatsApp, mail, papel).

    Casos de uso típicos:
      - "Lista mensual" del cliente Pérez, con sus 30 productos
        más comunes y los precios pactados con él.
      - "Promo de fin de mes" — descuento extra del 5% sobre la
        lista del cliente para empujarle un cierre.

    Los items se persisten via `ListaPreciosItem` (M2M con orden),
    PERO el precio NO se congela — se calcula al generar el PDF
    para que refleje:
      1. PrecioCliente (si lo hay para ese par cliente+articulo)
      2. Sino, precio minorista
      3. Más descuento_porcentaje de esta lista, si lo tiene

    Eso evita que la lista quede desactualizada cuando suben los
    precios. Si Osvaldo quiere "congelar" un precio específico para
    un cliente, lo hace creando un PrecioCliente, no acá.
    """

    cliente = models.ForeignKey(
        'cliente.Cliente',
        related_name='listas_precios',
        on_delete=models.CASCADE,
    )
    nombre = models.CharField(
        max_length=120,
        help_text='Etiqueta interna ("Lista marzo", "Promo navidad", etc.)',
    )
    descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text=(
            'Descuento adicional aplicado a TODA la lista (sobre el '
            'precio que ya considera PrecioCliente si lo hay). 0 = sin '
            'descuento extra.'
        ),
    )
    descuento_motivo = models.CharField(max_length=255, blank=True, default='')
    creado_por = models.ForeignKey(
        'auth.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    articulos = models.ManyToManyField(
        'Articulo',
        through='ListaPreciosItem',
        related_name='listas_precios',
    )

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'lista de precios'
        verbose_name_plural = 'listas de precios'
        indexes = [
            # Búsqueda típica: "todas las listas de este cliente".
            models.Index(fields=['cliente', '-updated_at']),
        ]

    def __str__(self):
        return f'{self.nombre} — {self.cliente.nombre_completo()}'

    def cantidad_items(self) -> int:
        return self.items.count()


class ListaPreciosItem(models.Model):
    """
    Through-table M2M de `ListaPrecios.articulos`. Existe para poder
    persistir el `orden` (el operador a veces quiere agruparlos
    distinto del orden alfabético).
    """

    lista = models.ForeignKey(
        ListaPrecios,
        related_name='items',
        on_delete=models.CASCADE,
    )
    articulo = models.ForeignKey(
        'Articulo',
        # PROTECT para que no se "pierdan" items al borrar artículos
        # con listas asociadas. Antes hay que sacarlos de las listas.
        on_delete=models.PROTECT,
    )
    orden = models.PositiveIntegerField(default=0)
    # Nota opcional por item ("solo si lleva 50+", "regalo con cada
    # 10", etc.) que se muestra al lado del precio en el PDF.
    nota = models.CharField(max_length=120, blank=True, default='')

    class Meta:
        ordering = ['orden', 'articulo__nombre']
        verbose_name = 'item de lista de precios'
        verbose_name_plural = 'items de lista de precios'
        constraints = [
            models.UniqueConstraint(
                fields=['lista', 'articulo'],
                name='un_articulo_por_lista',
            ),
        ]

    def __str__(self):
        return f'{self.lista.nombre} · {self.articulo.nombre}'


class ReglaCategoria(models.Model):
    """
    Reglas de auto-asignación de categoría por matching del nombre.

    Modelo: si el nombre del artículo CONTIENE alguna de las
    `palabras_clave` (case-insensitive), se le asigna esta categoría.

    Las reglas se aplican corriendo el management command
    `aplicar_reglas_categoria` (manual o desde el panel de tareas).
    Por seguridad, NO pisamos categorías ya asignadas — solo
    completamos las que están en NULL.

    Si dos reglas matchean el mismo artículo (ej. "coca cola"
    matchea "coca" y "cola"), gana la de menor `prioridad` (default 100).
    """

    categoria = models.ForeignKey(
        Categoria,
        related_name='reglas',
        on_delete=models.CASCADE,
    )
    # JSONField para no tener que armar una tabla aparte de
    # keyword. La validación de "lista de strings" la hace el form.
    palabras_clave = models.JSONField(
        default=list,
        help_text=(
            'Lista de strings. Si el nombre del artículo contiene '
            'cualquiera (case-insensitive), se le asigna esta categoría. '
            'Ej: ["alfajor", "chupetin", "chicle"]'
        ),
    )
    prioridad = models.PositiveIntegerField(
        default=100,
        help_text='Menor número = mayor prioridad. Útil para resolver ambigüedad cuando dos reglas matchean el mismo artículo.',
    )
    activa = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['prioridad', 'categoria__nombre']
        verbose_name = 'regla de categoría'
        verbose_name_plural = 'reglas de categoría'

    def __str__(self):
        keywords = ', '.join(self.palabras_clave[:3]) if self.palabras_clave else '(sin palabras)'
        if self.palabras_clave and len(self.palabras_clave) > 3:
            keywords += '…'
        return f'{self.categoria.nombre} ← [{keywords}]'


# ---------------------------------------------------------------------------
# Artículo
# ---------------------------------------------------------------------------
class Articulo(models.Model):
    id = models.AutoField(primary_key=True)
    codigo = models.CharField(max_length=255)
    codigo_interno = models.CharField(max_length=50, blank=True, null=True)
    nombre = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True, null=True)
    stock = models.PositiveIntegerField()
    precio_minorista = models.DecimalField(max_digits=10, decimal_places=2,  null=True)
    precio_mayorista = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    vencimiento = models.DateField(blank=True)
    marca = models.CharField(max_length=255, blank=True, null=True, default='Generico')
    cantidad_por_mayor = models.PositiveIntegerField(default=100, null=True)
    # FK a Categoria nullable: artículos viejos arrancan sin categoría
    # y se les asigna después corriendo las reglas. SET_NULL para
    # que borrar una categoría NO borre los artículos (que es lo que
    # haría CASCADE — desastre).
    categoria = models.ForeignKey(
        Categoria,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='articulos',
    )
    # FK al Proveedor del artículo. Permite filtrar el listado y
    # disparar bulk updates de precio por proveedor ("aumentar 10%
    # a todos los productos de X"). Nullable porque los artículos
    # legacy del dump no tienen esta info; se completan a mano o
    # con una bulk action.
    # El modelo Proveedor vive en `compra.models` (es donde se llevan
    # las compras al proveedor), por eso lo referenciamos por string
    # para evitar un import circular si en algún momento compra
    # importa articulo (hoy no, pero curarnos en salud).
    proveedor = models.ForeignKey(
        'compra.Proveedor',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='articulos',
    )

    def save(self, *args, **kwargs):
        if not self.codigo_interno:
            # Obtener las iniciales del nombre del artículo
            iniciales = ''.join(word[0] for word in self.nombre.split())
            # Generar un número aleatorio de 4 dígitos
            random_number = ''.join(random.choices(string.digits, k=4))
            # Combinar las iniciales y el número aleatorio
            self.codigo_interno = iniciales.upper() + random_number
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.codigo} - {self.codigo_interno} | {self.marca + " - " if self.marca else " - "} |  {self.nombre}' \
               f' | Min ${self.precio_minorista} | May ${self.precio_mayorista} '\
               f'|umbral {self.cantidad_por_mayor}'
                   

    def get_articulo_short_name(self):
        return  f'{self.codigo_interno} {self.marca if self.marca != "Generico" else ""} {self.nombre}'
    
    def sugerir_codigo_interno(self):
        
        if not self.nombre:
            return self.id
        else:
            iniciales = [palabra[0] for palabra in self.nombre.split() if palabra]
            random_int = [str(random.randint(0, 10) for i in range(3))]
            return ''.join(iniciales + random_int)

    class Meta:
        ordering = ['codigo']
            
  