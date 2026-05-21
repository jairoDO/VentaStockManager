# CLAUDE.md — convenciones del proyecto

Notas para cualquier dev (humano o LLM) que toque este código. Si
algo está documentado acá, es **regla del proyecto** — no inventes
algo distinto a menos que vengas con razones.

## UI / pantallas custom

### Stack visual

Pantallas que armamos por fuera del django admin usan:

- **Tailwind CSS via CDN** (`https://cdn.tailwindcss.com`)
- **Alpine.js 3 via CDN** (`https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js`)
- Templates standalone (no extends de `admin/base_site.html` para
  pantallas operativas — solo el admin se queda con material-admin)

Pantallas que siguen este patrón hoy:

- `venta/templates/venta/venta_nueva.html`
- `cliente/templates/cliente/extracto.html`
- `articulo/templates/articulo/grilla_precios.html`
- `articulo/templates/articulo/lista_precios.html`
- `articulo/templates/articulo/lista_precios_publica.html`
- `configuracion/templates/configuracion/panel_tareas.html`

### REGLA: nunca alert / confirm / prompt del browser

Los nativos del browser:
- Rompen el look visual (colores del sistema operativo, no del app)
- Bloquean el thread principal de JavaScript
- No se pueden personalizar (labels, colores, layout)
- Mobile tiene comportamientos inconsistentes (especialmente iOS Safari)

**Usar siempre el modal Alpine reusable en
`articulo/templates/partials/_modal_alpine.html`.**

Cómo se usa desde otro template:

```html
{% comment %} 1. Mergeá el state del modal con tu componente Alpine: {% endcomment %}
<script>
  function miPantalla() {
    return {
      ...modalState(),
      // tu state...
    };
  }
</script>

{% comment %} 2. Incluí el partial al final del div con x-data: {% endcomment %}
<div x-data="miPantalla()">
  ...
  {% include "partials/_modal_alpine.html" %}
</div>
```

Llamar a `abrirModal({...})` para mostrar:

```js
// Confirmación tipo confirm()
this.abrirModal({
  tipo: 'confirm',
  titulo: 'Eliminar registro',
  mensaje: 'Esta acción no se puede deshacer. ¿Confirmás?',
  okLabel: 'Sí, eliminar',
  cancelLabel: 'No, cancelar',
  callback: () => { /* solo se ejecuta si confirma */ },
});

// Aviso tipo alert()
this.abrirModal({
  tipo: 'warning',
  titulo: 'La venta se guardó, pero...',
  lista: warnings,
  okLabel: 'Entendido',
  callback: () => { window.location.href = '/admin/venta/venta/'; },
});
```

Tipos: `'info'` (azul), `'warning'` (ámbar), `'error'` (rojo),
`'confirm'` (gris con botón Cancelar).

Si encontrás `alert()`, `confirm()` o `prompt()` en algún template
custom, **es un bug** — reemplazalo por el modal antes de seguir.

### Comentarios en templates Django

`{# ... #}` es **solo para una línea**. Si abarca varias, Django lo
renderiza como texto y aparece en la pantalla — bug clásico.

Para multi-línea, usar `{% comment %}...{% endcomment %}`.

**Bug recurrente** (ya pasó 5 veces antes de blindarlo). Ahora hay un
**Django system check** que falla si encuentra alguno roto. Ver
`configuracion/checks.py`. El check corre automáticamente con:

- `python manage.py check`
- `python manage.py runserver` (no arranca si falla)
- `python manage.py migrate` (no migra si falla)
- `python manage.py test` (no testea si falla)
- El `buildCommand` de Render (no deploya si falla)

Si introducís un `{# ... #}` multi-línea por error, vas a ver:

```
ERRORS:
?: (configuracion.E001) Comentario {# ... #} multi-línea en
   templates/foo.html:42. Django NO lo interpreta como comentario y lo
   renderiza como TEXTO en la página.
	HINT: Convertí a {% comment %}...{% endcomment %}. Preview: "..."
```

Solución: convertir a `{% comment %}...{% endcomment %}`.

Bypass de emergencia (no usar salvo necesidad real):
`SKIP_TEMPLATE_COMMENT_CHECK=1 python manage.py runserver`.

## Backend

### Cálculo de totales con descuentos

Todo lo financiero (PDFs, reportes, UI) debe usar las funciones
canónicas de `venta/utils.py`:

- `subtotal_linea(av)` — cantidad × precio × (1 − desc_línea/100)
- `subtotal_venta_sin_desc_global(venta)` — suma de subtotales antes del desc global
- `total_venta(venta)` — total final (subtotal × (1 − desc_global/100))

NO usar `Venta.precio_total` (property legacy que no aplica
descuentos) ni `ArticuloVenta.total` ni recalcular en cada lugar.

### Precio efectivo por cliente

Para listas de precios, sugerencias, PDFs y cualquier lugar donde
necesites "precio para este cliente":

```python
from articulo.precios import precio_efectivo, cargar_precios_pactados

# Para 1 artículo:
precio = precio_efectivo(articulo, cliente, descuento_lista)

# Para muchos (evita N+1):
mapa = cargar_precios_pactados(cliente, articulos)
for art in articulos:
    precio = precio_efectivo(art, cliente, descuento_lista, precios_pactados_map=mapa)
```

Aplica en cascada: PrecioCliente → precio_minorista → descuento_lista.

### Stock

- `ArticuloVenta.save()` ajusta stock automáticamente con el delta
  (create descuenta cantidad entera, update descuenta o devuelve el
  delta vs cantidad anterior).
- Signal `pre_delete` devuelve el stock al borrar (incluso cuando es
  cascade al borrar la Venta entera desde el admin).
- Stock insuficiente NO bloquea la venta — genera `AlertaStock`
  + warning en la respuesta. Ver `venta/views_nueva.py:api_venta_guardar`.

## Tests

Correr todo con:

```bash
docker compose exec -T web python manage.py test articulo wa_campania --no-input
```

Si agregás features con UI custom, considerá agregar tests E2E con
`Client` de Django (no browser) que cubran la API JSON.
