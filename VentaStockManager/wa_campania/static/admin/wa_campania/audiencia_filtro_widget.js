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
    const filtrosBox = container.querySelector('.af-filtros');

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

    function sync() {
      // Reconstruir el JSON desde el UI.
      const next = {
        todos: cbTodos.checked,
        compraron_ultimos_dias: selDias.value ? Number(selDias.value) : null,
        con_saldo_a_favor: cbFavor.checked,
        con_saldo_deudor: cbDeudor.checked,
        solo_con_whatsapp_valido: cbWhatsappValido.checked,
      };
      hidden.value = JSON.stringify(next);
      // Ocultar filtros si "todos" está tildado — visualmente
      // comunica que esos checkboxes no aplican.
      filtrosBox.style.opacity = next.todos ? '0.4' : '1';
      filtrosBox.style.pointerEvents = next.todos ? 'none' : 'auto';
    }

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
