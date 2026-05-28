from typing import Iterable
from django.db import models

# Create your models here.
from django.db import models
from cliente.models import Cliente
from articulo.models import Articulo
# from vendedor.models import Vendedor
from vendedor.models import Vendedor
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation


class Venta(models.Model):
    fecha_compra = models.DateField()
    fecha_entrega = models.DateField()
    cliente = models.ForeignKey(Cliente, related_name='ventas', on_delete=models.CASCADE)
    vendedor = models.ForeignKey(Vendedor, related_name='ventas', on_delete=models.CASCADE)
    # Descuento global persistido (no calculado). Si mañana cambia el
    # precio de un artículo, el descuento aplicado a esta venta sigue
    # siendo el que el vendedor cerró en el momento. `descuento_motivo`
    # es texto libre porque el negocio rechaza un enum cerrado: cada
    # vendedor justifica la rebaja como puede ("cliente fiel", "queja
    # por demora", "rotura previa", etc).
    descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text='Descuento porcentual aplicado al total general de la venta (0-100)',
    )
    descuento_motivo = models.CharField(max_length=255, blank=True, default='')
    # Soft archive: cuando una venta es vieja (default >18 meses), un
    # cron la marca acá. El admin filtra el queryset default para
    # ocultarlas — siguen en la DB pero fuera del flujo normal. NO
    # borramos data (ni el Pedido ni los ArticuloVenta) porque la
    # historia financiera tiene que poder reconstruirse.
    archivada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['fecha_compra']
        verbose_name = _("venta")
        verbose_name_plural = _("ventas")


    def __str__(self):
        return format_html(f"\nVenta del {self.fecha_compra} al cliente {self.cliente}")

    def save(self, *args, **kwargs) -> None:
        # Cada Venta tiene un Pedido 1:1. Antes este método creaba un
        # Pedido nuevo en CADA save() con id=self.id, lo cual en edits
        # pisaba el Pedido existente reseteando `pagado` y `estado` a
        # los defaults. Ahora solo creamos Pedido si todavía no existe;
        # los edits de Venta no tocan el Pedido asociado.
        es_nuevo = self.pk is None
        super().save(*args, **kwargs)
        if es_nuevo:
            Pedido.objects.get_or_create(venta=self, defaults={'id': self.id})
            # Auto-resolución de alertas de inactividad: si este cliente
            # tenía una alerta pendiente por "dejó de comprar", el hecho
            # de registrar una venta nueva la cierra automáticamente.
            self._resolver_alertas_inactividad()
        else:
            # Edit de una venta existente. Garantizamos que SIEMPRE tenga
            # su Pedido asociado: las ventas legacy importadas del dump de
            # PythonAnywhere pueden no tenerlo, y antes editarlas no lo
            # creaba (el get_or_create de arriba solo corría en el alta).
            # Sin Pedido, la venta no aparece en la bandeja de Pedidos ni
            # se le puede generar el PDF/comanda.
            #
            # Importante: NO usamos `defaults={'id': self.id}` acá. Forzar
            # ese id en datos legacy puede colisionar con un Pedido que ya
            # exista con ese id (las secuencias de venta y pedido pueden
            # haber divergido en el dump). Dejamos que Postgres asigne el
            # id — nada en el código exige que pedido.id == venta.id.
            if not Pedido.objects.filter(venta=self).exists():
                Pedido.objects.create(venta=self)

    def _resolver_alertas_inactividad(self) -> None:
        """
        Marca como revisadas todas las alertas de inactividad pendientes
        del cliente. Se llama al crear una venta nueva: si el cliente
        "volvió", la alerta ya no tiene sentido. Best-effort: cualquier
        error no debe tumbar la carga de la venta.
        """
        try:
            from cliente.models import AlertaClienteInactivo
            from django.utils import timezone

            AlertaClienteInactivo.objects.filter(
                cliente_id=self.cliente_id,
                revisada=False,
            ).update(revisada=True, revisada_at=timezone.now())
        except Exception:  # noqa: BLE001 — nunca romper el save de la venta
            pass
    
    @property
    def precio_total(self):
        if not self.ventas.exists():
            return 0
        return sum([articulo.get_precio_total() for articulo in self.ventas.all()])


    @precio_total.setter
    def precio_total(self, value):
        self._precio_total = value
        
        
    def generar_link(self):
        return format_html("<a href='/venta/{}/'>Ver venta</a>", self.id)
    
    def crear_fila_html_desde_venta(self): 
        return format_html("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td></td></tr>",
                           self.fecha_compra, self.cliente.nombre_completo(), self.pedido.estado, self.precio_total, self.generar_link())



