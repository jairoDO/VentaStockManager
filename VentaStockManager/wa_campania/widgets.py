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
    "vendedor_ids": list[int],
    "campania_origen_id": int | None,
    "barrio": str,
    "centro_latitud": float | None,
    "centro_longitud": float | None,
    "radio_km": float | None,
    "clientes_excluidos_ids": list[int],
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

              <div>
                <label style="display:block; font-size:12px; font-weight:600; color:#475569; margin-bottom:4px;">
                  Clientes de una campaña anterior
                </label>
                <select class="af-campania-origen"
                        style="width:100%; padding:8px 10px; border:1px solid #cbd5e1; border-radius:6px; font-size:14px; background:white;">
                  <option value="">No usar una campaña anterior</option>
                </select>
                <div style="font-size:11px; color:#64748b; margin-top:3px;">Reutiliza sus destinatarios y permite refinarlos con vendedor o barrio.</div>
              </div>

              <div>
                <label style="display:block; font-size:12px; font-weight:600; color:#475569; margin-bottom:4px;">
                  Vendedor asignado
                </label>
                <select class="af-vendedores" multiple size="4"
                        style="width:100%; padding:8px 10px; border:1px solid #cbd5e1; border-radius:6px; font-size:14px; background:white;">
                  <option disabled>Cargando vendedores…</option>
                </select>
                <div style="font-size:11px; color:#64748b; margin-top:3px;">Podés elegir uno o varios vendedores.</div>
              </div>

              <div class="af-zona-mapa" style="padding:12px; background:white; border:1px solid #cbd5e1; border-radius:6px;">
                <div style="display:flex; flex-wrap:wrap; align-items:end; justify-content:space-between; gap:10px; margin-bottom:8px;">
                  <div>
                    <label style="display:block; font-size:12px; font-weight:700; color:#334155; margin-bottom:3px;">
                      📍 Zona en el mapa (recomendado)
                    </label>
                    <div style="font-size:11px; color:#64748b;">Tocá el mapa para marcar el centro. El radio es aproximado y se mide en línea recta.</div>
                  </div>
                  <label style="font-size:12px; color:#475569;">
                    Radio
                    <select class="af-radio-km browser-default" style="display:inline-block; width:auto; min-width:95px; margin-left:5px; padding:5px 8px; border:1px solid #cbd5e1; border-radius:5px; background:white;">
                      <option value="1">1 km</option>
                      <option value="2">2 km</option>
                      <option value="3" selected>3 km</option>
                      <option value="5">5 km</option>
                      <option value="10">10 km</option>
                      <option value="20">20 km</option>
                      <option value="30">30 km</option>
                    </select>
                  </label>
                </div>
                <div class="af-map" style="height:300px; width:100%; border:1px solid #cbd5e1; border-radius:6px; overflow:hidden;"></div>
                <div style="display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; margin-top:8px;">
                  <span class="af-map-summary" style="font-size:12px; color:#475569;">Marcá un punto para usar el radio.</span>
                  <div style="display:flex; gap:6px;">
                    <button type="button" class="af-use-location" style="padding:5px 9px;">⌖ Mi ubicación</button>
                    <button type="button" class="af-clear-zone" style="padding:5px 9px;" disabled>Quitar zona</button>
                  </div>
                </div>
              </div>

              <div>
                <label style="display:block; font-size:12px; font-weight:600; color:#475569; margin-bottom:4px;">
                  Localidad escrita (alternativa)
                </label>
                <input type="search" class="af-barrio" placeholder="Ej. Alta Córdoba"
                       style="width:100%; box-sizing:border-box; padding:8px 10px; border:1px solid #cbd5e1; border-radius:6px; font-size:14px;">
                <div style="font-size:11px; color:#64748b; margin-top:3px;">Úsala para clientes antiguos que todavía no tengan ubicación en el mapa.</div>
              </div>

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

            <!-- Resumen definitivo -->
            <div class="af-audience-summary" style="margin-top:16px; padding:14px; border:2px solid #bfdbfe; border-radius:8px; background:#eff6ff;">
              <div style="display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px;">
                <div>
                  <div style="font-size:12px; font-weight:700; color:#1e3a8a; text-transform:uppercase;">Destinatarios de esta campaña</div>
                  <div class="af-audience-total" style="font-size:26px; line-height:1.15; font-weight:800; color:#0f172a;">Elegí un filtro</div>
                  <div class="af-audience-detail" style="font-size:12px; color:#475569; margin-top:3px;">El número se actualizará automáticamente.</div>
                </div>
                <button type="button" class="af-use-suggested-radius" style="display:none; padding:7px 11px;">Usar radio sugerido</button>
              </div>
              <div class="af-audience-recommendation" style="display:none; margin-top:9px; padding-top:9px; border-top:1px solid #bfdbfe; font-size:12px; color:#1e40af;"></div>
            </div>

            <!-- Revisión paginada -->
            <div class="af-seleccion-manual" style="margin-top: 16px; border-top: 1px solid #cbd5e1; padding-top: 14px;">
              <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:8px;">
                <div>
                  <b style="color:#0f172a;">👥 Revisar destinatarios (opcional)</b>
                  <div style="font-size:11px; color:#64748b;">Todos los encontrados ya están incluidos. Destildá solamente a quien no quieras enviarle.</div>
                </div>
                <span class="af-selected-count" style="background:#dbeafe; color:#1d4ed8; padding:3px 9px; border-radius:12px; font-size:11px; font-weight:600;">0 excluidos</span>
              </div>
              <input type="search" class="af-client-search" placeholder="Buscar por nombre, dirección o WhatsApp…"
                     style="width:100%; box-sizing:border-box; padding:8px 10px; border:1px solid #cbd5e1; border-radius:6px; margin-bottom:8px;">
              <div class="af-sender-notice" style="display:none; margin-bottom:8px; padding:8px 10px; background:#fff7ed; border:1px solid #fdba74; border-radius:6px; font-size:11px; color:#9a3412;"></div>
              <div class="af-client-list" style="background:white; border:1px solid #e2e8f0; border-radius:6px; min-height:80px; overflow:hidden;">
                <div style="padding:16px; color:#64748b; text-align:center;">Cargando clientes…</div>
              </div>
              <div class="af-pagination" style="display:flex; align-items:center; justify-content:center; gap:10px; margin-top:8px;">
                <button type="button" class="af-prev" style="padding:5px 10px;">← Anterior</button>
                <span class="af-page-info" style="font-size:12px; color:#64748b;">Página 1</span>
                <button type="button" class="af-next" style="padding:5px 10px;">Siguiente →</button>
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
        # URL versionada: después de un deploy algunos teléfonos conservaban
        # el JS anterior y los desplegables quedaban en "Cargando…".
        css = {
            'all': (
                'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
                'https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css',
            ),
        }
        js = (
            'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
            'https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.js',
            'https://unpkg.com/@maplibre/maplibre-gl-leaflet/leaflet-maplibre-gl.js',
            '/static/admin/wa_campania/audiencia_filtro_widget.js?v=20260911d',
        )
