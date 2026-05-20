"""
Modelos de campañas de WhatsApp.

Una `Campania` representa una promoción/comunicación que el admin quiere
mandar a un grupo de clientes (todos, los que compraron hace poco, los
que deben plata, etc.). Cada cliente que reciba la campaña genera un
`EnvioWhatsapp` con su propio status (pendiente/enviado/fallido), así
podemos auditar quién recibió qué.

Por qué dos modelos en vez de un campo `clientes` M2M en Campania:
  - Necesitamos persistir el ESTADO de cada envío individual (¿llegó?
    ¿falló?). Una M2M no tiene status por par.
  - Necesitamos persistir el TELÉFONO USADO al momento del envío
    (snapshot), porque el cliente puede cambiar de número después y
    los logs tienen que reflejar lo que mandamos.
  - Necesitamos persistir cuándo se envió cada uno (sent_at), para
    poder responder "¿esta promo se mandó a fulano el martes?".
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Campania(models.Model):
    """
    Una campaña de WhatsApp: un mensaje + opcional adjunto + filtros
    de audiencia + estado global. La explosión a `EnvioWhatsapp` se
    hace cuando el admin aprieta "Enviar".
    """

    ESTADO_BORRADOR = 'borrador'
    ESTADO_ENVIANDO = 'enviando'
    ESTADO_FINALIZADA = 'finalizada'
    ESTADO_CANCELADA = 'cancelada'
    ESTADO_CHOICES = [
        (ESTADO_BORRADOR, 'Borrador'),
        (ESTADO_ENVIANDO, 'Enviando'),
        (ESTADO_FINALIZADA, 'Finalizada'),
        (ESTADO_CANCELADA, 'Cancelada'),
    ]

    # ---- Filtros de audiencia ----
    # Persisto los filtros como un JSON para no tener mil columnas y
    # para poder agregar nuevos filtros sin migración. El parsing vive
    # en `audiencia.resolver_clientes()`.
    #
    # Forma del JSON:
    #   {
    #     "todos": false,
    #     "compraron_ultimos_dias": 30,  # null = sin filtro
    #     "con_saldo_a_favor": false,
    #     "con_saldo_deudor": false,
    #     "solo_con_whatsapp_valido": true,  # siempre true por default
    #   }
    AUDIENCIA_DEFAULT: dict = {
        'todos': False,
        'compraron_ultimos_dias': None,
        'con_saldo_a_favor': False,
        'con_saldo_deudor': False,
        'solo_con_whatsapp_valido': True,
    }

    nombre = models.CharField(max_length=120)
    # Template con variables {{nombre}}, {{apellido}}, {{saldo}}.
    # Se renderiza por cliente al enviar — NO se renderiza acá.
    mensaje = models.TextField(
        help_text='Template del mensaje. Variables: {{nombre}}, {{apellido}}, {{saldo}}',
    )
    adjunto = models.FileField(
        upload_to='wa_campania/adjuntos/',
        null=True,
        blank=True,
        help_text='Imagen (JPG/PNG) o PDF. Opcional.',
    )
    audiencia_filtro = models.JSONField(default=dict, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default=ESTADO_BORRADOR,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    enviada_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'campaña de WhatsApp'
        verbose_name_plural = 'campañas de WhatsApp'

    def __str__(self):
        return f'{self.nombre} [{self.get_estado_display()}]'

    @property
    def total_envios(self) -> int:
        return self.envios.count()

    @property
    def total_enviados_ok(self) -> int:
        return self.envios.filter(status=EnvioWhatsapp.STATUS_ENVIADO).count()

    @property
    def total_fallidos(self) -> int:
        return self.envios.filter(status=EnvioWhatsapp.STATUS_FALLIDO).count()


class EnvioWhatsapp(models.Model):
    """
    Un mensaje específico a un cliente específico dentro de una
    campaña. La tabla puede crecer rápido (1 fila por destinatario por
    campaña) — indexamos por campaña para el reporte y por status
    para el worker.
    """

    STATUS_PENDIENTE = 'pendiente'
    STATUS_ENVIANDO = 'enviando'
    STATUS_ENVIADO = 'enviado'
    STATUS_FALLIDO = 'fallido'
    STATUS_CHOICES = [
        (STATUS_PENDIENTE, 'Pendiente'),
        (STATUS_ENVIANDO, 'Enviando'),
        (STATUS_ENVIADO, 'Enviado'),
        (STATUS_FALLIDO, 'Fallido'),
    ]

    campania = models.ForeignKey(
        Campania,
        related_name='envios',
        on_delete=models.CASCADE,
    )
    cliente = models.ForeignKey(
        'cliente.Cliente',
        related_name='envios_whatsapp',
        # PROTECT para no perder histórico si alguien borra un cliente.
        on_delete=models.PROTECT,
    )
    # Snapshot del número que usamos. Si el cliente cambia el suyo
    # después, queremos saber a qué número fuimos cuando se mandó.
    telefono_usado = models.CharField(max_length=20, default='')
    # Renderizado del mensaje al momento del envío (después de
    # sustituir las variables). Snapshot por la misma razón.
    mensaje_renderizado = models.TextField(blank=True, default='')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDIENTE,
    )
    error_msg = models.TextField(blank=True, default='')
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'envío de WhatsApp'
        verbose_name_plural = 'envíos de WhatsApp'
        indexes = [
            # El worker scane periódicamente envíos pendientes.
            models.Index(fields=['status', 'created_at']),
            # Reportes por campaña.
            models.Index(fields=['campania', 'status']),
        ]
        constraints = [
            # Un cliente recibe a lo sumo una vez la misma campaña.
            # Si en el futuro queremos reenvíos manuales, lo hacemos
            # con una campaña nueva (no duplicando en la misma).
            models.UniqueConstraint(
                fields=['campania', 'cliente'],
                name='un_envio_por_cliente_por_campania',
            ),
        ]

    def __str__(self):
        return f'{self.cliente} ← {self.campania.nombre} ({self.get_status_display()})'
