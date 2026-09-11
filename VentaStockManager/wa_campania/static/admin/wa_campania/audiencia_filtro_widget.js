/*
 * AudienciaFiltroWidget — UI amigable para Campania.audiencia_filtro.
 *
 * Reemplaza el textarea de JSON crudo por checkboxes + select.
 * Persiste como JSON en un hidden input para no romper el contrato
 * con `wa_campania.audiencia.resolver_clientes()`.
 *
 * Estado leído/escrito del JSON:
 *   { todos, compraron_ultimos_dias, con_saldo_a_favor,
 *     con_saldo_deudor, vendedor_ids, campania_origen_id, barrio,
 *     centro_latitud, centro_longitud, radio_km,
 *     solo_con_whatsapp_valido }
 *
 * Compatibilidad: solo vanilla JS. Sin Alpine ni jQuery.
 */
(function () {
  'use strict';

  function initWidget(container) {
    if (container.dataset.initialized === '1') return;
    container.dataset.initialized = '1';

    const hidden = container.querySelector('input[type="hidden"]');
    const cbTodos = container.querySelector('.af-todos');
    const selDias = container.querySelector('.af-dias');
    const cbFavor = container.querySelector('.af-favor');
    const cbDeudor = container.querySelector('.af-deudor');
    const cbWhatsappValido = container.querySelector('.af-whatsapp-valido');
    const selVendedores = container.querySelector('.af-vendedores');
    const selCampaniaOrigen = container.querySelector('.af-campania-origen');
    const inputBarrio = container.querySelector('.af-barrio');
    const filtrosBox = container.querySelector('.af-filtros');
    const clientList = container.querySelector('.af-client-list');
    const clientSearch = container.querySelector('.af-client-search');
    const prevButton = container.querySelector('.af-prev');
    const nextButton = container.querySelector('.af-next');
    const pageInfo = container.querySelector('.af-page-info');
    const selectedCount = container.querySelector('.af-selected-count');
    const senderNotice = container.querySelector('.af-sender-notice');
    const mapElement = container.querySelector('.af-map');
    const mapSummary = container.querySelector('.af-map-summary');
    const radiusSelect = container.querySelector('.af-radio-km');
    const clearZoneButton = container.querySelector('.af-clear-zone');
    const useLocationButton = container.querySelector('.af-use-location');

    // Parse del JSON inicial. Si falla, arrancamos con defaults sanos.
    let state;
    try {
      const raw = hidden.value || '{}';
      state = JSON.parse(raw);
      if (typeof state !== 'object' || state === null) state = {};
    } catch (e) {
      state = {};
    }

    // Aplicar al UI los valores iniciales.
    cbTodos.checked = !!state.todos;
    const dias = state.compraron_ultimos_dias;
    selDias.value = (dias === null || dias === undefined || dias === '') ? '' : String(dias);
    cbFavor.checked = !!state.con_saldo_a_favor;
    cbDeudor.checked = !!state.con_saldo_deudor;
    // solo_con_whatsapp_valido: default true si no viene en el state
    // (es el comportamiento recomendado / legal).
    cbWhatsappValido.checked = state.solo_con_whatsapp_valido !== false;
    inputBarrio.value = state.barrio || '';
    const selectedVendedores = new Set((state.vendedor_ids || []).map(Number));
    let selectedCampaniaOrigen = state.campania_origen_id ? Number(state.campania_origen_id) : null;
    const selectedIds = new Set((state.clientes_ids || []).map(Number));
    let currentPage = 1;
    let totalPages = 1;
    let searchTimer = null;
    let centerLatitude = Number(state.centro_latitud);
    let centerLongitude = Number(state.centro_longitud);
    if (!Number.isFinite(centerLatitude) || !Number.isFinite(centerLongitude)) {
      centerLatitude = null;
      centerLongitude = null;
    }
    let radiusKm = Number(state.radio_km) || 3;
    if (!Array.from(radiusSelect.options).some(function (option) {
      return Number(option.value) === radiusKm;
    })) radiusKm = 3;
    radiusSelect.value = String(radiusKm);
    let audienceMap = null;
    let centerMarker = null;
    let radiusCircle = null;
    let clientMarkers = null;

    function sync() {
      // Reconstruir el JSON desde el UI.
      const next = {
        todos: cbTodos.checked,
        compraron_ultimos_dias: selDias.value ? Number(selDias.value) : null,
        con_saldo_a_favor: cbFavor.checked,
        con_saldo_deudor: cbDeudor.checked,
        solo_con_whatsapp_valido: cbWhatsappValido.checked,
        vendedor_ids: Array.from(selectedVendedores),
        campania_origen_id: selectedCampaniaOrigen,
        barrio: inputBarrio.value.trim(),
        centro_latitud: centerLatitude,
        centro_longitud: centerLongitude,
        radio_km: centerLatitude !== null ? radiusKm : null,
        clientes_ids: Array.from(selectedIds),
      };
      hidden.value = JSON.stringify(next);
      // Ocultar filtros si "todos" está tildado — visualmente
      // comunica que esos checkboxes no aplican.
      filtrosBox.style.opacity = next.todos ? '0.4' : '1';
      filtrosBox.style.pointerEvents = next.todos ? 'none' : 'auto';
      selectedCount.textContent = selectedIds.size + (selectedIds.size === 1 ? ' seleccionado' : ' seleccionados');
      clearZoneButton.disabled = centerLatitude === null;
    }

    function escapeHtml(value) {
      const div = document.createElement('div');
      div.textContent = value == null ? '' : String(value);
      return div.innerHTML;
    }

    function refreshMaterialSelect(select) {
      // Material Admin transforma los <select> al cargar la página. Como
      // vendedores y campañas llegan después por fetch, el desplegable
      // visible conserva las opciones iniciales si no lo reconstruimos.
      if (!window.M || !window.M.FormSelect) return;
      const instance = window.M.FormSelect.getInstance(select);
      if (instance) instance.destroy();
      window.M.FormSelect.init(select);
    }

    function drawSelectedZone() {
      if (!audienceMap || centerLatitude === null || centerLongitude === null) return;
      const center = [centerLatitude, centerLongitude];
      if (!centerMarker) {
        centerMarker = window.L.marker(center, {draggable: true}).addTo(audienceMap);
        centerMarker.bindTooltip('Centro de la zona');
        centerMarker.on('dragend', function (event) {
          const point = event.target.getLatLng();
          setSelectedZone(point.lat, point.lng, true);
        });
      } else {
        centerMarker.setLatLng(center);
      }
      if (!radiusCircle) {
        radiusCircle = window.L.circle(center, {
          radius: radiusKm * 1000,
          color: '#4f46e5',
          fillColor: '#818cf8',
          fillOpacity: 0.16,
          weight: 2,
        }).addTo(audienceMap);
      } else {
        radiusCircle.setLatLng(center);
        radiusCircle.setRadius(radiusKm * 1000);
      }
    }

    function setSelectedZone(latitude, longitude, reloadClients) {
      centerLatitude = Number(latitude);
      centerLongitude = Number(longitude);
      cbTodos.checked = false;
      sync();
      drawSelectedZone();
      if (audienceMap) audienceMap.panTo([centerLatitude, centerLongitude]);
      if (reloadClients) loadClients(1);
    }

    function clearSelectedZone() {
      centerLatitude = null;
      centerLongitude = null;
      if (audienceMap && centerMarker) audienceMap.removeLayer(centerMarker);
      if (audienceMap && radiusCircle) audienceMap.removeLayer(radiusCircle);
      centerMarker = null;
      radiusCircle = null;
      sync();
      loadClients(1);
    }

    function renderMapPoints(data) {
      if (!audienceMap || !clientMarkers) return;
      clientMarkers.clearLayers();
      const points = data.map_points || [];
      points.forEach(function (point) {
        const coordinates = [Number(point.latitud), Number(point.longitud)];
        if (!Number.isFinite(coordinates[0]) || !Number.isFinite(coordinates[1])) return;
        const marker = window.L.circleMarker(coordinates, {
          radius: 6,
          color: '#047857',
          fillColor: '#10b981',
          fillOpacity: 0.8,
          weight: 2,
        });
        const distance = point.distancia_km === undefined
          ? ''
          : '<br>' + Number(point.distancia_km).toFixed(1).replace('.', ',') + ' km del centro';
        marker.bindTooltip('<b>' + escapeHtml(point.nombre) + '</b>' + distance);
        marker.addTo(clientMarkers);
      });

      const missing = Number(data.clientes_sin_coordenadas || 0);
      if (data.zona) {
        mapSummary.textContent = data.total + ' dentro de ' + radiusKm + ' km';
        if (missing) mapSummary.textContent += ' · ' + missing + ' sin ubicación no se pudieron evaluar';
      } else {
        mapSummary.textContent = points.length + ' clientes con ubicación';
        if (missing) mapSummary.textContent += ' · ' + missing + ' sin ubicación';
      }

    }

    function initAudienceMap() {
      if (!mapElement || !window.L) {
        mapSummary.textContent = 'No se pudo cargar el mapa. Podés usar la localidad escrita.';
        return;
      }
      // El galpón está en el ingreso sur de Villa Allende. Empezamos ahí
      // y no alejamos el mapa para abarcar clientes con puntos dispersos.
      const initialCenter = centerLatitude === null
        ? [-31.3073, -64.2811]
        : [centerLatitude, centerLongitude];
      audienceMap = window.L.map(mapElement).setView(initialCenter, centerLatitude === null ? 14 : 13);
      if (window.L.maplibreGL && window.maplibregl) {
        window.L.maplibreGL({
          style: 'https://tiles.openfreemap.org/styles/positron',
        }).addTo(audienceMap);
        audienceMap.attributionControl.addAttribution(
          '<a href="https://openfreemap.org/" target="_blank">OpenFreeMap</a> · ' +
          '<a href="https://www.openmaptiles.org/" target="_blank">© OpenMapTiles</a> · ' +
          'datos <a href="https://www.openstreetmap.org/copyright" target="_blank">© OpenStreetMap</a>',
        );
      } else {
        mapSummary.textContent = 'No se pudo cargar el fondo del mapa. Actualizá la página para volver a intentar.';
      }
      clientMarkers = window.L.layerGroup().addTo(audienceMap);
      audienceMap.on('click', function (event) {
        setSelectedZone(event.latlng.lat, event.latlng.lng, true);
      });
      drawSelectedZone();
      setTimeout(function () { audienceMap.invalidateSize(); }, 50);
    }

    function loadClients(page) {
      currentPage = page || 1;
      const params = new URLSearchParams({page: String(currentPage), q: clientSearch.value.trim()});
      Array.from(selectedVendedores).forEach(function (id) { params.append('vendedor', String(id)); });
      if (selectedCampaniaOrigen) params.set('campania', String(selectedCampaniaOrigen));
      if (inputBarrio.value.trim()) params.set('barrio', inputBarrio.value.trim());
      if (centerLatitude !== null && centerLongitude !== null) {
        params.set('centro_latitud', String(centerLatitude));
        params.set('centro_longitud', String(centerLongitude));
        params.set('radio_km', String(radiusKm));
      }
      clientList.innerHTML = '<div style="padding:16px; color:#64748b; text-align:center;">Cargando clientes…</div>';
      fetch('/wa-campania/api/clientes/?' + params.toString(), {credentials: 'same-origin'})
        .then(function (response) {
          if (!response.ok) throw new Error('No se pudo cargar la lista');
          return response.json();
        })
        .then(function (data) {
          if (selVendedores.dataset.loaded !== '1') {
            selVendedores.innerHTML = (data.vendedores || []).map(function (vendedor) {
              return '<option value="' + vendedor.id + '">' + escapeHtml(vendedor.nombre) + '</option>';
            }).join('');
            Array.from(selVendedores.options).forEach(function (option) {
              option.selected = selectedVendedores.has(Number(option.value));
            });
            selVendedores.dataset.loaded = '1';
            refreshMaterialSelect(selVendedores);
          }
          if (selCampaniaOrigen.dataset.loaded !== '1') {
            selCampaniaOrigen.innerHTML = '<option value="">No usar una campaña anterior</option>' +
              (data.campanias || []).map(function (campania) {
                return '<option value="' + campania.id + '">' + escapeHtml(campania.nombre) +
                  ' · ' + escapeHtml(campania.fecha) + '</option>';
              }).join('');
            selCampaniaOrigen.value = selectedCampaniaOrigen ? String(selectedCampaniaOrigen) : '';
            selCampaniaOrigen.dataset.loaded = '1';
            refreshMaterialSelect(selCampaniaOrigen);
          }
          const excludedIds = (data.excluded_sender_client_ids || []).map(Number);
          excludedIds.forEach(function (id) { selectedIds.delete(id); });
          if (data.excluded_sender_number) {
            senderNotice.style.display = 'block';
            senderNotice.textContent = 'El WhatsApp conectado (' + data.excluded_sender_number + ') no aparece en la lista porque una cuenta no puede enviarse mensajes a sí misma.';
          } else {
            senderNotice.style.display = 'none';
            senderNotice.textContent = '';
          }
          sync();
          totalPages = data.pages || 1;
          currentPage = data.page || 1;
          if (!data.results.length) {
            clientList.innerHTML = '<div style="padding:16px; color:#64748b; text-align:center;">No se encontraron clientes elegibles.</div>';
          } else {
            clientList.innerHTML = data.results.map(function (client) {
              const checked = selectedIds.has(Number(client.id)) ? ' checked' : '';
              return '<label style="display:flex; gap:9px; align-items:flex-start; padding:9px 11px; border-bottom:1px solid #f1f5f9; cursor:pointer;">' +
                '<input type="checkbox" class="af-client-check" value="' + client.id + '"' + checked + '>' +
                '<span><b style="color:#0f172a;">' + escapeHtml(client.nombre) + '</b>' +
                '<br><span style="font-size:11px; color:#64748b;">' + escapeHtml(client.whatsapp) +
                (client.direccion ? ' · ' + escapeHtml(client.direccion) : '') + '</span></span></label>';
            }).join('');
            clientList.querySelectorAll('.af-client-check').forEach(function (checkbox) {
              checkbox.addEventListener('change', function () {
                const id = Number(checkbox.value);
                if (checkbox.checked) selectedIds.add(id); else selectedIds.delete(id);
                sync();
              });
            });
          }
          pageInfo.textContent = 'Página ' + currentPage + ' de ' + totalPages + ' · ' + data.total + ' clientes';
          prevButton.disabled = !data.has_previous;
          nextButton.disabled = !data.has_next;
          renderMapPoints(data);
        })
        .catch(function (error) {
          if (selVendedores.dataset.loaded !== '1') {
            selVendedores.innerHTML = '<option disabled>No se pudieron cargar los vendedores</option>';
            refreshMaterialSelect(selVendedores);
          }
          if (selCampaniaOrigen.dataset.loaded !== '1') {
            selCampaniaOrigen.innerHTML = '<option value="">No se pudieron cargar las campañas anteriores</option>';
            refreshMaterialSelect(selCampaniaOrigen);
          }
          clientList.innerHTML = '<div style="padding:16px; color:#b91c1c; text-align:center;">' + escapeHtml(error.message) + '</div>';
        });
    }

    prevButton.addEventListener('click', function () { if (currentPage > 1) loadClients(currentPage - 1); });
    nextButton.addEventListener('click', function () { if (currentPage < totalPages) loadClients(currentPage + 1); });
    clientSearch.addEventListener('input', function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () { loadClients(1); }, 250);
    });

    selVendedores.addEventListener('change', function () {
      selectedVendedores.clear();
      Array.from(selVendedores.selectedOptions).forEach(function (option) {
        selectedVendedores.add(Number(option.value));
      });
      cbTodos.checked = false;
      sync();
      loadClients(1);
    });
    selCampaniaOrigen.addEventListener('change', function () {
      selectedCampaniaOrigen = selCampaniaOrigen.value ? Number(selCampaniaOrigen.value) : null;
      cbTodos.checked = false;
      sync();
      loadClients(1);
    });
    inputBarrio.addEventListener('input', function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        cbTodos.checked = false;
        sync();
        loadClients(1);
      }, 350);
    });
    radiusSelect.addEventListener('change', function () {
      radiusKm = Number(radiusSelect.value) || 3;
      if (centerLatitude !== null) {
        cbTodos.checked = false;
        sync();
        drawSelectedZone();
        loadClients(1);
      }
    });
    clearZoneButton.addEventListener('click', clearSelectedZone);
    useLocationButton.addEventListener('click', function () {
      if (!navigator.geolocation) {
        mapSummary.textContent = 'Este dispositivo no permite detectar la ubicación. Marcala en el mapa.';
        return;
      }
      mapSummary.textContent = 'Detectando ubicación…';
      navigator.geolocation.getCurrentPosition(
        function (position) {
          setSelectedZone(position.coords.latitude, position.coords.longitude, true);
        },
        function () {
          mapSummary.textContent = 'No se pudo detectar la ubicación. Marcala en el mapa.';
        },
        {enableHighAccuracy: true, timeout: 10000},
      );
    });

    [cbTodos, selDias, cbFavor, cbDeudor, cbWhatsappValido].forEach(function (el) {
      el.addEventListener('change', sync);
    });

    // Mutual exclusion: si tildan "a favor" y "deudor" a la vez, los
    // filtros no van a matchear a nadie. Avisamos en consola y
    // destildamos el otro como heurística.
    cbFavor.addEventListener('change', function () {
      if (cbFavor.checked && cbDeudor.checked) cbDeudor.checked = false;
      sync();
    });
    cbDeudor.addEventListener('change', function () {
      if (cbDeudor.checked && cbFavor.checked) cbFavor.checked = false;
      sync();
    });

    initAudienceMap();
    sync();  // primer flush para que el hidden coincida con el UI
    loadClients(1);
  }

  function initAll() {
    document.querySelectorAll('.audiencia-filtro-widget').forEach(initWidget);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
