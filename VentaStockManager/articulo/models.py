
import random
import string
import uuid
from datetime import timedelta
from django.db import models
import random
from django.utils import timezone
from django.utils.html import format_html


# ---------------------------------------------------------------------------
# Rubros — agrupador de Categorías
# ---------------------------------------------------------------------------
class Rubro(models.Model):
    """
    Nivel superior a Categoría. Un Rubro contiene N categorías.

    Ejemplo (negocio real "Golosinas Insa"):
      - Rubro "Golosinas" → Categorías [Chupetines, Alfajores,
        Masticables, Gomitas, Pastillas, Chocolates, ...]
      - Rubro "Bebidas" → Categorías [Gaseosas, Aguas, Jugos, Cervezas, ...]
      - Rubro "Almacén" → Categorías [Condimentos, Fideos, ...]

    Caso de uso principal: al armar una Lista de Precios el operador
    elegía categoría por categoría (chupetines, después alfajores,
    etc.). Con Rubros, elige UN rubro ("Golosinas") y se cargan todos
    los artículos cuya categoría pertenezca al rubro.

    El Rubro es OPCIONAL en Categoria — una categoría puede no tener
    rubro asignado, en cuyo caso no aparece en ningún filtro de rubro.
    """

    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.TextField(blank=True, default='')
    # Color hex para distinguir visualmente en el selector del editor.
    color = models.CharField(
        max_length=7,
        default='#9CA3AF',
        help_text='Color hex (ej. #FF5733) para mostrar el rubro en el selector.',
    )
    # `orden` permite controlar manualmente en qué orden aparecen los
    # rubros en el selector. Menor número = primero. Default 0 → orden
    # alfabético entre los empatados.
    orden = models.PositiveIntegerField(
        default=0,
        help_text='Para ordenar en el selector. Menor = aparece primero.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('orden', 'nombre')
        verbose_name = 'rubro'
        verbose_name_plural = 'rubros'

    def __str__(self):
        return self.nombre


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

    Una Categoría puede (opcionalmente) pertenecer a un `Rubro`.
    El Rubro es el nivel superior (Golosinas, Bebidas, etc.) — sirve
    para que el operador agrupe varias categorías al armar listas
    de precios.
    """

    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.TextField(blank=True, default='')
    # Color hex (con `#`) para badges en el admin. Default neutro.
    color = models.CharField(
        max_length=7,
        default='#607d8b',
        help_text='Color hex (ej. #2196f3) para mostrar la categoría en badges.',
    )
    # FK opcional al rubro. SET_NULL para que borrar un rubro no
    # arrastre las categorías (que es lo que haría CASCADE).
    rubro = models.ForeignKey(
        'Rubro',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='categorias',
        help_text='Rubro al que pertenece (Golosinas, Bebidas, Almacén, etc.). Opcional.',
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
            'Magnitud del ajuste porcentual aplicado a TODA la lista, '
            'siempre positiva (0-100). Si querés AUMENTO en vez de '
            'descuento, cambiá `tipo_ajuste`. El campo no se renombró '
            'para no romper datos / APIs viejas.'
        ),
    )
    # Cómo se interpreta `descuento_porcentaje`: como descuento (-pct)
    # o como aumento (+pct). Default 'descuento' para compatibilidad
    # con todas las listas que ya existen en DB. Ver migración 0008.
    TIPO_AJUSTE_CHOICES = [
        ('descuento', 'Descuento'),
        ('aumento', 'Aumento'),
    ]
    tipo_ajuste = models.CharField(
        max_length=10,
        choices=TIPO_AJUSTE_CHOICES,
        default='descuento',
        help_text=(
            'Cómo se interpreta el % global: descuento (resta) o '
            'aumento (suma). El número en sí siempre es positivo (0–100).'
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

    # Link público compartible. Cuando `share_token` es NULL, la lista
    # NO tiene link activo (estado por defecto). Cuando se comparte,
    # se genera un UUID4 + una fecha de vencimiento opcional. La
    # combinación token + chequeo de expiración la hace la vista
    # pública — no usamos un BooleanField "compartida" porque el
    # token-mismo es la prueba de existencia y permite revocar
    # simplemente seteándolo a NULL (sin tocar otras filas).
    share_token = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        help_text=(
            'UUID que se usa en la URL pública. NULL = link no '
            'compartido o revocado.'
        ),
    )
    share_expira_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            'Fecha de expiración del link público. NULL = no expira '
            '(no recomendado; el flujo normal usa el default de '
            'ConfiguracionGeneral.lista_precios_link_dias).'
        ),
    )

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

    @property
    def link_activo(self) -> bool:
        """
        ¿La lista tiene un link público utilizable ahora?

        Activo = hay token Y (no hay expiración O todavía no expiró).
        Esto es la fuente de verdad usada por la vista pública para
        decidir si renderizar o devolver 404 — y por el front (vía
        la API JSON) para mostrar el botón "Desactivar" vs el botón
        "Compartir".
        """
        if not self.share_token:
            return False
        if self.share_expira_at is None:
            return True
        return self.share_expira_at > timezone.now()

    def compartir(self, dias: int | None = None) -> dict:
        """
        Activa (o renueva) el link público de la lista.

        - Genera un UUID4 nuevo (si ya había uno, lo PISA — pensar
          esto como "revocar y regenerar": el link anterior queda
          inválido inmediatamente).
        - Si `dias` es None, lee `ConfiguracionGeneral.lista_precios_link_dias`.
          Si el caller pasa 0 o un valor explícito, se respeta tal cual.
        - Persiste los cambios con `save(update_fields=...)` para no
          tocar `updated_at` con un save() completo (la lista en sí
          no cambió — solo el link).

        Devuelve el dict `{'share_token': UUID, 'share_expira_at': dt}`
        para que el caller arme el response sin re-leer del modelo.
        """
        # Import perezoso para evitar ciclos (configuracion importa
        # apps de Django muy temprano).
        from configuracion.models import get_config

        if dias is None:
            dias = get_config().lista_precios_link_dias

        self.share_token = uuid.uuid4()
        if dias and dias > 0:
            self.share_expira_at = timezone.now() + timedelta(days=dias)
        else:
            # `dias=0` explícito = "no expira". Pensado para el corner
            # case en que Osvaldo quiera un link permanente para un
            # cliente VIP (no recomendado, pero no lo prohibimos).
            self.share_expira_at = None
        self.save(update_fields=['share_token', 'share_expira_at', 'updated_at'])
        return {
            'share_token': self.share_token,
            'share_expira_at': self.share_expira_at,
        }

    def desactivar_link(self) -> None:
        """
        Revoca el link público. Si no había link, no hace nada
        (idempotente — el caller no necesita chequear antes).
        """
        if not self.share_token and not self.share_expira_at:
            return
        self.share_token = None
        self.share_expira_at = None
        self.save(update_fields=['share_token', 'share_expira_at', 'updated_at'])


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


class DifusionListaPreciosEnvio(models.Model):
    """
    Un envío individual de una lista de precios a un cliente por WhatsApp.

    Se crean en bulk cuando el operador aprieta "Enviar a los N
    seleccionados" en la pantalla de difundir. La task de django-q2
    los procesa uno por uno con delay (rate limit) para no levantar
    sospechas en WhatsApp.

    El `modo` se SNAPSHOTEA al crear el envío (no se lee de
    Cliente.formato_preferido_lista_precios en runtime). Razón: si el
    cliente cambia su preferencia entre que se encola y se procesa,
    queremos respetar la decisión tomada al difundir.
    """

    MODO_LINK = 'link'
    MODO_PDF = 'pdf'
    MODO_AMBOS = 'ambos'
    MODO_TEXTO = 'texto'
    MODO_CHOICES = [
        (MODO_TEXTO, 'Solo texto'),
        (MODO_LINK, 'Solo link'),
        (MODO_PDF, 'Solo PDF'),
        (MODO_AMBOS, 'PDF + link'),
    ]

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

    lista = models.ForeignKey(
        'ListaPrecios',
        related_name='envios_difusion',
        on_delete=models.CASCADE,
    )
    cliente = models.ForeignKey(
        'cliente.Cliente',
        related_name='envios_difusion_lista',
        on_delete=models.PROTECT,
    )
    modo = models.CharField(max_length=10, choices=MODO_CHOICES)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDIENTE,
    )
    # Snapshot del número usado al momento de encolar — útil si el
    # cliente cambia su whatsapp_number después de que se mandó.
    telefono_usado = models.CharField(max_length=20, blank=True, default='')
    error_msg = models.TextField(blank=True, default='')
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    creado_por = models.ForeignKey(
        'auth.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'envío de difusión de lista'
        verbose_name_plural = 'envíos de difusión de lista'
        indexes = [
            # Worker query: WHERE status=pendiente ORDER BY created_at
            models.Index(fields=['status', 'created_at']),
            # Reporte por lista: WHERE lista_id=X
            models.Index(fields=['lista', '-created_at']),
        ]

    def __str__(self):
        return f'{self.lista.nombre} → {self.cliente.nombre_completo()} [{self.status}]'


class SolicitudListaCliente(models.Model):
    """
    Cliente pidió la lista por WhatsApp pero NO tiene una asignada.

    Se crea desde `wa_campania.auto_responder` cuando detecta el caso.
    El operador la ve como notificación en el header del admin (badge
    rojo con count) y puede:
      - Ir directo al editor de lista con el cliente preseleccionado
        para armarle una rápido.
      - Marcarla como "resuelta" (después de armar la lista o si decidió
        ignorarla — ej. ex-cliente que insiste).

    NO duplicamos: si el cliente vuelve a pedir mientras hay una
    solicitud pendiente, no creamos otra (sería ruido visual). Si la
    primera ya está resuelta y vuelve a pedir, sí creamos nueva.
    """

    cliente = models.ForeignKey(
        'cliente.Cliente',
        related_name='solicitudes_lista',
        on_delete=models.CASCADE,
    )
    # Texto crudo que mandó el cliente. Sirve para entender si pidió
    # "lista" genérica o algo más específico ("lista de bebidas") que
    # el operador pueda usar al armarle la lista.
    mensaje_original = models.TextField(blank=True, default='')
    resuelta = models.BooleanField(
        default=False,
        help_text='Marcar cuando la lista esté armada o se decida ignorar.',
    )
    resuelta_at = models.DateTimeField(null=True, blank=True)
    notas = models.TextField(
        blank=True,
        default='',
        help_text='Opcional: por qué se resolvió de tal manera, qué lista se le armó, etc.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'solicitud de lista'
        verbose_name_plural = 'solicitudes de lista'
        indexes = [
            # Query del badge: COUNT WHERE resuelta=False. Index acelera.
            models.Index(fields=['resuelta', '-created_at']),
            # Para chequeo de dedupe (¿ya hay una pendiente para este cliente?).
            models.Index(fields=['cliente', 'resuelta']),
        ]

    def __str__(self):
        estado = 'pendiente' if not self.resuelta else 'resuelta'
        return f'{self.cliente.nombre_completo()} pidió lista [{estado}]'


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
    # `codigo` es el identificador "humano" del artículo (etiqueta,
    # comprobante, factura, planilla). Lo deja libre el operador pero
    # si lo omite, `save()` autogenera uno único (iniciales + 4 dígitos
    # con retry en colisión). Por eso `blank=True`: el form admin / la
    # grilla no lo exigen, save() lo completa.
    #
    # NO unique=True a nivel DB: el dump legacy de Sheets tiene
    # duplicados (mismo código en filas distintas) y agregar el
    # constraint reventaría la migración. La unicidad se garantiza
    # SOLO para los códigos auto-generados — los que carga el operador
    # a mano pueden chocar y lo tomamos como "asunto del operador".
    codigo = models.CharField(max_length=255, blank=True)
    codigo_interno = models.CharField(max_length=50, blank=True, null=True)
    nombre = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True, null=True)
    stock = models.PositiveIntegerField()
    # Umbral de "stock bajo": cuando `stock` cae al/debajo de este número
    # después de una venta, se crea una AlertaStock(tipo='reponer') para
    # que la administración sepa que hay que pedir reposición.
    # Default 5: razonable para artículos chicos rotativos. El operador
    # puede ajustarlo por artículo desde el admin o desde la grilla.
    stock_minimo = models.PositiveIntegerField(
        default=5,
        help_text='Cuando el stock cae a este número o menos, se genera '
                  'una alerta "Reponer" para la administración.',
    )
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

    def _generar_codigo_unico(self) -> str:
        """
        Devuelve un código nuevo que NO existe en la tabla para esta
        clase. Formato: ``<INICIALES>-<4 dígitos>`` (ej. "COCA-1234").

        Usa hasta 20 reintentos con random 4-dígitos. Espacio de
        candidatos por prefijo: 10.000. Para que las colisiones sean
        problema realmente, harían falta ~miles de artículos con las
        mismas iniciales — irrealista para este negocio.

        Fallback: si los 20 reintentos fallan (extremadamente improbable),
        usamos timestamp para garantizar unicidad sin tirar excepción.
        """
        nombre = (self.nombre or '').strip()
        if nombre:
            # Iniciales de cada palabra, hasta 4 chars, en mayúsculas.
            iniciales = ''.join(w[0] for w in nombre.split())[:4].upper()
        else:
            iniciales = 'ART'
        if not iniciales:
            iniciales = 'ART'

        Cls = type(self)
        for _ in range(20):
            random_part = ''.join(random.choices(string.digits, k=4))
            candidato = f'{iniciales}-{random_part}'
            # Excluímos el propio pk para no chocar con uno mismo en
            # caso de un edge case (update donde codigo se vacía).
            qs = Cls.objects.filter(codigo=candidato)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if not qs.exists():
                return candidato

        # Fallback paranoia: timestamp en lugar de random. Garantía
        # de no colisionar consigo mismo en un sub-segundo razonable.
        import time
        return f'{iniciales}-{int(time.time()) % 1_000_000}'

    def save(self, *args, **kwargs):
        # ---- Auto-generación de codigo (público, único) ----
        # El operador puede crear un artículo sin código (form admin o
        # grilla). Acá lo completamos con uno único auto-generado. Solo
        # si está vacío — si trae uno cargado a mano, lo respetamos.
        if not (self.codigo or '').strip():
            self.codigo = self._generar_codigo_unico()

        # ---- Auto-generación de codigo_interno (legacy) ----
        if not self.codigo_interno:
            # Obtener las iniciales del nombre del artículo
            iniciales = ''.join(word[0] for word in self.nombre.split())
            # Generar un número aleatorio de 4 dígitos
            random_number = ''.join(random.choices(string.digits, k=4))
            # Combinar las iniciales y el número aleatorio
            self.codigo_interno = iniciales.upper() + random_number

        # ---- Detectar cambio de precio_minorista ----
        # Cuando sube el precio del artículo (típicamente por inflación),
        # los PrecioCliente acordados sobre el precio VIEJO quedan
        # desactualizados — el cliente termina con un descuento implícito
        # mucho más grande que el original. Para evitar precios stale,
        # cuando detectamos un cambio en precio_minorista borramos todos
        # los PrecioCliente apuntando a este artículo. Osvaldo (o quien
        # haya hecho el acuerdo) tiene que volver a setearlos manualmente
        # — esa fricción es deseada: obliga a revisar si el acuerdo sigue
        # vigente con el precio nuevo.
        #
        # update_fields acotado y precio_minorista NO está en la lista =>
        # save() no toca el precio, no hace falta chequear.
        update_fields = kwargs.get('update_fields')
        precio_cambio = False
        if self.pk and (update_fields is None or 'precio_minorista' in update_fields):
            try:
                anterior = type(self).objects.only('precio_minorista').get(pk=self.pk)
                if anterior.precio_minorista != self.precio_minorista:
                    precio_cambio = True
            except type(self).DoesNotExist:
                # Race condition rarísima: la fila se borró entre que
                # tenemos self.pk y el SELECT. No es crítico: tratamos
                # como "no había antes" → no borramos pactados (no hay
                # ninguno que apuntar a un Articulo recién borrado).
                pass

        # ¿Cambió el stock en este save? Lo evaluamos para auto-resolver
        # alertas de reposición DESPUÉS del super().save() (cuando el
        # stock nuevo ya está persistido).
        stock_tocado = (update_fields is None or 'stock' in update_fields)

        super().save(*args, **kwargs)

        # ---- Auto-resolver alertas de stock cuando se repone ----
        # Si el stock quedó por ARRIBA del umbral (stock_minimo), las
        # AlertaStock pendientes (sin revisar) de este artículo ya no
        # aplican — el operador repuso. Las marcamos como revisadas
        # automáticamente para que el badge "⚠ N alertas" baje solo.
        #
        # Umbral: si stock_minimo > 0, usamos ese; sino, basta con que
        # haya stock (> 0). Solo corremos si el stock se tocó en este
        # save (evita queries innecesarias en saves de precio/nombre).
        if self.pk and stock_tocado:
            umbral = self.stock_minimo or 0
            stock_ok = (self.stock or 0) > umbral if umbral > 0 else (self.stock or 0) > 0
            if stock_ok:
                from venta.models import AlertaStock
                n = (
                    AlertaStock.objects
                    .filter(articulo_id=self.pk, revisada=False)
                    .update(revisada=True, revisada_at=timezone.now())
                )
                if n:
                    print(
                        f'[ARTICULO {self.pk}] stock repuesto a {self.stock} '
                        f'(umbral {umbral}) → {n} alerta(s) auto-resuelta(s).'
                    )

        if precio_cambio:
            # Borrar los PrecioCliente del artículo en una sola query.
            # Importamos local para evitar circular (cliente → articulo).
            from cliente.models import PrecioCliente
            borrados, _ = PrecioCliente.objects.filter(articulo_id=self.pk).delete()
            # auditlog ya logea el delete por cada PrecioCliente; no
            # necesitamos extra logging acá.
            if borrados:
                # print + tag para que aparezca en los logs de Render.
                # Es info útil para el operador si se da cuenta tarde
                # de que perdió un acuerdo (puede grep-ear en logs).
                print(
                    f'[ARTICULO {self.pk}] precio_minorista cambió → '
                    f'{borrados} PrecioCliente borrados (acuerdos stale).'
                )

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
            
  