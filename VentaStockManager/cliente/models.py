# -*- coding: utf-8 -*-
from decimal import Decimal

from django.conf import settings
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

    # Cómo prefiere ESTE cliente recibir la lista de precios. Si está
    # en NULL (default), se aplica el modo global de
    # `ConfiguracionGeneral.formato_default_lista_precios`. El operador
    # puede pisar este default por envío particular desde la pantalla
    # de difundir. Tres niveles en cascada: global → cliente → envío.
    FORMATO_LISTA_LINK = 'link'
    FORMATO_LISTA_PDF = 'pdf'
    FORMATO_LISTA_AMBOS = 'ambos'
    FORMATO_LISTA_TEXTO = 'texto'
    FORMATO_LISTA_CHOICES = [
        (FORMATO_LISTA_TEXTO, 'Solo texto (lista pegada en el mensaje)'),
        (FORMATO_LISTA_LINK, 'Solo link público (siempre actualizado)'),
        (FORMATO_LISTA_PDF, 'Solo PDF adjunto (queda en el chat)'),
        (FORMATO_LISTA_AMBOS, 'Ambos: PDF + link debajo'),
    ]
    formato_preferido_lista_precios = models.CharField(
        max_length=10,
        choices=FORMATO_LISTA_CHOICES,
        blank=True,
        default='',
        help_text=(
            'Cómo prefiere este cliente recibir la lista de precios. '
            'Si queda vacío, se usa el default global de Configuración. '
            'Si elegís uno acá, se aplica salvo que el operador lo pise '
            'al difundir.'
        ),
    )

    def save(self, *args, **kwargs):
        # Auto-derivar / normalizar `whatsapp_number`.
        #
        # Caso 1 — vacío: derivarlo de `telefono`.
        #   El operador típicamente carga el cliente con `telefono` y se
        #   olvida del `whatsapp_number`. Resultado: el cliente no
        #   aparece en Difundir (filtra por whatsapp_number no vacío).
        #
        # Caso 2 — incompleto (formato AR sin código país, ej '3513452496'
        #   en vez de '5493513452496'): re-normalizar.
        #   El bot manda al JID `<numero>@s.whatsapp.net`; sin código
        #   país, ese JID no existe en WhatsApp y el envío se pierde
        #   silenciosamente (WhatsApp acepta el packet pero no entrega).
        #   Es el bug que reportó el usuario en mayo 2026 — un cliente
        #   "jairo Testing" cargado con `3513452496` recibía "enviado"
        #   en las difusiones pero el mensaje nunca llegaba.
        #
        # Esto NO toca `puede_recibir_whatsapp` (opt-in explícito, regla
        # legal). Solo el formato del número.
        from .phone_utils import normalizar_telefono_ar

        # Caso 1: vacío + tiene telefono.
        if not self.whatsapp_number and self.telefono:
            normalizado = normalizar_telefono_ar(self.telefono)
            if normalizado:
                self.whatsapp_number = normalizado

        # Caso 2: tiene whatsapp_number pero le falta código país.
        # `normalizar_telefono_ar` agrega `549` cuando ve 10 dígitos
        # (móvil AR sin internacional). Si el resultado difiere del
        # original Y es más largo, asumimos que estaba incompleto.
        if self.whatsapp_number:
            normalizado = normalizar_telefono_ar(self.whatsapp_number)
            if normalizado and normalizado != self.whatsapp_number:
                # Defensivo: solo "promovemos" hacia más largo (agregar
                # código país). Nunca lo recortamos.
                if len(normalizado) > len(self.whatsapp_number):
                    self.whatsapp_number = normalizado

        super().save(*args, **kwargs)

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


