/*
 * AudienciaFiltroWidget — UI amigable para Campania.audiencia_filtro.
 *
 * Reemplaza el textarea de JSON crudo por checkboxes + select.
 * Persiste como JSON en un hidden input para no romper el contrato
 * con `wa_campania.audiencia.resolver_clientes()`.
 *
 * Estado leído/escrito del JSON:
 *   { todos, compraron_ultimos_dias, con_saldo_a_favor,
 *     con_saldo_deudor, solo_con_whatsapp_valido }
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
    const inputBarrio = container.querySelector('.af-barrio');
    const filtrosBox = container.querySelector('.af-filtros');
    const clientList = container.querySelector('.af-client-list');
    const clientSearch = container.querySelector('.af-client-search');
    const prevButton = container.querySelector('.af-prev');
    const nextButton = container.querySelector('.af-next');
    const pageInfo = container.querySelector('.af-page-info');
    const selectedCount = container.querySelector('.af-selected-count');
    const senderNotice = container.querySelector('.af-sender-notice');

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
    const selectedIds = new Set((state.clientes_ids || []).map(Number));
    let currentPage = 1;
    let totalPages = 1;
    let searchTimer = null;

    function sync() {
      // Reconstruir el JSON desde el UI.
      const next = {
        todos: cbTodos.checked,
        compraron_ultimos_dias: selDias.value ? Number(selDias.value) : null,
        con_saldo_a_favor: cbFavor.checked,
        con_saldo_deudor: cbDeudor.checked,
        solo_con_whatsapp_valido: cbWhatsappValido.checked,
        vendedor_ids: Array.from(selectedVendedores),
        barrio: inputBarrio.value.trim(),
        clientes_ids: Array.from(selectedIds),
      };
      hidden.value = JSON.stringify(next);
      // Ocultar filtros si "todos" está tildado — visualmente
      // comunica que esos checkboxes no aplican.
      filtrosBox.style.opacity = next.todos ? '0.4' : '1';
      filtrosBox.style.pointerEvents = next.todos ? 'none' : 'auto';
      selectedCount.textContent = selectedIds.size + (selectedIds.size === 1 ? ' seleccionado' : ' seleccionados');
    }

    function escapeHtml(value) {
      const div = document.createElement('div');
      div.textContent = value == null ? '' : String(value);
      return div.innerHTML;
    }

    function loadClients(page) {
      currentPage = page || 1;
      const params = new URLSearchParams({page: String(currentPage), q: clientSearch.value.trim()});
      Array.from(selectedVendedores).forEach(function (id) { params.append('vendedor', String(id)); });
      if (inputBarrio.value.trim()) params.set('barrio', inputBarrio.value.trim());
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
        })
        .catch(function (error) {
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
    inputBarrio.addEventListener('input', function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        cbTodos.checked = false;
        sync();
        loadClients(1);
      }, 350);
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
