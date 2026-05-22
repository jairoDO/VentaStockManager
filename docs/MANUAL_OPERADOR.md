# Manual del Operador — Golosinas Insa

Manual práctico para el día a día con el sistema. Si nunca lo abriste, empezá por la sección **1**.

> Para sacar capturas y reemplazar los placeholders, ver instrucciones al final del documento (sección "Cómo actualizar este manual").

---

## Índice

1. [Acceso al sistema](#1-acceso-al-sistema)
2. [Pantalla principal](#2-pantalla-principal)
3. [Artículos](#3-artículos)
4. [Clientes](#4-clientes)
5. [Hacer una venta nueva](#5-hacer-una-venta-nueva)
6. [Cuenta corriente y pagos](#6-cuenta-corriente-y-pagos)
7. [Listas de precios](#7-listas-de-precios)
8. [WhatsApp del negocio](#8-whatsapp-del-negocio)
9. [Configuración general](#9-configuración-general)
10. [Problemas frecuentes](#10-problemas-frecuentes)
11. [Glosario](#11-glosario)

---

## 1. Acceso al sistema

### URL

```
https://golosinas-insa.onrender.com/admin/
```

Guardala como favorito en el navegador del local.

### Cómo entrar

1. Abrí la URL en Chrome / Firefox / Safari.
2. Ingresá usuario y contraseña.
3. Click en **"Iniciar sesión"**.

![Captura: pantalla de login](capturas/01-login.png)

### Si te olvidaste la contraseña

Avisale al técnico (Jairo). Hay un mecanismo para resetear sin perder datos: cambia un campo en Render y al siguiente deploy se actualiza la contraseña.

---

## 2. Pantalla principal

Después de loguearte vas al "dashboard". Está dividido en secciones por app:

- **Artículo**: todo lo relacionado a los productos.
- **Cliente**: clientes, cuentas corrientes, listas de precios.
- **Venta**: ventas, pedidos, alertas de stock.
- **Configuración**: panel de control del sistema.
- **WA campaña**: WhatsApp (campañas, envíos).

![Captura: pantalla principal del admin](capturas/02-home-admin.png)

### Atajos arriba a la derecha

- **💬 WhatsApp** (solo superusuario): te lleva al panel de conexión del bot.
- **⚠ N alertas de stock**: aparece cuando hay ventas que se cargaron sin stock suficiente — hay que revisarlas.
- **📋 N pedidos de lista**: aparece cuando un cliente pidió la lista por WhatsApp pero todavía no tiene una asignada.

---

## 3. Artículos

### Ver todos los artículos (grilla)

Click en **"Artículos"** → vas a la **grilla** (tipo Excel): podés editar precios directamente en la tabla.

![Captura: grilla de artículos](capturas/03-grilla-articulos.png)

#### Cómo editar el precio

1. Click en la celda del precio (minorista o mayorista).
2. Escribir el nuevo valor.
3. Tab o Enter para confirmar.

> **Atención**: si el artículo tenía precios pactados con clientes, esos **se borran automáticamente** al cambiar el precio minorista. Es por seguridad: los acuerdos viejos quedarían stale con el precio nuevo.

#### Filtros disponibles

- Por **categoría** (arriba a la izquierda).
- Por **proveedor**.
- Buscar por **nombre / código**.

#### Crear artículo nuevo

1. Click en **"+ Nueva fila"** al final.
2. Tipeá nombre + precio minorista. El resto es opcional.
3. Tab para confirmar.

![Captura: nueva fila en grilla](capturas/04-grilla-nueva-fila.png)

### Categorías

Las categorías sirven para agrupar artículos. Click en **"Categorías"** desde el menú izquierdo.

![Captura: lista de categorías](capturas/05-categorias-lista.png)

#### Reglas de auto-asignación

Cuando creás una categoría, podés sumar **reglas** (palabras clave). Si un artículo contiene esas palabras en el nombre, se asigna automáticamente.

Ejemplo: categoría "Chocolates" con reglas `["chocolate", "alfajor", "bombón"]` → todos los artículos con esas palabras se categorizan solos.

![Captura: editar regla de categoría](capturas/06-categoria-regla.png)

---

## 4. Clientes

### Ver clientes

Click en **"Clientes"** → lista con saldo de cuenta corriente.

![Captura: lista de clientes](capturas/07-clientes-lista.png)

### Crear cliente nuevo

1. Click en **"AGREGAR CLIENTE"** arriba a la derecha.
2. Completar:
   - **Nombre, Apellido, Dirección, Teléfono**: obligatorios.
   - **Whatsapp number**: si lo dejás vacío y el teléfono es argentino, el sistema agrega el prefijo `549` automáticamente al guardar.
   - **Puede recibir WhatsApp**: tildá si el cliente aceptó recibir mensajes.
3. Save.

![Captura: form de cliente nuevo](capturas/08-cliente-form.png)

> **Importante**: por ley, **solo mandamos WhatsApp a clientes con `puede_recibir_whatsapp = True`**. Si no lo tildás, ese cliente NO va a estar en las campañas.

---

## 5. Hacer una venta nueva

Es la pantalla más usada. Click en **"+ Venta nueva"** en el menú lateral.

![Captura: pantalla de venta nueva vacía](capturas/09-venta-nueva-vacia.png)

### Paso 1 — Seleccionar cliente

Empezá tipeando el nombre o apellido. Aparece un dropdown con coincidencias.

![Captura: buscar cliente en venta](capturas/10-venta-buscar-cliente.png)

Cuando seleccionás el cliente:
- Si tiene **lista de precios activa**, aparece un banner verde proponiendo aplicar el descuento.
- Si tiene **saldo a favor**, aparece arriba.

### Paso 2 — Agregar artículos

En la primera fila, tipeá el nombre del producto. Dropdown con resultados.

![Captura: dropdown de artículos](capturas/11-venta-buscar-articulo.png)

Cada fila tiene:
- **Cantidad** (default 1)
- **Precio** (auto-completa según el cliente: minorista, mayorista o pactado)
- **Subtotal**

#### Precio pactado

Si el cliente tiene un precio pactado para ese artículo, aparece un badge **⭐ Pactado $X**. Click en el badge → aplica el precio pactado.

![Captura: badge de precio pactado](capturas/12-venta-badge-pactado.png)

#### Precio de lista

Si el cliente tiene una lista de precios que incluye ese artículo, aparece otro badge **📋 Lista $X**.

#### Atención: artículo duplicado

Si agregás el mismo artículo dos veces, el sistema te avisa con un modal:

> Artículo duplicado: "Marca — Chupetín" ya está cargado en la fila 1 con cantidad 3. ¿Sumar 2 → quedaría 5?

![Captura: modal artículo duplicado](capturas/13-venta-modal-dupe.png)

Click en **"Sí, sumar"** o **"No, cancelar"**.

### Paso 3 — Descuento global (opcional)

Si querés aplicar un descuento sobre toda la venta, scrolleá abajo y completá:
- **Descuento (%)**: número 0–100.
- **Motivo del descuento**: texto libre, opcional pero recomendado.

### Paso 4 — Pago (opcional)

Si el cliente paga al contado:
- Marcá **"Pagó al contado"**.
- Ingresar **monto pagado** (si fue distinto al total, ej. pagó de más → queda saldo a favor).

Si NO se marca pagó al contado, la diferencia queda como deuda en la cuenta corriente.

### Paso 5 — Guardar

Click en **"Guardar venta"** abajo a la derecha. Si todo está OK:
- Te avisa "Venta guardada".
- Te ofrece **descargar el comprobante PDF**.
- Te lleva al detalle de la venta.

![Captura: venta guardada exitosamente](capturas/14-venta-guardada.png)

---

## 6. Cuenta corriente y pagos

### Ver la cuenta de un cliente

Desde la lista de clientes, click en el saldo. O desde el menú: **Cliente → Cuentas corrientes**.

![Captura: cuenta corriente de un cliente](capturas/15-cuenta-corriente.png)

Vas a ver:
- **Saldo actual**: positivo = a favor del cliente. Negativo = el cliente debe.
- **Movimientos**: timeline read-only de ventas, pagos, ajustes.
- **Dos botones arriba**:
  - 💰 **Registrar pago** (verde): cuando el cliente paga deuda.
  - 💵 **Registrar saldo a favor** (azul): cuando el cliente adelanta plata.

### Registrar un pago

1. Click en **"💰 Registrar pago"**.
2. Ingresar:
   - **Cliente**: pre-completado.
   - **Monto pagado**: positivo, ej. 5000.
   - **Nota (opcional)**: "Efectivo del 22/05", "Transferencia BBVA", etc.
3. Save.

![Captura: form de registrar pago](capturas/16-registrar-pago.png)

> **Importante**: los movimientos NO se pueden editar ni borrar una vez creados. Si hay un error, hay que cargar otro movimiento opuesto (ej. un pago negativo de la misma magnitud). Esto preserva la auditoría del saldo.

### Registrar saldo a favor

Cuando el cliente trae plata sin tener deuda (adelantando para próximas compras):

1. Click en **"💵 Registrar saldo a favor"**.
2. Ingresar **monto adelantado**.
3. Save.

El resultado es igual a un pago, pero con etiqueta distinta para tu memoria.

---

## 7. Listas de precios

Las listas de precios son una herramienta para mandar al cliente un listado personalizado de productos con sus precios, descuentos especiales, etc. Se mandan por WhatsApp como link, PDF o texto.

### Crear una lista nueva

1. Menú lateral → **"Listas de precios"** o **"+ Nueva lista"**.
2. Seleccionar el cliente.
3. Ponerle un nombre interno (ej. "Lista marzo Pérez").
4. Configurar **ajuste global** (opcional):
   - **Descuento (%)** o **Aumento (%)**: 0–100.
   - **Motivo**: texto libre.
5. Agregar artículos (uno por uno o "todos los de tal categoría").
6. **Guardar**.

![Captura: editor de lista de precios](capturas/17-lista-editor.png)

### Compartir la lista por link

Una vez guardada, click en **"📤 Compartir"** → genera un link público.

![Captura: botón compartir lista](capturas/18-lista-compartir.png)

El link incluye:
- **Token UUID único** (no se puede adivinar).
- **Expiración** automática (por default a 7 días, configurable).

Podés copiar el link y mandárselo al cliente por cualquier canal. **NO uses WhatsApp manualmente — usá la difusión integrada (siguiente sección)**.

### Difundir la lista por WhatsApp

Click en **"📨 Difundir"** en el editor de lista.

![Captura: pantalla de difundir](capturas/19-lista-difundir.png)

Pasos:
1. **Seleccionar clientes** a los que les vas a mandar.
2. **Elegir modo**:
   - **Texto**: el contenido de la lista en el mensaje (recomendado — sin clicks extra para el cliente).
   - **Link**: solo el link a la lista web.
   - **PDF**: archivo adjunto.
   - **Ambos**: PDF + link.
3. Click en **"Enviar a N clientes"**.

El sistema encola los envíos y los procesa con un delay de 4 segundos entre cada uno (para no levantar sospechas en WhatsApp).

### Comportamiento clave: los precios NO se "congelan"

Cuando creás una lista, **NO se guarda una foto del precio** del artículo. Cada vez que se rinde el PDF o el cliente abre el link, se calcula el precio **al momento** desde:
1. Precio pactado (si existe), o
2. Precio minorista del artículo, +
3. Aplicar el descuento/aumento de la lista.

> **Consecuencia**: si subís el precio de un artículo después de mandar la lista, el cliente que abre el link después ve el precio nuevo, no el del momento del share.

---

## 8. WhatsApp del negocio

### Vincular el bot

1. Atajo **💬 WhatsApp** arriba a la derecha del admin (solo superusuario).
2. O directo: `https://golosinas-insa.onrender.com/wa-campania/conexion/`.

![Captura: panel de conexión WhatsApp](capturas/20-wa-panel.png)

Si NO está vinculado:
- Aparece un **QR**.
- Abrí WhatsApp en el celular del negocio → Ajustes → Dispositivos vinculados → Vincular un dispositivo.
- Escaneá el QR.
- Al rato el panel cambia a estado **"Conectado"**.

![Captura: WhatsApp conectado](capturas/21-wa-conectado.png)

### Mandar mensaje de prueba

Botón **"Mandar prueba"** → ingresar un número → te llega un mensaje de test. Útil para verificar que el bot responde.

### Auto-responder

Si está habilitado (Configuración general → Auto-responder), cuando un cliente escribe al WhatsApp del negocio:

- "lista" → si tiene una lista asignada, le manda el link. Si no, se crea una **solicitud de lista** que aparece en el badge del header.
- "saldo" → le manda su saldo actual en cuenta corriente.

---

## 9. Configuración general

Menú lateral → **"Configuración general"**. Es una pantalla única con varios fieldsets.

![Captura: configuración general](capturas/22-config-general.png)

### Lo más usado

| Sección | Para qué sirve |
|---|---|
| **Sheets sync** | Habilitar / deshabilitar la sincronización con Google Sheets. |
| **Retención de ventas** | Cuántos meses de ventas se mantienen "activas" antes de archivar. |
| **Recordatorios de saldo** | Habilitar mensajes automáticos de WhatsApp para clientes con deuda vieja. |
| **Formato default lista de precios** | Si no se elige modo al difundir, qué usar por default. |
| **Auto-responder habilitado** | ON/OFF del bot que responde "lista" y "saldo". |

---

## 10. Problemas frecuentes

### "Hubo problemas al guardar: CuentaCliente matching query does not exist"

✅ **Ya arreglado** en versiones recientes. La cuenta se crea automáticamente al guardar la primera venta del cliente.

### "El vendedor no se selecciona automáticamente"

El sistema busca un `Vendedor` cuyo campo `usuario` apunte al user logueado. Si no encuentra, no autocompleta.

**Fix**: andar a `/admin/vendedor/vendedor/` y editar el vendedor que te corresponda → setear el campo "Usuario" a tu user → Save. Próximas ventas se autocompletan.

### "La venta dice 'stock insuficiente'"

La venta se guarda igual (no es bloqueante). Pero se genera una **alerta de stock** que aparece en el badge del header. Cuando puedas reponer, marcala como revisada en `/admin/venta/alertastock/`.

### "El bot dice 'desconectado' / no manda mensajes"

1. Andá a `/wa-campania/conexion/`.
2. Si te muestra QR, hay que escanear de nuevo.
3. Si dice "conectado" pero los mensajes fallan: probá **"Reiniciar bot"**.
4. Si nada funciona: avisale al técnico.

### "Necesito acceso a la app rápido pero todo está lento"

La DB está en otra región (US East 1) mientras el servidor está en Oregon. Latencia ~70ms por query. Si Osvaldo nota mucho la lentitud, está pendiente migrar la DB a Oregon (mejora ~10x).

### "Me cerró sesión sin avisar"

Si pasa después de un deploy reciente, es esperable (un cambio de SECRET_KEY invalida sesiones). En operación normal NO debería pasar. Si pasa frecuentemente, avisar al técnico.

---

## 11. Glosario

- **Articulo**: producto del kiosko.
- **Cliente**: persona o empresa que compra. Tiene **cuenta corriente** automática.
- **Vendedor**: usuario que carga ventas. Asociado a un `auth.User`.
- **Venta**: una transacción. Tiene varios `ItemDeVenta`.
- **Pedido**: parecido a venta pero sin entregar todavía (queda pendiente).
- **PrecioCliente**: precio pactado a mano para un par cliente+articulo. Se invalida si sube el precio minorista del articulo.
- **Lista de precios**: conjunto de artículos para mandar al cliente, con ajuste % global opcional.
- **Cuenta corriente**: estado de la deuda/saldo del cliente. Suma de todos sus `MovimientoCuenta`.
- **Movimiento de cuenta**: una línea en la cuenta corriente. Tipo: venta a cuenta, pago, aplicación saldo, excedente, ajuste.
- **Auditlog**: registro histórico de todas las creaciones/modificaciones/borrados. Útil para "¿qué pasó con este registro?".
- **Bot / WA / WhatsApp**: el servicio Node.js que se conecta a WhatsApp y manda mensajes en nombre del negocio.

---

## Cómo actualizar este manual

### Sacar capturas

1. Mientras usás el sistema, cuando estés en una pantalla relevante, sacá screenshot:
   - **Mac**: Cmd + Shift + 4 (área) o Cmd + Shift + 3 (pantalla completa).
   - **Windows**: tecla "PrtSc" o usar "Recortes".
2. Renombrá el archivo con el número que corresponde (ver placeholders arriba: `01-login.png`, `02-home-admin.png`, etc.).
3. Guardalo en `docs/capturas/` del repo.

### Editar el texto

El archivo es `docs/MANUAL_OPERADOR.md`. Está en formato **Markdown**:

- `# Título`, `## Subtítulo`, `### Sub-subtítulo`
- `**negrita**`, `*itálica*`
- Listas con `-` o `1.`
- Tablas con `|`
- Imágenes con `![alt](capturas/archivo.png)`

### Versionarlo

Después de editar:

```bash
cd /Users/jairo/Documents/abstract/VentaStockManager-mejoras
git add docs/
git commit -m "docs: update manual operador"
git push origin feat/articulo-mejoras-mayo
```

---

**Última actualización**: 2026-05-22
**Versión del sistema cubierta**: post-migración a Render (golosinas-insa.onrender.com)
**Contacto técnico**: Jairo Ordoñez
