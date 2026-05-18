# Comportamiento esperado — Pantalla de Venta

> **Para qué sirve este documento**: caracterizar qué hace la pantalla
> `/admin/venta/venta/add/` HOY (antes del refactor a Alpine.js). Cada
> ítem es un test manual que ejecutás y marcás. Si después del refactor
> alguno se rompe → tenemos que arreglarlo o revertir.
>
> **Cuándo se actualiza**: cada vez que se agrega/cambia una funcionalidad
> en la pantalla. Antes de empezar el refactor, este documento define
> "lo que ya funciona y NO se puede romper".

## Setup para validar

- URL: `http://localhost:8000/admin/venta/venta/add/`
- User: `jairo` / `admin123` (en local Docker)
- Datos cargados: dump real de PA (117k objetos, 13908 ventas, 1226 artículos)

---

## Test 1: Carga inicial de la pantalla

- [ ] La página carga sin errores 500
- [ ] No hay errores rojos en la consola del browser
- [ ] El dropdown de **Cliente** muestra opciones cuando se hace click
- [ ] El select de **Vendedor** muestra opciones
- [ ] El campo **Fecha de compra** está editable
- [ ] La sección **Artículos vendidos** tiene al menos una fila vacía con
      botón "Add another" debajo

## Test 2: Autocomplete de cliente

- [ ] Tipear "ma" en el campo cliente filtra resultados a clientes cuyo
      nombre contiene "ma"
- [ ] Click en un resultado lo selecciona y cierra el dropdown
- [ ] El cliente seleccionado queda visible en el input

## Test 3: Autocomplete de artículo

- [ ] Tipear 2+ caracteres en el campo artículo filtra resultados
- [ ] Los resultados muestran formato: `código - codigo_interno | marca | nombre | Min $X | May $Y | umbral N`
- [ ] Click en un resultado lo selecciona

## Test 4: Cálculo de precio al seleccionar artículo (LO MÁS CRÍTICO)

- [ ] Al seleccionar un artículo, el campo **Precio** se llena automáticamente
- [ ] Si **Cantidad ≤ umbral**: el precio = `precio_minorista`
- [ ] Si **Cantidad > umbral**: el precio = `precio_mayorista`
- [ ] Si cambio la cantidad, el precio se recalcula (puede saltar de
      minorista a mayorista y viceversa)

## Test 5: Total de la línea

- [ ] En cada fila de artículo, el campo **Precio total** muestra
      `cantidad × precio`
- [ ] Cambiar cantidad → total se actualiza
- [ ] Cambiar precio manual → total se actualiza

## Test 6: Total general

- [ ] Hay un campo / display de **Total general** (probablemente el
      `div.readonly` que vi en el JS)
- [ ] El total general = suma de los `precio_total` de cada línea
- [ ] Agregar una fila → total se recalcula
- [ ] Borrar una fila → total se recalcula

## Test 7: Agregar / quitar líneas

- [ ] Click en "Add another" agrega una fila nueva
- [ ] La fila nueva tiene los mismos selects/inputs que las otras
- [ ] El JS se enlaza correctamente con la fila nueva (al seleccionar
      artículo en una fila nueva, llena precio)
- [ ] Botón / icono de eliminar saca la fila
- [ ] El total general se recalcula tras agregar/quitar

## Test 8: Guardado de la venta

- [ ] Click en "Guardar" persiste la venta
- [ ] Te redirige a la lista de ventas
- [ ] La venta aparece en la lista con los datos correctos

## Test 9: Edición de venta existente

- [ ] Abrir una venta existente (de las 13908 cargadas)
- [ ] Los datos se cargan correctamente
- [ ] El total general muestra la suma correcta
- [ ] Editar un valor (cantidad, precio) recalcula correctamente

## Test 10: Validaciones del formulario

- [ ] Si dejo una fila con artículo pero sin cantidad → error visible
- [ ] Si dejo todas las filas vacías → error visible
- [ ] Mensajes de error están en español

---

## Comportamientos conocidos como BUG (no a preservar)

Lista de cosas que están raras o mal en el código actual pero **no son
features**. El refactor las arregla:

- `return precio_mayorista, precio_minorista` en `get_price_from_articulo_option`
  → en JS esto devuelve solo `precio_minorista`. Era una intención de devolver
  ambos pero está mal escrito. El refactor lo corrige devolviendo un objeto.
- Carga de Select2 desde CDN externo (`cdnjs.cloudflare.com`) era redundante
  y causaba race conditions. Ya removida.
- Múltiples definiciones de `handleSelectionChange` (3 versiones en el mismo
  archivo) — solo la última gana por hoisting de JS. El refactor lo deja una.

---

## Cómo usar este checklist

1. **Antes de empezar el refactor**: pasar este checklist contra HOY (con
   el JS viejo) y marcar lo que funciona. Eso fija el baseline.
2. **Después de cada commit del refactor**: volver a pasar el checklist.
   Si algo se rompió, **revertir** o arreglar antes de seguir.
3. **Al final del refactor**: el checklist debe pasar todo verde.
