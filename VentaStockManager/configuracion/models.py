"""
Configuración operativa runtime.

Patrón "singleton model": un único registro en la tabla que centraliza
los parámetros que el admin necesita poder cambiar SIN modificar
variables de entorno ni reiniciar el server (retención de ventas, y
en el futuro probablemente más).

Diseño:
  - Helper `get_config()` que devuelve la instancia, creándola con
    defaults si no existe. Garantiza que cualquier código pueda
    hacer `get_config().ventas_retencion_meses` sin chequear nada.
  - El admin (ver `configuracion/admin.py`) bloquea `add` y `delete`,
    así nadie ensucia con múltiples filas o se queda sin config.
  - Cambios en este modelo quedan auditados por django-auditlog
    (registrado en `apps.py`), así sabemos quién bajó la retención
    a 6 meses cuando aparezca un cliente quejándose.
"""

from __future__ import annotations

from django.db import models


class ConfiguracionGeneral(models.Model):
    """
    Singleton: solo debe haber UNA fila en esta tabla. El admin
    enforce esto con has_add_permission, pero defensivamente también
    forzamos pk=1 en el save().
    """

    ventas_retencion_meses = models.PositiveIntegerField(
        default=18,
        help_text=(
            'Cantidad de meses a partir de los cuales una venta se '
            'archiva automáticamente. NO se borra: solo queda oculta '
            'del listado normal del admin (visible con filtro '
            '"Archivadas"). El cron `archivar_ventas_antiguas` usa '
            'este valor.'
        ),
    )
    # Cuántos días dura un link público de lista de precios desde que
    # se genera. Pensado para que Osvaldo pueda achicarlo si nota que
    # los links están "filtrándose" entre clientes (default 7 = una
    # semana, suficiente para que el cliente compare y vuelva a
    # comprar sin abrir una ventana eterna de leak).
    lista_precios_link_dias = models.PositiveIntegerField(
        default=7,
        help_text=(
            'Cantidad de días que dura un link público de lista de '
            'precios desde que se comparte. Se aplica al momento de '
            'apretar "Compartir link público"; cambiar este valor NO '
            'modifica retroactivamente los links ya emitidos.'
        ),
    )
    # ------------------------------------------------------------------
    # Integración con Google Sheets — toggles operativos
    # ------------------------------------------------------------------
    # Antes esto vivía SOLO en settings.SHEETS_SYNC_ENABLED (env var).
    # Lo subimos al singleton para que Osvaldo pueda prender/apagar la
    # sincronización desde el admin sin necesidad de redeploy. La env
    # var sigue existiendo como kill-switch defensivo: si está en False
    # explícita, NUNCA sincroniza, ignorando el singleton.
    sheets_sync_habilitado = models.BooleanField(
        default=False,
        help_text=(
            'Master switch de la integración con Google Sheets. Si está '
            'desactivado, ni el sync de pull (Sheets → DB) ni el de '
            'delete bidireccional (DB → Sheets) funcionan. Útil para '
            'apagar TODO durante una migración o cuando Sheets deja de '
            'ser fuente de verdad.'
        ),
    )
    sheets_delete_sync_habilitado = models.BooleanField(
        default=False,
        help_text=(
            'Sincroniza el BORRADO de un artículo desde la DB hacia el '
            'Sheet (vacía la fila en la planilla). Requiere que el '
            '"master switch" de arriba también esté en True. Necesita '
            'que el service-account sea Editor del Sheet (no Viewer).'
        ),
    )

    # ------------------------------------------------------------------
    # Difusión de lista de precios — preferencias globales
    # ------------------------------------------------------------------
    # Cuando el operador difunde una lista de precios por WhatsApp,
    # tiene tres modos posibles: solo link, solo PDF, o ambos. El modo
    # se resuelve en cascada de tres niveles:
    #   1. Override del envío particular (UI: selector arriba de la lista
    #      de clientes en la pantalla de difundir).
    #   2. Override del cliente (Cliente.formato_preferido_lista_precios)
    #      — si está seteado, gana sobre el default global.
    #   3. Default global (ESTE campo) — si nadie más eligió, se usa.
    FORMATO_LISTA_LINK = 'link'
    FORMATO_LISTA_PDF = 'pdf'
    FORMATO_LISTA_AMBOS = 'ambos'
    FORMATO_LISTA_TEXTO = 'texto'
    FORMATO_LISTA_CHOICES = [
        (FORMATO_LISTA_TEXTO, 'Solo texto (lista pegada en el mensaje)'),
        (FORMATO_LISTA_LINK, 'Solo link público (más fresco)'),
        (FORMATO_LISTA_PDF, 'Solo PDF adjunto (queda guardado)'),
        (FORMATO_LISTA_AMBOS, 'Ambos: PDF + link debajo'),
    ]
    formato_default_lista_precios = models.CharField(
        max_length=10,
        choices=FORMATO_LISTA_CHOICES,
        default=FORMATO_LISTA_LINK,
        help_text=(
            'Cómo se mandan las listas de precios por defecto en una '
            'difusión. Recomendado "Solo link" para que siempre llegue '
            'actualizado. Cada cliente puede tener su preferencia propia, '
            'y al difundir el operador puede pisar este default.'
        ),
    )

    # ------------------------------------------------------------------
    # Recordatorios automáticos de saldo deudor
    # ------------------------------------------------------------------
    # Schedule django-q2 que escanea clientes con saldo negativo (debe
    # plata) + sin actividad reciente, y les manda un WhatsApp recordando
    # el pendiente. Cumple los principios:
    #   - Opt-in obligatorio: solo a clientes con `puede_recibir_whatsapp=True`.
    #   - Rate limit del wa-bot global.
    #   - Frecuencia mínima entre recordatorios al mismo cliente (no spamear).
    #   - Auditable: cada envío queda registrado en `RecordatorioSaldoEnviado`.
    recordatorios_saldo_habilitado = models.BooleanField(
        default=False,
        help_text=(
            'Si está prendido, una vez por semana (configurable abajo) se '
            'mandan WhatsApps a clientes con saldo deudor sin compras '
            'recientes. Apagalo cuando no quieras que se manden — el '
            'schedule sigue corriendo pero hace NO-OP.'
        ),
    )
    recordatorios_saldo_dias_inactividad = models.PositiveIntegerField(
        default=30,
        help_text=(
            'Días sin compras para considerar "inactivo" y candidato a '
            'recordatorio. Default 30. Si bajás esto, le va a llegar a '
            'más gente (incluyendo clientes que están al día y simplemente '
            'no compraron esta semana).'
        ),
    )
    recordatorios_saldo_monto_minimo = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=(
            'Solo recordar saldos deudores mayores a este monto. 0 = '
            'recordar a todos los deudores. Útil para no molestar por '
            'deudas menores ($100, $200) que no compensan el ruido.'
        ),
    )
    recordatorios_saldo_template = models.TextField(
        default=(
            'Hola {{nombre}}, te recuerdo que tenés un saldo pendiente de '
            '${{saldo_abs}} desde hace {{dias}} días. Cualquier cosa avisame.'
        ),
        help_text=(
            'Template del mensaje. Variables: {{nombre}}, {{saldo_abs}} '
            '(monto positivo de la deuda), {{dias}} (días desde última compra).'
        ),
    )
    recordatorios_saldo_frecuencia_dias = models.PositiveIntegerField(
        default=7,
        help_text=(
            'Días mínimos entre recordatorios al mismo cliente. Default 7 '
            '(una vez por semana). Si el cron corre todos los días, este '
            'campo evita spamear al mismo cliente todos los días.'
        ),
    )
    recordatorios_saldo_ultima_corrida_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text='Cuándo corrió por última vez la task (read-only).',
    )

    # ------------------------------------------------------------------
    # Auto-responder de mensajes entrantes al bot
    # ------------------------------------------------------------------
    # Si está prendido, el bot responde solo cuando un cliente le
    # manda "lista" / "precios" / "saldo". Solo a clientes registrados
    # (whatsapp_number matchea). Otros mensajes se ignoran (no
    # respondemos a números desconocidos, no exponemos info).
    auto_responder_habilitado = models.BooleanField(
        default=False,
        help_text=(
            'Si está prendido, cuando un cliente le manda al bot "lista" '
            'o "saldo" le respondemos automáticamente. Ahorra responder '
            'lo mismo todos los días. NO responde a números desconocidos.'
        ),
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'configuración general'
        verbose_name_plural = 'configuración general'

    def __str__(self):
        return (
            f'Configuración general (retención: {self.ventas_retencion_meses} '
            f'meses · link listas: {self.lista_precios_link_dias} días)'
        )

    def save(self, *args, **kwargs):
        # Singleton: pk siempre 1. Si alguien intenta crear una
        # segunda fila desde shell, esto la convierte en "actualizar
        # la única". El admin además bloquea add desde la UI.
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Defensa: nunca permitir borrar la configuración. Si alguien
        # lo intenta desde shell, no rompemos nada — el silencio es
        # menos peligroso que dejar el sistema sin config.
        return

    def resolver_formato_lista(self, cliente=None, override: str = '') -> str:
        """
        Devuelve el modo de envío de lista de precios para un cliente,
        aplicando la cascada de tres niveles:

          override (envío particular)
            > cliente.formato_preferido_lista_precios (si seteado)
              > self.formato_default_lista_precios (global)

        Valores válidos: 'link', 'pdf', 'ambos'.

        - `cliente` puede ser None (ej. mensaje de prueba sin cliente).
        - `override` puede venir vacío ('') = "no hay override, usar
          siguiente nivel".

        Nunca devuelve '' o algo raro — siempre cae en el default global
        que es un valor válido.
        """
        validos = ('link', 'pdf', 'ambos', 'texto')
        if override in validos:
            return override
        if cliente is not None:
            pref = getattr(cliente, 'formato_preferido_lista_precios', '') or ''
            if pref in validos:
                return pref
        return self.formato_default_lista_precios or 'link'


def get_config() -> ConfiguracionGeneral:
    """
    Devuelve la única instancia de ConfiguracionGeneral, creándola
    con defaults si no existe. Pensado para usarse desde cualquier
    parte del código (commands, tasks, views) sin tener que manejar
    el caso "qué pasa si todavía no se cargó la config".
    """
    obj, _ = ConfiguracionGeneral.objects.get_or_create(pk=1)
    return obj