class ArticuloVenta(models.Model):
    venta = models.ForeignKey(Venta, related_name='ventas', on_delete=models.CASCADE)
    # PROTECT: si alguien intenta borrar un Articulo que tiene
    # ventas históricas asociadas, Django lanza ProtectedError en
    # vez de borrar en cascada las líneas de venta (que es lo que
    # hacía antes y borraba historial irrecuperable). Para borrar
    # un artículo hay que primero archivar/migrar sus ventas.
    articulo = models.ForeignKey(
        Articulo,
        related_name='articulos_vendidos',
        on_delete=models.PROTECT,
    )
    cantidad = models.PositiveBigIntegerField(default=1)
    # `precio` legacy: CharField sucio del dump de PA. NO removemos
    # todavía porque hay 13.9k filas con datos en formatos mixtos.
    # Las escrituras nuevas siguen llenándolo por compatibilidad
    # con el resto del código (templates, PDFs, etc.).
    precio = models.CharField(max_length=255)
    # `precio_decimal`: campo nuevo (fase deuda técnica). Toda
    # lógica financiera futura (descuentos, saldos, listas de
    # precios) debe leer/escribir de acá. Se llena automáticamente
    # en el save() parseando el CharField legacy.
    precio_decimal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            'Versión normalizada del precio. Se llena automáticamente '
            'a partir del campo `precio` (CharField legacy). Usar este '
            'campo en cálculos financieros nuevos.'
        ),
    )
    # Descuento por línea. Independiente del descuento global de la
    # Venta — se aplican secuencialmente (primero el de línea, después
    # el global sobre el subtotal). Lo persistimos para no perder la
    # auditoría de qué se rebajó en qué ítem.
    descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text='Descuento porcentual aplicado a esta línea (0-100)',
    )

    def save(self, *args, **kwargs):
        # Ajuste de stock automático. Antes este save() descontaba la
        # cantidad SIEMPRE (incluido en updates), lo cual generaba
        # doble-descuento. Ahora calculamos el DELTA y lo aplicamos:
        #
        #   - create:  delta = cantidad (descuenta todo).
        #   - update:  delta = cantidad_nueva - cantidad_anterior.
        #              Si cantidad subió → descuenta más; si bajó →
        #              devuelve. Si quedó igual → no toca stock.
        #
        # Mantener esta invariante en el save() también deja la regla
        # consistente con el signal `pre_delete` que devuelve la
        # cantidad actual: total descontado = total devuelto.
        cantidad_anterior = 0
        if self.pk:
            prev = (
                type(self).objects
                .filter(pk=self.pk)
                .only('cantidad', 'articulo_id')
                .first()
            )
            if prev:
                cantidad_anterior = prev.cantidad or 0
                # Si cambió el artículo (raro), devolvemos stock al
                # viejo y descontamos al nuevo. La pantalla nueva
                # maneja este caso explícitamente, pero por las dudas
                # acá también.
                if prev.articulo_id and self.articulo_id and prev.articulo_id != self.articulo_id:
                    from articulo.models import Articulo
                    art_viejo = Articulo.objects.get(pk=prev.articulo_id)
                    art_viejo.stock = (art_viejo.stock or 0) + cantidad_anterior
                    art_viejo.save(update_fields=['stock'])
                    cantidad_anterior = 0  # ya devolvimos todo al viejo

        # Mantener `precio_decimal` sincronizado con el CharField
        # `precio`. Si el caller setea precio_decimal manualmente,
        # lo respetamos. Sino, parseamos del precio legacy.
        if self.precio_decimal is None and self.precio:
            # Import local para evitar ciclo de imports.
            from venta.utils import parse_precio
            self.precio_decimal = parse_precio(self.precio)

        if self.articulo:
            delta = (self.cantidad or 0) - cantidad_anterior
            if delta != 0:
                self.articulo.stock = max(0, (self.articulo.stock or 0) - delta)
                self.articulo.save(update_fields=['stock'])
                # Crear alerta "Reponer" si el stock cae al/debajo del
                # umbral mínimo del artículo Y la venta DESCUENTA (delta > 0).
                # Anti-spam: solo si NO hay ya una alerta "reponer" sin
                # revisar para este artículo. Si la hay, ya está en la
                # bandeja del operador — no inflar con duplicados.
                if delta > 0:
                    self._crear_alerta_reponer_si_corresponde()

        super().save(*args, **kwargs)

    def _crear_alerta_reponer_si_corresponde(self):
        """
        Crea AlertaStock(tipo='reponer') si:
          - el articulo tiene stock_minimo > 0 (si es 0, el operador
            desactivó la alerta para ese articulo)
          - el stock del articulo es <= stock_minimo
          - NO hay ya una alerta tipo='reponer' sin revisar para este articulo

        Se llama solo desde save() después de descontar stock.
        """
        art = self.articulo
        if (art.stock_minimo or 0) <= 0:
            return  # Stock mínimo en 0 → operador desactivó alertas para este art.
        if (art.stock or 0) > art.stock_minimo:
            return  # Stock sigue por arriba del umbral, nada que hacer.

        # Import local — AlertaStock está en el mismo módulo pero no
        # estaba importado en el scope de la función arriba todavía.
        existe = AlertaStock.objects.filter(
            articulo=art,
            tipo=AlertaStock.TIPO_REPONER,
            revisada=False,
        ).exists()
        if existe:
            return

        AlertaStock.objects.create(
            articulo=art,
            tipo=AlertaStock.TIPO_REPONER,
            venta=self.venta,
            cantidad_pedida=0,
            cantidad_faltante=0,
            stock_disponible_al_momento=art.stock or 0,
        )

    def get_precio_total(self):
        if self.articulo:
            return float(self.cantidad) * float(self.precio.replace("'", "").replace(",", ""))
        else:
            return 0.0
    @property
    def precio_minorista_2(self):
        return str(self.articulo.precio_minorista)

    @property
    def total(self):
        # Preferimos `precio_decimal` si está cargado (datos nuevos
        # o ya backfilleados). Fallback al CharField legacy con
        # parseo robusto para no romper la página por una fila
        # con dato sucio.
        if self.precio_decimal is not None:
            return Decimal(self.cantidad) * self.precio_decimal
        from venta.utils import parse_precio
        return Decimal(self.cantidad) * parse_precio(self.precio)
    def __str__(self):
                # return f"{self.cantidad} unidades de {self.articulo} en la venta {self.venta}"
        articulo_str = str(self.articulo) if self.articulo else "No article"
        venta_str = str(self.venta) if self.venta else "No sale"
        return f"{self.cantidad} unidades de {articulo_str} en la venta {venta_str}"
    

