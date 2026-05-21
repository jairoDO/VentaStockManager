"""
Widgets custom para el admin de `articulo`.

`ListaPalabrasWidget`
    Reemplazo del input crudo de JSONField para `ReglaCategoria.palabras_clave`.
    Antes el operador veía algo tipo `["alfajor","chupetin"]` y tenía
    que escribir JSON válido a mano (con comillas, comas, etc.) — error
    prone y nada amigable.

    Ahora renderiza una lista dinámica con un input por palabra y un
    botón "+ agregar palabra". El JSON se reconstruye con vanilla JS
    en un input hidden que es lo que Django termina recibiendo.

    No usa Alpine acá: el admin de material-admin no carga Alpine por
    default y agregarlo sumaría peso para una sola pantalla. Vanilla
    JS alcanza.
"""

from __future__ import annotations

import json

from django import forms
from django.utils.safestring import mark_safe


class ListaPalabrasWidget(forms.Widget):
    """
    Widget para campos JSONField cuyo contenido es una lista de strings.
    Renderiza una lista editable de inputs + "+ agregar palabra".

    Internamente persiste el valor como JSON en un input hidden cuyo
    name coincide con el del field — así Django no nota la diferencia
    con el widget default y JSONField parsea normalmente.
    """

    # No `template_name` para no acoplar a la engine de templates; el
    # HTML se arma en `render()`. Más código pero más portable.

    def format_value(self, value):
        """
        Convierte el value del field (Python) a una representación
        que el JS del template pueda leer al iniciar (JSON string).
        """
        if value in (None, '', []):
            return '[]'
        if isinstance(value, list):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            # Ya viene como JSON (caso de rebind tras error de validación).
            return value
        # Fallback defensivo.
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return '[]'

    def render(self, name, value, attrs=None, renderer=None):
        # Generamos un id único de container para evitar colisiones
        # cuando hay varias instancias en la misma página (típico en
        # inlines).
        attrs = attrs or {}
        widget_id = attrs.get('id') or f'id_{name}'
        container_id = f'{widget_id}_container'
        json_initial = self.format_value(value)

        # `data-name` se usa desde el script para encontrar el hidden
        # asociado. Lo hacemos por data-* en vez de id porque los
        # inlines de Django numeran los ids como `<prefix>-<idx>-name`
        # y el name viene con el mismo patrón — sería redundante.
        # Panel de preview: cuenta + breakdown + samples + "aplicar ahora".
        # El JS lo activa cuando hay ≥1 palabra cargada y muestra los
        # resultados del endpoint /articulos/api/reglas/preview/.
        html = f'''
        <div class="lista-palabras-widget" id="{container_id}" data-name="{name}">
          <input type="hidden" name="{name}" value='{self._escape_attr(json_initial)}' />
          <div class="lista-palabras-items" style="display:flex; flex-direction:column; gap:6px; margin-bottom:8px;"></div>
          <button type="button" class="lista-palabras-add"
                  style="padding:4px 10px; background:#f1f5f9; color:#334155;
                         border:1px solid #cbd5e1; border-radius:4px;
                         cursor:pointer; font-size:13px;">
            + agregar palabra
          </button>
          <p style="margin:6px 0 0; font-size:12px; color:#64748b;">
            Una palabra por línea. Match case-insensitive contra el nombre del artículo.
          </p>

          <div class="lista-palabras-preview" style="margin-top:12px; display:none;
               background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:10px;">
            <div class="lpw-preview-header" style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
              <span style="font-size:13px; color:#475569;">👁 Preview:</span>
              <strong class="lpw-total" style="font-size:18px; color:#0f172a;">0</strong>
              <span style="font-size:13px; color:#475569;">artículos matchean</span>
              <span class="lpw-loading" style="font-size:11px; color:#94a3b8; display:none;">(actualizando…)</span>
            </div>
            <div class="lpw-breakdown" style="margin-top:4px; font-size:12px; color:#64748b;">
              <span class="lpw-sin-cat" style="color:#0d9488;">— sin categoría</span> ·
              <span class="lpw-con-otra" style="color:#94a3b8;">— ya con otra</span>
            </div>
            <details class="lpw-samples-details" style="margin-top:8px; font-size:12px;">
              <summary style="cursor:pointer; color:#2563eb;">Ver muestra</summary>
              <ul class="lpw-samples-list" style="margin:6px 0 0 0; padding-left:20px; max-height:180px;
                   overflow-y:auto; color:#334155; font-size:12px;"></ul>
            </details>
            <div class="lpw-aplicar-wrap" style="margin-top:10px; padding-top:10px;
                 border-top:1px dashed #cbd5e1; display:none;">
              <button type="button" class="lpw-aplicar"
                      style="padding:6px 14px; background:#059669; color:white;
                             border:none; border-radius:4px; cursor:pointer;
                             font-size:13px; font-weight:500;">
                ⚡ Aplicar ahora a los <span class="lpw-aplicar-count">0</span> sin categoría
              </button>
              <span class="lpw-aplicar-result" style="margin-left:10px; font-size:12px;"></span>
              <p class="lpw-aplicar-help" style="margin:6px 0 0; font-size:11px; color:#94a3b8;">
                Asigna esta categoría a los artículos sin categoría que matchean
                las palabras de arriba. NO toca los que ya tienen otra categoría.
              </p>
            </div>
          </div>
        </div>
        '''.strip()
        return mark_safe(html)

    @staticmethod
    def _escape_attr(s: str) -> str:
        """Escape mínimo para que el JSON entre en value='...' sin romper."""
        return s.replace("'", '&#39;')

    class Media:
        # Inyectamos el script una vez por página. Inline JS sería más
        # simple PERO Django renderiza widgets uno por uno y duplicaría
        # el handler binding en cada instancia. Mejor un script externo
        # que itera todos los containers al DOMContentLoaded.
        js = ('admin/articulo/lista_palabras_widget.js',)