class DireccionCliente(models.Model):
    """
    Domicilio reutilizable y geolocalizable de un cliente.

    `Cliente.direccion` se conserva por compatibilidad con pantallas
    legacy. Las funciones nuevas deben usar este modelo y el Pedido
    guarda además una copia de la dirección elegida para preservar el
    historial aunque el cliente cambie de domicilio más adelante.
    """

    FUENTE_LEGACY = 'legacy'
    FUENTE_GPS = 'gps'
    FUENTE_MANUAL = 'manual'
    FUENTE_GEOCODIFICADA = 'geocodificada'
    FUENTE_CHOICES = [
        (FUENTE_LEGACY, 'Importada del campo anterior'),
        (FUENTE_GPS, 'Ubicación GPS'),
        (FUENTE_MANUAL, 'Carga manual'),
        (FUENTE_GEOCODIFICADA, 'Dirección geocodificada'),
    ]

    cliente = models.ForeignKey(
        Cliente,
        related_name='direcciones',
        on_delete=models.CASCADE,
    )
    etiqueta = models.CharField(max_length=60, default='Principal')
    direccion_texto = models.CharField(max_length=255, blank=True, default='')
    localidad = models.CharField(max_length=120, blank=True, default='')
    provincia = models.CharField(max_length=120, blank=True, default='')
    referencia = models.TextField(blank=True, default='')
    latitud = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    longitud = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    precision_metros = models.PositiveIntegerField(null=True, blank=True)
    fuente = models.CharField(
        max_length=20,
        choices=FUENTE_CHOICES,
        default=FUENTE_MANUAL,
    )
    confirmada = models.BooleanField(default=False)
    es_principal = models.BooleanField(default=False)
    confirmada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='direcciones_cliente_confirmadas',
    )
    confirmada_en = models.DateTimeField(null=True, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-es_principal', '-actualizada_en')
        verbose_name = 'dirección de cliente'
        verbose_name_plural = 'direcciones de clientes'
        constraints = [
            models.UniqueConstraint(
                fields=('cliente',),
                condition=models.Q(es_principal=True),
                name='una_direccion_principal_por_cliente',
            ),
        ]

    def __str__(self):
        return self.direccion_texto or f'Dirección de {self.cliente}'

    @property
    def tiene_coordenadas(self):
        return self.latitud is not None and self.longitud is not None

    def confirmar(self, *, usuario=None):
        from django.utils import timezone

        self.confirmada = True
        self.confirmada_en = timezone.now()
        self.confirmada_por = usuario if getattr(usuario, 'is_authenticated', False) else None

    def establecer_como_principal(self):
        DireccionCliente.objects.filter(
            cliente=self.cliente,
            es_principal=True,
        ).exclude(pk=self.pk).update(es_principal=False)
        self.es_principal = True


# ---------------------------------------------------------------------------
# Cuenta corriente
# ---------------------------------------------------------------------------
class RecordatorioSaldoEnviado(models.Model):
    """
    Trace de cada recordatorio de saldo deudor mandado por WhatsApp
    a un cliente. Existe por dos razones:

      1. Auditoría: saber a quién se le mandó y cuándo, para resolver
         disputas ("nunca me avisaste") o medir efectividad del feature.
      2. Anti-spam: la task lee la fila más reciente por cliente y
         compara con `frecuencia_dias` del singleton — si pasó menos
         tiempo, NO le manda de nuevo.

    Si el feature se apaga y se vuelve a prender, la historia queda.
    El monto/dias se snapshotean al enviar — son útiles para entender
    a posteriori qué umbral activó el envío.
    """

    STATUS_ENVIADO = 'enviado'
    STATUS_FALLIDO = 'fallido'
    STATUS_CHOICES = [
        (STATUS_ENVIADO, 'Enviado'),
        (STATUS_FALLIDO, 'Fallido'),
    ]

    cliente = models.ForeignKey(
        'Cliente',
        related_name='recordatorios_saldo',
        on_delete=models.CASCADE,
    )
    saldo_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Monto del saldo al momento del recordatorio (negativo = debe).',
    )
    dias_desde_ultima_compra = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Días desde la última compra del cliente al momento del recordatorio.',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ENVIADO)
    error_msg = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'recordatorio de saldo enviado'
        verbose_name_plural = 'recordatorios de saldo enviados'
        indexes = [
            # Query típica: WHERE cliente_id=? ORDER BY created_at DESC LIMIT 1
            models.Index(fields=['cliente', '-created_at']),
        ]

    def __str__(self):
        return f'{self.cliente.nombre_completo()} · {self.status} · {self.created_at:%d/%m/%Y %H:%M}'


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
    # OneToOne con Pedido: este movimiento es EL pago canónico de este
    # pedido (creado por `pedido.set_monto_pagado()`). Permite
    # identificar y actualizar/borrar el movimiento sin parsear
    # descripciones cuando cambia `pedido.monto_pagado`.
    #
    # Solo lo setea la lógica de "registrar pago" (acción bulk + edit
    # del campo en el admin). Movimientos cargados por otras vías
    # (pagos a mano desde "Registrar pago" del cliente, ajustes, etc)
    # quedan con este campo en NULL — no son el pago canónico de un
    # pedido en particular.
    pedido_origen = models.OneToOneField(
        'venta.Pedido',
        null=True,
        blank=True,
        related_name='movimiento_pago',
        on_delete=models.SET_NULL,
        help_text=(
            'Pedido cuyo "monto_pagado" generó este movimiento. Si está '
            'seteado, este es el pago canónico de ese pedido y se '
            'edita/borra automáticamente cuando el operador cambia el '
            'campo monto_pagado del pedido.'
        ),
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


class AlertaClienteInactivo(models.Model):
    """
    Alerta interna que avisa cuando un cliente que SOLÍA comprar dejó
    de hacerlo por más tiempo del configurado (por defecto 30 días).

    La genera una tarea diaria (django-q2) que mira la última compra de
    cada cliente. Solo se generan alertas para clientes que tienen al
    menos una venta registrada (los que "compraron antes"): un cliente
    que nunca compró no está "inactivo", simplemente no es cliente
    todavía.

    Se resuelve automáticamente cuando el cliente vuelve a comprar: al
    guardar una venta se marcan como revisadas todas las alertas
    pendientes de ese cliente. También se puede resolver a mano desde
    el admin.

    Anti-spam: solo hay una alerta pendiente (revisada=False) por
    cliente a la vez. La tarea diaria no crea una nueva si ya existe
    una sin revisar para ese cliente.
    """

    cliente = models.ForeignKey(
        Cliente,
        related_name='alertas_inactividad',
        on_delete=models.CASCADE,
    )
    # Snapshot de la fecha de la última compra al momento de generar la
    # alerta. Puede ser null si por algún motivo la venta se borró, pero
    # normalmente siempre tiene valor (solo alertamos clientes con compras).
    ultima_compra = models.DateField(null=True, blank=True)
    # Días transcurridos desde la última compra al momento de generar la
    # alerta. Útil para mostrar "hace 45 días" sin recalcular.
    dias_inactivo = models.PositiveIntegerField(default=0)
    revisada = models.BooleanField(default=False)
    revisada_at = models.DateTimeField(null=True, blank=True)
    revisada_por = models.ForeignKey(
        'auth.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'alerta de cliente inactivo'
        verbose_name_plural = 'alertas de clientes inactivos'
        ordering = ['-created_at']
        indexes = [
            # Consulta más común: alertas pendientes (para el badge y el
            # anti-spam de la tarea diaria).
            models.Index(fields=['cliente', 'revisada']),
            models.Index(fields=['revisada']),
        ]

    def __str__(self):
        estado = 'revisada' if self.revisada else 'pendiente'
        return f'{self.cliente.nombre_completo()} inactivo {self.dias_inactivo}d ({estado})'


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
