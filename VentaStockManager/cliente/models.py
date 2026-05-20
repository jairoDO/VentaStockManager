# -*- coding: utf-8 -*-
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.urls import reverse


class Cliente(models.Model):
    """
    A model representing a client.
    """
    GENERO_CHOICES = [
        ('M', 'Masculino'),
        ('F', 'Femenino'),
    ]
    nombre = models.TextField(blank=False)
    apellido = models.TextField(blank=False)
    telefono = models.TextField(default='00000000', blank=True, null=True)
    # Número normalizado para WhatsApp. Formato: solo dígitos, con
    # prefijo internacional (sin `+`). Ejemplo: 5491155551234.
    # Se llena automáticamente desde `telefono` con un parser AR
    # best-effort, pero se puede editar a mano cuando el legacy es
    # ambiguo. Si queda vacío, el cliente no recibe WhatsApp.
    whatsapp_number = models.CharField(
        max_length=20,
        blank=True,
        default='',
        help_text='Solo dígitos, con código de país. Ej: 5491155551234',
    )
    # Opt-in explícito: el cliente NO recibe comunicaciones masivas
    # de WhatsApp hasta que alguien lo habilite a mano (o por bulk
    # action). Esto cubre la parte legal (consentimiento) y previene
    # que una campaña salga a clientes que no aceptaron recibirla.
    # Es independiente de `whatsapp_number` — alguien puede tener
    # número cargado pero NO querer recibir promos.
    puede_recibir_whatsapp = models.BooleanField(
        default=False,
        help_text=(
            'Si está marcado, el cliente recibe campañas de WhatsApp. '
            'Si no, queda excluido de la audiencia aunque tenga número '
            'cargado. Cambiar por consentimiento explícito.'
        ),
    )
    direccion = models.CharField(max_length=50, default='direccion', blank=True, null=True)
    codigo_interno = models.CharField(max_length=50, default='no-codigo', blank=True, null=True)

    def nombre_completo(self):
        return f"{self.nombre} {self.apellido}"
    
    def get_str_with_user(self, user):
        if user.is_superuser:
            return f"{self.nombre} {self.apellido} - {self.direccion}"
        return str(self)
    # def clean(self):
    #     """       
    #     Clean method to validate the client's age.
    #     """
    #     if self.edad and self.edad <= 0:
    #         raise ValidationError("La edad debe ser mayor a 0")
        
    class Meta:
        """
        Meta class for the Cliente model.
        """
        verbose_name = "cliente"
        verbose_name_plural = "clientes"

        # def get_latest_by(self):
        #     pass

        # def get_ordering(self):
        #     pass

    def __str__(self):
        return self.nombre + "  " + self.apellido + f" ({self.direccion})" if self.direccion else "(sin direccion)"

    def get_absolute_url(self):
        """
        Get the absolute URL for the client detail view.
        """
        return reverse("cliente_detail", kwargs={"pk": self.pk})

    @property
    def saldo(self):
        """
        Saldo actual del cliente. Convención:
          - Positivo  ⇒ el negocio le debe al cliente (saldo a favor).
          - Negativo  ⇒ el cliente le debe al negocio.

        El saldo NO se persiste como campo: se calcula sumando todos
        los movimientos de la cuenta corriente. Así nunca hay desfasaje
        entre "lo que dice el campo" y "lo que dicen los movimientos".
        """
        try:
            cuenta = self.cuenta
        except CuentaCliente.DoesNotExist:
            return Decimal('0')
        return cuenta.saldo


