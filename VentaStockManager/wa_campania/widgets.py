"""
Widget custom para `Campania.audiencia_filtro`.

Antes el JSONField se renderizaba como un textarea con JSON crudo
(`{"compraron_ultimos_dias": 30, "solo_con_whatsapp_valido": true}`).
El operador no sabe qué es JSON ni quiere aprenderlo.

Acá lo reemplazamos por checkboxes + un select para "días", igual que
en la pantalla de Difundir. Internamente persiste como JSON en un
hidden input (Django JSONField parsea sin saber del widget).

Esquema del JSON que mantenemos (NO cambia, así
`wa_campania.audiencia.resolver_clientes()` sigue funcionando):

  {
    "todos": bool,
    "compraron_ultimos_dias": int | None,
    "con_saldo_a_favor": bool,
    "con_saldo_deudor": bool,
    "solo_con_whatsapp_valido": bool,
  }
"""

from __future__ import annotations

import json

from django import forms
from django.utils.safestring import mark_safe


_DIAS_OPCIONES = [
    ('', 'Sin filtro (cualquier momento)'),
    ('7', 'Últimos 7 días'),
    ('30', 'Últimos 30 días'),
    ('60', 'Últimos 60 días'),
    ('90', 'Últimos 90 días'),
    ('180', 'Últimos 6 meses'),
    ('365', 'Último año'),
]


class AudienciaFiltroWidget(forms.Widget):
    """
    Render del filtro de audiencia con UI human-friendly. Mantiene el
    contrato JSON al guardar — Django/JSONField parsean lo mismo que
    antes.
    """

    def format_value(self, value):
        """
        Convierte el value del field a JSON string para el JS inicial.
        Tolera dict (modelo recién cargado), str (rebind tras error),
        y None/'' (form nuevo).
        """
        if value in (None, '', {}):
            return '{}'
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return '{}'

    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs or {}
        widget_id = attrs.get('id') or f'id_{name}'
        container_id = f'{widget_id}_container'
        json_initial = self.format_value(value)

        dias_options_html = ''.join(
            f'<option value="{v}">{label}</option>'
            for v, label in _DIAS_OPCIONES
        )

        html = f'''
        <div class="audiencia-filtro-widget" id="{container_id}" data-name="{name}">
          <input type="hidden" name="{name}" value='{self._escape_attr(json_initial)}' />
          <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px;">

            <!-- Toggle "Todos" -->
            <label style="display: flex; align-items: center; gap: 8px; margin-bottom: 12px;
                          padding: 8px 12px; background: white; border: 1px solid #cbd5e1;
                          border-radius: 6px; cursor: pointer; font-weight: 600; color: #0f172a;">
              <input type="checkbox" class="af-todos">
              <span>📣 Enviar a TODOS los clientes con WhatsApp opt-in</span>
              <span style="font-size: 11px; color: #64748b; font-weight: 400; margin-left: auto;">
                (ignora los filtros de abajo)
              </span>
            </label>

            <!-- Filtros (se ocultan si "todos" está tildado) -->
            <div class="af-filtros" style="display: flex; flex-direction: column; gap: 10px;">

              <!-- Filtro: días desde última compra -->
              <div>
                <label style="display: block; font-size: 12px; font-weight: 600;
                              color: #475569; margin-bottom: 4px;">
                  Filtrar por actividad: compraron en
                </label>
                <select class="af-dias"
                        style="width: 100%; padding: 8px 10px; border: 1px solid #cbd5e1;
                               border-radius: 6px; font-size: 14px; background: white;">
                  {dias_options_html}
                </select>
              </div>

              <!-- Filtros booleanos -->
              <div style="display: flex; flex-direction: column; gap: 6px;">
                <label style="display: flex; align-items: center; gap: 8px;
                              padding: 6px 10px; background: white; border: 1px solid #e2e8f0;
                              border-radius: 6px; cursor: pointer; font-size: 13px;">
                  <input type="checkbox" class="af-favor">
                  <span>💰 Solo clientes con <b>saldo a favor</b> (les debemos)</span>
                </label>
                <label style="display: flex; align-items: center; gap: 8px;
                              padding: 6px 10px; background: white; border: 1px solid #e2e8f0;
                              border-radius: 6px; cursor: pointer; font-size: 13px;">
                  <input type="checkbox" class="af-deudor">
                  <span>⚠ Solo clientes con <b>saldo deudor</b> (nos deben)</span>
                </label>
                <label style="display: flex; align-items: center; gap: 8px;
                              padding: 6px 10px; background: white; border: 1px solid #e2e8f0;
                              border-radius: 6px; cursor: pointer; font-size: 13px;
                              color: #64748b;">
                  <input type="checkbox" class="af-whatsapp-valido" checked>
                  <span>📱 Solo con WhatsApp válido (recomendado)</span>
                </label>
              </div>
            </div>

            <p style="margin: 10px 0 0 0; padding: 8px 10px; background: #fefce8;
                      border: 1px solid #fde047; border-radius: 6px; font-size: 11px;
                      color: #854d0e;">
              💡 El opt-in (<code style="background:white; padding:1px 4px; border-radius:3px;">puede_recibir_whatsapp=True</code>)
              SIEMPRE se respeta. Estos filtros refinan dentro de los que
              ya dieron consentimiento.
            </p>
          </div>
        </div>
        '''.strip()
        return mark_safe(html)

    @staticmethod
    def _escape_attr(s: str) -> str:
        return s.replace("'", '&#39;')

    class Media:
        js = ('admin/wa_campania/audiencia_filtro_widget.js',)
