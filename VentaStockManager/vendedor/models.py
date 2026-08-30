from django.db import models
from django.contrib.auth.models import User
import re

def validar_cuil(cuil):
    """
    Validar un CUIL Argentino.
    El CUIL debe tener 11 dígitos.
    """
    # Regex para verificar el formato correcto
    if not re.match(r'^\d{2}-\d{8}-\d$', cuil):
        return False
    
    # Remover los guiones
    cuil = cuil.replace('-', '')

    # Coeficientes para validación
    coef = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    suma = 0

    for i in range(10):
        suma += int(cuil[i]) * coef[i]

    digito_verificador = (11 - (suma % 11)) % 11
    return digito_verificador == int(cuil[-1])

# Ejemplo de uso:
# print(validar_cuil("20-12345678-9"))

def validate_cuil(value):
    if not validar_cuil(value):
        raise ValidationError(f'{value} no es un CUIL válido')
    
class Vendedor(models.Model):
    GENERO_CHOICES = [
        ('M', 'Masculino'),
        ('F', 'Femenino'),
    ]
    id = models.AutoField(primary_key=True)
    usuario = models.OneToOneField(User, on_delete=models.CASCADE)
    nombre = models.TextField(blank=False)
    apellido = models.TextField(blank=False, default='Sin apellido')
    # perfil = models.OneToOneField(User, on_delete=models.CASCADE)
    telefono = models.TextField(blank=True, null=True)
    
  # Campos adicionales del vendedor (opcional)
  # Ej: nombre_completo, telefono, etc.
    def fullname(self):
            return self.nombre + ' ' + self.apellido

    def __str__(self):
        return self.usuario.username

    def display_name(self):
        """
        Nombre "completo" del vendedor para UI customer-facing (PDF de
        pedido + listado del admin).

        Formato: `username (nombre apellido)` si nombre/apellido están
        cargados con data útil. Solo `username` si no.

        Pensado para que el operador vea el username (lo que tipea para
        loguear, fuente de verdad) y al mismo tiempo el cliente vea el
        nombre real entre paréntesis.

        Skips:
          - `apellido='Sin apellido'` (default legacy del modelo).
          - Si nombre+apellido normalizados == username, no duplicamos.
        """
        try:
            username = self.usuario.username if self.usuario else ''
        except Exception:  # noqa: BLE001
            username = ''
        nombre = (self.nombre or '').strip()
        apellido = (self.apellido or '').strip()
        if apellido.lower() == 'sin apellido':
            apellido = ''
        full = ' '.join(p for p in [nombre, apellido] if p)
        if full and full.lower() != username.lower():
            if username:
                return f'{username} ({full})'
            return full
        return username or '-'

    class Meta:
        verbose_name = "Vendedor"
        verbose_name_plural = "Vendedores"# Create your models here.


class Repartidor(models.Model):
    """
    Perfil operativo de un usuario que realiza entregas.

    Es independiente de Vendedor a propósito: el mismo User puede tener
    ambos perfiles cuando una persona vende y también reparte.
    """

    usuario = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='repartidor',
    )
    nombre = models.CharField(max_length=150, blank=True, default='')
    telefono = models.CharField(max_length=50, blank=True, default='')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ('nombre', 'usuario__username')
        verbose_name = 'Repartidor'
        verbose_name_plural = 'Repartidores'

    def __str__(self):
        return self.nombre.strip() or self.usuario.get_full_name() or self.usuario.username
