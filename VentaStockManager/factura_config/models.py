from django.db import models
from django.core.exceptions import ValidationError
import os

def validate_font_file(value):
    ext = os.path.splitext(value.name)[1].lower()
    valid_extensions = ['.ttf', '.otf']
    max_size = 5 * 1024 * 1024  # 5MB

    if ext not in valid_extensions:
        raise ValidationError('Solo se permiten archivos de fuente (.ttf, .otf)')
    
    if value.size > max_size:
        raise ValidationError('El archivo no puede ser mayor a 5MB')

class FacturaConfiguration(models.Model):
    FONT_CHOICES = [
        ('Helvetica', 'Helvetica'),
        ('Helvetica-Bold', 'Helvetica Bold'),
        ('Times-Roman', 'Times Roman'),
        ('Times-Bold', 'Times Bold'),
        ('Courier', 'Courier'),
        ('Courier-Bold', 'Courier Bold'),
    ]
    
    COLOR_CHOICES = [
        ('black', 'Negro'),
        ('blue', 'Azul'),
        ('red', 'Rojo'),
        ('green', 'Verde'),
        ('gray', 'Gris'),
    ]

    # Page size
    page_width = models.FloatField(default=21, help_text="Ancho en cm")
    # page_height = models.FloatField(default=29.7, help_text="Alto en cm")
    
    # Margins
    margin_top = models.FloatField(default=0, help_text="Margen superior en cm")
    margin_bottom = models.FloatField(default=0, help_text="Margen inferior en cm")
    margin_left = models.FloatField(default=0.5, help_text="Margen izquierdo en cm")
    margin_right = models.FloatField(default=0.5, help_text="Margen derecho en cm")
    
    # Fonts.
    # OJO: el `default` tiene que ser el VALUE de la choice
    # ('Helvetica-Bold' con guión), NO el label ('Helvetica Bold'
    # con espacio). reportlab.pdfmetrics busca el font por value;
    # con el label tira KeyError 'Helvetica Bold' al generar el PDF.
    # Antes los defaults eran labels y rompían cuando no había una
    # `FacturaConfiguration` cargada en DB (fallback a in-memory
    # con defaults).
    header_font = models.CharField(
        max_length=50,
        choices=FONT_CHOICES,
        default='Helvetica-Bold',
        help_text="Fuente para encabezados"
    )
    content_font = models.CharField(
        max_length=50,
        choices=FONT_CHOICES,
        default='Helvetica-Bold',
        help_text="Fuente para contenido"
    )

    total_font = models.CharField(
        max_length=50,
        choices=FONT_CHOICES,
        default='Helvetica-Bold',
        help_text="Fuente para contenido"
    )
    # custom_font = models.FileField(
    #     upload_to='fonts/',
    #     null=True,
    #     blank=True,
    #     validators=[validate_font_file],
    #     help_text="Archivo de fuente personalizada (.ttf o .otf)"
    # )
    
    # # Font sizes
    font_size_header = models.IntegerField(default=9, help_text="Tamaño de fuente para encabezados")
    font_size_content = models.IntegerField(default=9, help_text="Tamaño de fuente para contenido")
    font_size_total = models.IntegerField(default=12, help_text="Tamaño de fuente para totales")
    
    # Colors
    header_color = models.CharField(
        max_length=20,
        choices=COLOR_CHOICES,
        default='black',
        help_text="Color para encabezados"
    )
    content_color = models.CharField(
        max_length=20,
        choices=COLOR_CHOICES,
        default='black',
        help_text="Color para contenido"
    )
    
    # Table styles
    table_border_color = models.CharField(
        max_length=20,
        choices=COLOR_CHOICES,
        default='black',
        help_text="Color del borde de las tablas"
    )
    table_border_width = models.FloatField(
        default=1,
        help_text="Grosor del borde de las tablas en puntos",
    )
    
    # Column widths in cm
    column_width_article = models.FloatField(default=4, help_text="Ancho columna artículo en cm")
    column_width_quantity = models.FloatField(default=1, help_text="Ancho columna cantidad en cm")
    column_width_price = models.FloatField(default=1.5, help_text="Ancho columna precio en cm")
    column_width_total = models.FloatField(default=1.5, help_text="Ancho columna total en cm")

    class Meta:
        verbose_name = "Configuración Factura"
        verbose_name_plural = "Configuración Factura"
        app_label = "factura_config"

    def __str__(self):
        return "Configuración Factura"