# ---------------------------------------------------------------------------
# Cuenta corriente
# ---------------------------------------------------------------------------
class CuentaCliente(models.Model):
    """
    Cuenta corriente de un cliente. Hay una sola por cliente (OneToOne).

    El saldo NO se persiste — se calcula como `sum(movimientos.monto)`.
    Esto evita tener dos sources of truth (campo + tabla) que se
    desfasen. La contra es que cada lectura del saldo hace una query;
    si en el futuro la app crece, hacemos caching o un trigger, pero
    por ahora con índices sobre `cuenta_id` es más que suficiente.
    """

    cliente = models.OneToOneField(
        Cliente,
        related_name='cuenta',
        on_delete=models.CASCADE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'cuenta corriente'
        verbose_name_plural = 'cuentas corrientes'

    def __str__(self):
        return f'Cuenta de {self.cliente.nombre_completo()} (saldo {self.saldo})'

    @property
    def saldo(self):
        agg = self.movimientos.aggregate(s=Sum('monto'))
        return agg['s'] or Decimal('0')


class MovimientoCuenta(models.Model):
    """
    Cada cambio en el saldo de un cliente. La convención de signo es:
      - `monto > 0`  ⇒ el saldo del cliente sube (a favor del cliente)
      - `monto < 0`  ⇒ el saldo del cliente baja (cliente debe más)

    Tipos:
      - `venta_a_cuenta`: una venta no se pagó totalmente. monto < 0.
      - `pago`: el cliente trae plata para cancelar deuda. monto > 0.
      - `aplicacion_saldo`: una venta usa saldo a favor. monto < 0.
      - `excedente_venta`: una venta se cobró de más, queda a favor. monto > 0.
      - `ajuste`: corrección manual del admin. signo libre.

    El campo `venta` es opcional: solo lo llenan los tipos que vienen
    automáticos desde la pantalla de venta (`venta_a_cuenta`,
    `aplicacion_saldo`, `excedente_venta`). Los pagos y ajustes manuales
    quedan sin venta asociada.
    """

    TIPO_VENTA_A_CUENTA = 'venta_a_cuenta'
    TIPO_PAGO = 'pago'
    TIPO_AJUSTE = 'ajuste'
    TIPO_APLICACION_SALDO = 'aplicacion_saldo'
    TIPO_EXCEDENTE = 'excedente_venta'

    TIPO_CHOICES = [
        (TIPO_VENTA_A_CUENTA, 'Venta a cuenta'),
        (TIPO_PAGO, 'Pago recibido'),
        (TIPO_AJUSTE, 'Ajuste manual'),
        (TIPO_APLICACION_SALDO, 'Aplicación de saldo'),
        (TIPO_EXCEDENTE, 'Excedente de venta'),
    ]

    cuenta = models.ForeignKey(
        CuentaCliente,
        related_name='movimientos',
        on_delete=models.CASCADE,
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    monto = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Positivo = a favor del cliente. Negativo = el cliente debe.',
    )
    venta = models.ForeignKey(
        'venta.Venta',
        null=True,
        blank=True,
        related_name='movimientos_cuenta',
        on_delete=models.SET_NULL,
    )
    descripcion = models.TextField(blank=True, default='')
    creado_por = models.ForeignKey(
        'auth.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'movimiento de cuenta'
        verbose_name_plural = 'movimientos de cuenta'
        indexes = [
            # Aggregate de saldo: WHERE cuenta_id = ?  GROUP BY → indexable.
            models.Index(fields=['cuenta', 'created_at']),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.monto} ({self.created_at:%Y-%m-%d})'


# ---------------------------------------------------------------------------
# Precios pactados por cliente
# ---------------------------------------------------------------------------
class PrecioCliente(models.Model):
    """
    Precio acordado entre el negocio y un cliente para un artículo
    específico. Reemplaza al precio minorista/mayorista cuando se
    vuelve a cargar una venta de ese cliente con ese artículo.

    Origen del registro: cada vez que el operador edita el precio de
    un artículo durante la carga de una venta (poniéndolo distinto al
    sugerido por defecto), se crea o actualiza este registro. La idea
    es no perder el "trato" que se cerró con el cliente en la venta
    anterior — la próxima vez que vuelva por ese artículo, sale a ese
    precio sin que el operador tenga que acordarse.

    Es único por (cliente, articulo): solo hay un precio vigente por
    par. Si el operador edita el precio en una venta nueva, se pisa
    el anterior (con la auditoría de django-auditlog para ver el
    histórico).

    Para "olvidar" un precio pactado, hay que borrarlo desde el admin
    a mano — la idea es justamente que se persista hasta que alguien
    decida lo contrario.
    """

    cliente = models.ForeignKey(
        Cliente,
        related_name='precios_pactados',
        on_delete=models.CASCADE,
    )
    articulo = models.ForeignKey(
        'articulo.Articulo',
        related_name='precios_pactados',
        # Si se borra el artículo no nos importa perder el precio
        # pactado (no hay nada que vender ya). El PROTECT en
        # ArticuloVenta.articulo va a impedir borrar artículos con
        # historial igual.
        on_delete=models.CASCADE,
    )
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    venta_origen = models.ForeignKey(
        'venta.Venta',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='precios_pactados_generados',
        help_text='Venta en la que se cerró por primera vez este precio.',
    )
    creado_por = models.ForeignKey(
        'auth.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'precio pactado'
        verbose_name_plural = 'precios pactados'
        constraints = [
            models.UniqueConstraint(
                fields=['cliente', 'articulo'],
                name='precio_unico_por_cliente_articulo',
            ),
        ]
        indexes = [
            # Consulta más común: dado un cliente, traer todos los
            # artículos con precio pactado (o ver si hay para uno
            # específico). Indexamos por cliente_id.
            models.Index(fields=['cliente']),
        ]

    def __str__(self):
        return f'{self.cliente.nombre_completo()} → {self.articulo.nombre}: ${self.precio_unitario}'


#Permission.objects.create(
#   codename='puede_acceder_lista_articulos',  
#   name='Puede acceder a la lista de artículos'   





# # Obtén el grupo de usuarios o créalo si no existe
# clientes_group, created = Group.objects.get_or_create(name='Clientes')

# # Obtén el permiso o créalo si no existe
# permission, created = Permission.objects.get_or_create(
#     codename='puede_acceder_lista_articulos',  
#     name='Puede acceder a la lista de artículos'
# )

# # Agrega el permiso al grupo de usuarios
# clientes_group.permissions.add(permission)



# # Obtén el grupo de usuarios o créalo si no existe
# clientes_group, created = Group.objects.get_or_create(name='Clientes')

# # Obtén el permiso o créalo si no existe
# permission, created = Permission.objects.get_or_create(
#     codename='puede_acceder_lista_articulos',  
#     name='Puede acceder a la lista de artículos'
# )

# # Agrega el permiso al grupo de usuarios
# clientes_group.permissions.add(permission)