class AlertaStock(models.Model):
    """
    Registro persistente de cada vez que se vendió un artículo con
    stock insuficiente. El operador puede seguir trabajando — la
    venta no se rechaza — pero queda este rastro para que la
    administración detecte el desfasaje y lo corrija (reponer
    mercadería, ajustar stock manualmente, investigar el robo, lo
    que sea).

    Diseño:
      - `venta` SET_NULL: si la venta se borra después, la alerta
        sigue siendo útil como evidencia histórica.
      - `articulo` PROTECT: no perdemos contexto si alguien borra
        un artículo con alertas (no debería, está protegido contra
        cascade de ArticuloVenta también).
      - `revisada` + `revisada_at` + `revisada_por`: workflow simple
        de "vi esto y ya está atendido". La administración puede
        ordenar las alertas por "sin revisar primero".
      - `notas`: el revisor anota qué hizo ("ya entró mercadería",
        "ajusté stock manualmente +20", "preguntar a Juan").
    """

    # Dos tipos de alerta — comparten tabla porque tienen el mismo
    # workflow (revisar/marcar como atendida) y el operador las trata
    # como una sola bandeja de entrada en el admin.
    TIPO_INSUFICIENTE = 'insuficiente'
    TIPO_REPONER = 'reponer'
    TIPO_CHOICES = [
        (TIPO_INSUFICIENTE, 'Stock insuficiente al vender'),
        (TIPO_REPONER, 'Stock bajo umbral (reponer)'),
    ]

    tipo = models.CharField(
        max_length=20,
        choices=TIPO_CHOICES,
        default=TIPO_INSUFICIENTE,
        help_text='"insuficiente": se vendió más de lo que había. '
                  '"reponer": el stock cayó al/debajo del stock_minimo del articulo.',
    )

    venta = models.ForeignKey(
        'Venta',
        related_name='alertas_stock',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    articulo = models.ForeignKey(
        'articulo.Articulo',
        related_name='alertas_stock',
        on_delete=models.PROTECT,
    )
    # Para alertas tipo 'reponer', `cantidad_pedida` y `cantidad_faltante`
    # pueden ser 0 — no hubo faltante, solo el stock bajó del umbral.
    cantidad_pedida = models.PositiveIntegerField(
        default=0,
        help_text='Cuántas unidades pidió el operador en la venta. '
                  '0 para alertas tipo "reponer".',
    )
    stock_disponible_al_momento = models.IntegerField(
        help_text='Stock que había justo antes de la venta. Puede ser 0.',
    )
    cantidad_faltante = models.PositiveIntegerField(
        default=0,
        help_text='cantidad_pedida − stock_disponible_al_momento para "insuficiente". '
                  '0 para "reponer" (no hubo faltante, solo se cruzó el umbral).',
    )
    creado_por = models.ForeignKey(
        'auth.User',
        related_name='+',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    revisada = models.BooleanField(
        default=False,
        help_text='Marcar cuando alguien ya investigó y resolvió la alerta.',
    )
    revisada_at = models.DateTimeField(null=True, blank=True)
    revisada_por = models.ForeignKey(
        'auth.User',
        related_name='+',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    notas = models.TextField(
        blank=True,
        default='',
        help_text='Notas del revisor: qué se hizo, por qué pasó, etc.',
    )

    class Meta:
        ordering = ['revisada', '-created_at']
        verbose_name = 'alerta de stock'
        verbose_name_plural = 'alertas de stock'
        indexes = [
            # La consulta más común: contar/listar las sin revisar.
            models.Index(fields=['revisada', '-created_at']),
        ]

    def __str__(self):
        estado = 'revisada' if self.revisada else 'pendiente'
        return f'⚠ {self.articulo.nombre} (faltó {self.cantidad_faltante}) [{estado}]'


class Pedido(models.Model):
    PENDIENTE = 'Pendiente'
    ENTREGADO = 'Entregado'
    LISTO_PARA_RETIRAR = 'Listo para retirar'

    ESTADO_CHOICES = [
        (PENDIENTE, 'Pendiente'),
        (ENTREGADO, 'Entregado'),
        (LISTO_PARA_RETIRAR, 'Listo para retirar'),
    ]

    venta = models.OneToOneField(Venta, on_delete=models.CASCADE, related_name='pedido')
    pagado = models.BooleanField(default=False)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=PENDIENTE)

