/*
 * ListaPalabrasWidget — UI dinámica para `palabras_clave` en ReglaCategoria.
 *
 * Reemplaza el JSONField crudo (donde el operador veía ["a","b","c"] y
 * tenía que escribir JSON válido a mano) por una lista editable de
 * inputs con botones X y "+ agregar palabra".
 *
 * Cómo funciona:
 *   - El widget Python renderiza un <input type="hidden" name="..."
 *     value="[json]"> + un container vacío.
 *   - Este script encuentra TODOS los containers `.lista-palabras-widget`
 *     en la página (incluye los de inlines con varias filas) y para
 *     cada uno: parsea el JSON inicial, renderiza la lista de inputs,
 *     y vuelve a serializar el hidden cada vez que cambia algo.
 *   - El form submitea normalmente: Django recibe el JSON string en el
 *     name del field y JSONField lo parsea sin saber del widget.
 *
 * Compatibilidad: solo vanilla JS y APIs DOM modernos. Sin Alpine ni
 * jQuery — el admin no las garantiza disponibles.
 */
(function () {
  'use strict';

  function rowHtml(valor) {
    // String template para la fila. Escapeamos el valor con .value (no
    // con innerHTML) para evitar XSS si alguien grabó algo raro a mano.
    return (
      '<div class="lista-palabras-row" style="display:flex; gap:6px; align-items:center;">' +
      '<input type="text" class="lista-palabras-input" ' +
      'style="flex:1; padding:6px 10px; border:1px solid #cbd5e1; border-radius:4px; font-size:14px;" ' +
      'placeholder="palabra clave" />' +
      '<button type="button" class="lista-palabras-remove" ' +
      'title="Quitar palabra" ' +
      'style="padding:4px 10px; background:#fee2e2; color:#b91c1c; ' +
      'border:1px solid #fca5a5; border-radius:4px; cursor:pointer; ' +
      'font-size:14px; line-height:1;">×</button>' +
      '</div>'
    );
  }

  function initWidget(container) {
    // Skip el "template" invisible de Django inline (class .empty-form).
    // Django lo tiene en el DOM al cargar la página y lo clona cuando
    // clickeás "+ Agregar Regla". Si lo inicializáramos, el dataset
    // initialized=1 se copiaría a las filas clonadas → init() saldría
    // antes de bindear el botón "+ agregar palabra" → el botón aparece
    // pero no hace nada. Era el bug "no me deja agregar palabras".
    if (container.closest('.empty-form')) return;
    // Algunos forks de Django/material usan `.dynamic-form` con
    // `__prefix__` en los IDs. Cubrimos esa variante también.
    const hidden = container.querySelector('input[type="hidden"]');
    if (hidden && hidden.name && hidden.name.indexOf('__prefix__') !== -1) return;

    if (container.dataset.initialized === '1') return;
    container.dataset.initialized = '1';

    const hidden = container.querySelector('input[type="hidden"]');
    const itemsBox = container.querySelector('.lista-palabras-items');
    const addBtn = container.querySelector('.lista-palabras-add');

    // Estado en memoria. Single source of truth: cuando cambia, re-serializamos.
    let palabras;
    try {
      const raw = hidden.value || '[]';
      palabras = JSON.parse(raw);
      if (!Array.isArray(palabras)) palabras = [];
    } catch (e) {
      palabras = [];
    }
    // Filtra valores no-string defensivamente.
    palabras = palabras.filter(function (p) { return typeof p === 'string'; });

    function sync() {
      hidden.value = JSON.stringify(palabras);
    }

    function render() {
      itemsBox.innerHTML = '';
      palabras.forEach(function (p, idx) {
        const tmp = document.createElement('div');
        tmp.innerHTML = rowHtml(p);
        const row = tmp.firstChild;
        const inp = row.querySelector('.lista-palabras-input');
        inp.value = p;
        inp.addEventListener('input', function () {
          palabras[idx] = inp.value;
          sync();
        });
        // Tab desde el último input → agregar nueva, evita "y ahora qué".
        inp.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter') {
            ev.preventDefault();
            palabras.push('');
            sync();
            render();
            // Foco en la nueva.
            const inputs = itemsBox.querySelectorAll('.lista-palabras-input');
            if (inputs.length) inputs[inputs.length - 1].focus();
          }
        });
        row.querySelector('.lista-palabras-remove').addEventListener('click', function () {
          palabras.splice(idx, 1);
          sync();
          render();
        });
        itemsBox.appendChild(row);
      });
    }

    addBtn.addEventListener('click', function () {
      palabras.push('');
      sync();
      render();
      // Foco en la última creada.
      const inputs = itemsBox.querySelectorAll('.lista-palabras-input');
      if (inputs.length) inputs[inputs.length - 1].focus();
    });

    // ---- Preview de artículos que matchearían ----
    // Hits ligeros al endpoint /articulos/api/reglas/preview/ con debounce
    // para no martillar la DB mientras el operador tipea. El panel se
    // muestra/oculta según si hay keywords no vacías.
    const previewBox = container.querySelector('.lista-palabras-preview');
    const previewTotal = container.querySelector('.lpw-total');
    const previewSinCat = container.querySelector('.lpw-sin-cat');
    const previewConOtra = container.querySelector('.lpw-con-otra');
    const previewSamples = container.querySelector('.lpw-samples-list');
    const previewLoading = container.querySelector('.lpw-loading');
    const aplicarWrap = container.querySelector('.lpw-aplicar-wrap');
    const aplicarBtn = container.querySelector('.lpw-aplicar');
    const aplicarCount = container.querySelector('.lpw-aplicar-count');
    const aplicarResult = container.querySelector('.lpw-aplicar-result');

    // categoria_id se infiere de la URL del admin. Patrón estándar de
    // Django: /admin/articulo/categoria/<id>/change/ → categoria_id = id.
    // Si la URL es de "agregar" (.../add/) no hay id todavía: ocultamos
    // el botón "Aplicar ahora" (la categoría todavía no existe en DB).
    function inferCategoriaId() {
      const m = window.location.pathname.match(/\/categoria\/(\d+)\/change\//);
      return m ? Number(m[1]) : null;
    }
    const categoriaId = inferCategoriaId();

    let previewTimer = null;
    function debouncedPreview() {
      if (previewTimer) clearTimeout(previewTimer);
      previewTimer = setTimeout(fetchPreview, 400);
    }

    function fetchPreview() {
      const validKw = palabras.map(function (p) { return (p || '').trim(); })
        .filter(function (p) { return p.length > 0; });
      if (validKw.length === 0) {
        previewBox.style.display = 'none';
        return;
      }
      previewBox.style.display = '';
      previewLoading.style.display = '';
      const qs = encodeURIComponent(validKw.join(','));
      fetch('/articulos/api/reglas/preview/?keywords=' + qs, {
        credentials: 'same-origin',
      }).then(function (r) {
        return r.json();
      }).then(function (data) {
        previewLoading.style.display = 'none';
        previewTotal.textContent = String(data.total);
        previewSinCat.textContent = data.sin_categoria + ' sin categoría';
        previewConOtra.textContent = data.con_otra_categoria + ' ya con otra';
        // Render samples
        previewSamples.innerHTML = '';
        (data.samples || []).forEach(function (n) {
          const li = document.createElement('li');
          li.textContent = n;
          previewSamples.appendChild(li);
        });
        // Botón "Aplicar ahora" visible solo si:
        //   - estamos en /change/ (categoria_id conocido)
        //   - hay al menos uno "sin categoría" para tocar
        if (categoriaId && data.sin_categoria > 0) {
          aplicarWrap.style.display = '';
          aplicarCount.textContent = String(data.sin_categoria);
          aplicarBtn.disabled = false;
          aplicarBtn.style.opacity = '1';
        } else {
          aplicarWrap.style.display = 'none';
        }
      }).catch(function (e) {
        previewLoading.style.display = 'none';
        console.warn('Preview falló', e);
      });
    }

    // Bind: cualquier cambio en palabras dispara preview.
    // También al renderizar inicialmente, si vienen palabras precargadas.
    const origRender = render;
    render = function () {
      origRender();
      // Re-bind onInput de los inputs para que disparen preview también.
      // (origRender ya bind-ea para sync.)
      itemsBox.querySelectorAll('.lista-palabras-input').forEach(function (inp) {
        inp.addEventListener('input', debouncedPreview);
      });
      debouncedPreview();
    };

    // Click "Aplicar ahora": POST al endpoint, muestra resultado inline.
    // NO usamos confirm() (regla del proyecto) — el botón ya es explícito
    // y solo aparece después de que el operador vio el preview.
    if (aplicarBtn) {
      aplicarBtn.addEventListener('click', function () {
        if (!categoriaId) return;
        const validKw = palabras.map(function (p) { return (p || '').trim(); })
          .filter(function (p) { return p.length > 0; });
        if (validKw.length === 0) return;
        aplicarBtn.disabled = true;
        aplicarBtn.style.opacity = '0.6';
        aplicarResult.textContent = 'Aplicando…';
        aplicarResult.style.color = '#475569';

        // CSRF token: viene del cookie que Django setea cuando hay form.
        const csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
        fetch('/articulos/api/reglas/aplicar-ahora/', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': decodeURIComponent(csrf),
          },
          body: JSON.stringify({
            keywords: validKw.join(','),
            categoria_id: categoriaId,
          }),
        }).then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, data: data };
          });
        }).then(function (res) {
          if (!res.ok || !res.data.ok) {
            aplicarResult.textContent = '✗ ' + (res.data.mensaje || 'Error al aplicar.');
            aplicarResult.style.color = '#b91c1c';
            aplicarBtn.disabled = false;
            aplicarBtn.style.opacity = '1';
            return;
          }
          aplicarResult.textContent = '✓ ' + res.data.asignados + ' artículos asignados a "'
            + res.data.categoria_nombre + '".';
          aplicarResult.style.color = '#047857';
          // Re-fetch preview para que el contador "sin categoría" baje
          // y el botón se oculte (ya no quedan que asignar).
          debouncedPreview();
        }).catch(function (e) {
          aplicarResult.textContent = '✗ Error de red: ' + e.message;
          aplicarResult.style.color = '#b91c1c';
          aplicarBtn.disabled = false;
          aplicarBtn.style.opacity = '1';
        });
      });
    }

    sync();
    render();
  }

  function initAll() {
    document
      .querySelectorAll('.lista-palabras-widget')
      .forEach(initWidget);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }

  // Django admin inlines: cuando agregás una nueva fila inline, dispara
  // el evento `formset:added` (jQuery). Re-corremos init para los widgets
  // recién insertados. Si jQuery no está, no rompe — solo no se inicializa
  // la fila nueva hasta que recargues. (Mejor que tirar excepción.)
  if (window.django && window.django.jQuery) {
    window.django.jQuery(document).on('formset:added', function () {
      initAll();
    });
  }
})();
