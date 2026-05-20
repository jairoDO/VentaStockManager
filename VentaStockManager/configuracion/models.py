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
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'configuración general'
        verbose_name_plural = 'configuración general'

    def __str__(self):
        return f'Configuración general (retención: {self.ventas_retencion_meses} meses)'

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


def get_config() -> ConfiguracionGeneral:
    """
    Devuelve la única instancia de ConfiguracionGeneral, creándola
    con defaults si no existe. Pensado para usarse desde cualquier
    parte del código (commands, tasks, views) sin tener que manejar
    el caso "qué pasa si todavía no se cargó la config".
    """
    obj, _ = ConfiguracionGeneral.objects.get_or_create(pk=1)
    return obj
