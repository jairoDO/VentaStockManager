"""
Pantalla custom de Venta (reemplaza la inline del admin).

La pantalla del admin con TabularInline + autocomplete-light estaba
acumulando deuda: extra=12 filas vacías por defecto, lógica de stock
buggy en edits, JS de precio frágil pisado por select2, sin cálculos
en vivo, sin descuentos. En vez de seguir parcheándola, hicimos una
pantalla aparte en Alpine + Tailwind (CDN, sin build step) que habla
con el backend por JSON. El admin se queda solo con list/filtros y
PDF — esa parte ya funciona y no vale la pena reescribirla.

Las vistas API devuelven Decimals como string. Es a propósito: si
los serializamos como float perdemos precisión en cálculos
financieros (0.1 + 0.2 = 0.30000000000000004). Alpine los maneja
como string y para multiplicar/sumar los pasamos a Number en el
template; eso introduce error de punto flotante en el render pero
el cálculo CANÓNICO sigue siendo el del backend al guardar.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from articulo.models import Articulo
from cliente.models import Cliente, CuentaCliente, MovimientoCuenta, PrecioCliente
from vendedor.models import Vendedor
from venta.models import AlertaStock, ArticuloVenta, Venta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vendedor_for_user(user):
    """
    Devuelve el id del Vendedor asociado al User logueado, o None.

    El admin original hacía `get_or_create` y eso terminaba creando
    vendedores fantasmas (uno por cada admin que abría la pantalla,
    aunque nunca cargara una venta). Acá solo leemos: si el user no
    tiene Vendedor, el form muestra el select abierto y el operador
    elige uno manualmente. Crear Vendedores es una acción explícita.
    """
    if not user or not user.is_authenticated:
        return None
    vendedor = Vendedor.objects.filter(usuario=user).only('id').first()
    return vendedor.id if vendedor else None


def _decimal_or_400(raw, *, field, errores, allow_negative=False):
    """
    Parsea un input a Decimal, agregando el error a `errores` si falla.

    Devolvemos None ante error para que el caller decida si abortar o
    seguir validando otros campos (preferimos mostrarle TODOS los
    errores al operador de una, no uno a uno).
    """
    if raw is None or raw == '':
        return Decimal('0')
    try:
        valor = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        errores.append(f'{field}: valor inválido ({raw!r})')
        return None
    if not allow_negative and valor < 0:
        errores.append(f'{field}: no puede ser negativo')
        return None
    return valor


def _precio_sugerido(articulo: Articulo, cantidad: int) -> Decimal:
    """
    Precio "de lista" según cantidad (minorista vs mayorista).
    NO considera precio pactado por cliente — eso lo resuelve
    `_articulo_to_dict` recibiendo un PrecioCliente como parámetro.

    Si la cantidad iguala o supera el umbral, aplica mayorista. Si
    alguno de los dos precios está en None (puede pasar con datos
    viejos), fallback al otro.
    """
    umbral = articulo.cantidad_por_mayor or 0
    mayorista = articulo.precio_mayorista
    minorista = articulo.precio_minorista
    if cantidad >= umbral and umbral > 0 and mayorista is not None:
        return mayorista
    if minorista is not None:
        return minorista
    return mayorista or Decimal('0')


def _total_venta(venta: Venta) -> Decimal:
    """
    Calcula el total final de una Venta respetando descuentos.
    Wrapper sobre `venta.utils.total_venta` para mantener el nombre
    histórico que ya usaban los callers de este módulo.
    """
    from venta.utils import total_venta as _calc
    return _calc(venta)


def _articulo_to_dict(
    articulo: Articulo,
    cantidad: int,
    pactado: PrecioCliente | None = None,
    precio_lista: dict | None = None,
) -> dict:
    """
    Serializa un Articulo para el JSON del autocomplete.

    Tres precios coexisten en la respuesta (todos como string para no
    perder decimales en el round-trip a JS):

      - `precio_sugerido`: lo que se carga en el input al elegir. Es
        SIEMPRE el precio normal (minorista/mayorista según cantidad).
        Pactado y lista NO auto-cargan — son badges clickeables.

      - `precio_pactado` + `tiene_precio_pactado`: si hay PrecioCliente
        para este par (cliente, artículo). El operador puede hacer
        click en el badge para pisar el sugerido.

      - `precio_lista` + `lista_nombre`: si el artículo está en alguna
        ListaPrecios del cliente. Es el precio EFECTIVO ya con el
        ajuste de la lista aplicado. Mismo patrón: badge clickeable.

    Si coinciden pactado + lista, el front los muestra ambos (badges
    distintos) y el operador elige cuál aplicar.
    """
    precio_default = _precio_sugerido(articulo, cantidad)
    precio_sugerido = precio_default
    tiene_pactado = pactado is not None
    precio_pactado_val = str(pactado.precio_unitario) if pactado is not None else ''

    # Info del precio de lista (si existe). precio_lista viene
    # pre-calculado desde el caller con el ajuste aplicado.
    precio_lista_val = ''
    lista_nombre_val = ''
    if precio_lista:
        precio_lista_val = str(precio_lista.get('precio', ''))
        lista_nombre_val = precio_lista.get('lista_nombre', '')

    return {
        'id': articulo.id,
        'codigo': articulo.codigo or '',
        'codigo_interno': articulo.codigo_interno or '',
        'marca': articulo.marca or '',
        'nombre': articulo.nombre or '',
        'precio_minorista': str(articulo.precio_minorista or 0),
        'precio_mayorista': str(articulo.precio_mayorista or 0),
        # Mantenemos `umbral` como alias semántico en el JSON aunque
        # el campo del modelo es `cantidad_por_mayor`. Es el contrato
        # que pide el front; cambiarlo en el modelo es harina de otro
        # costal.
        'umbral': articulo.cantidad_por_mayor or 0,
        'stock': articulo.stock,
        'precio_sugerido': str(precio_sugerido),
        # Pactado: badge clickeable, NO auto-carga.
        'tiene_precio_pactado': tiene_pactado,
        'precio_pactado': precio_pactado_val,
        # Lista de precios del cliente: badge clickeable, NO auto-carga.
        'tiene_precio_lista': bool(precio_lista),
        'precio_lista': precio_lista_val,
        'lista_nombre': lista_nombre_val,
        # Legacy: el front viejo usaba esto para "lista $X" — ahora
        # coincide con precio_sugerido. Mantengo el nombre por compat.
        'precio_default_sin_acuerdo': str(precio_default),
    }


# ---------------------------------------------------------------------------
# Vistas API
# ---------------------------------------------------------------------------

@login_required
@require_GET
def api_articulos_buscar(request):
    """
    Búsqueda de artículos por nombre/código/marca para el autocomplete.

    Filtramos `stock > 0` porque la nueva pantalla es para cargar
    ventas reales y no queremos permitir vender lo que no hay. Si en
    el futuro queremos backorder, este filtro se relaja acá.

    Param opcional `cliente_id`: si viene, para cada artículo
    consultamos si hay un PrecioCliente y, en caso afirmativo, el
    `precio_sugerido` que devolvemos es el pactado (en vez del
    minorista/mayorista). El front muestra un badge "precio pactado"
    sobre esos resultados para que el operador se entere.
    """
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    try:
        cantidad = int(request.GET.get('cantidad') or '1')
    except ValueError:
        cantidad = 1
    if cantidad < 1:
        cantidad = 1

    try:
        cliente_id = int(request.GET.get('cliente_id') or '0')
    except ValueError:
        cliente_id = 0

    qs = (
        Articulo.objects
        .filter(stock__gt=0)
        .filter(
            Q(nombre__icontains=q)
            | Q(codigo__icontains=q)
            | Q(codigo_interno__icontains=q)
            | Q(marca__icontains=q)
        )
        .order_by('codigo')[:20]
    )
    articulos = list(qs)

    # Si tenemos cliente, traemos en un solo query todos los pactados
    # que apliquen a los artículos del result set (evita N+1 cuando el
    # cliente tiene muchos precios acordados).
    pactados_map: dict[int, PrecioCliente] = {}
    if cliente_id and articulos:
        pactados_qs = PrecioCliente.objects.filter(
            cliente_id=cliente_id,
            articulo_id__in=[a.id for a in articulos],
        )
        pactados_map = {p.articulo_id: p for p in pactados_qs}

    # Además del pactado, buscamos si el cliente tiene listas de
    # precios y si alguno de los artículos del result set está en
    # esas listas. Si lo está, calculamos el precio efectivo (con el
    # ajuste de la lista) y lo serializamos como un badge clickeable
    # paralelo al pactado.
    #
    # Decisión: si el cliente tiene MÚLTIPLES listas, usamos la más
    # reciente (`updated_at` desc). Razón: lo más probable es que la
    # última editada sea la "vigente". Si en el futuro queremos elegir
    # entre varias, el back ya tiene la info — solo cambia la UI.
    lista_info_map: dict[int, dict] = {}
    if cliente_id and articulos:
        try:
            from articulo.models import ListaPrecios, ListaPreciosItem
            from articulo.precios import precio_efectivo, cargar_precios_pactados

            lista_actual = (
                ListaPrecios.objects
                .filter(cliente_id=cliente_id)
                .order_by('-updated_at')
                .first()
            )
            if lista_actual:
                articulo_ids = [a.id for a in articulos]
                items_en_lista = ListaPreciosItem.objects.filter(
                    lista=lista_actual,
                    articulo_id__in=articulo_ids,
                ).values_list('articulo_id', flat=True)
                articulos_en_lista_ids = set(items_en_lista)
                if articulos_en_lista_ids:
                    # Calculamos el precio efectivo de cada artículo
                    # que está en la lista — aplicando el ajuste de
                    # la lista (descuento o aumento). Reusamos el
                    # helper canónico `precio_efectivo` (mismo que el
                    # PDF y la pantalla de listas).
                    cliente_obj = Cliente.objects.get(pk=cliente_id)
                    articulos_en_lista = [
                        a for a in articulos if a.id in articulos_en_lista_ids
                    ]
                    pact_map_para_lista = cargar_precios_pactados(
                        cliente_obj, articulos_en_lista,
                    )
                    for a in articulos_en_lista:
                        p = precio_efectivo(
                            a,
                            cliente_obj,
                            descuento_lista=lista_actual.descuento_porcentaje,
                            precios_pactados_map=pact_map_para_lista,
                            tipo_ajuste=lista_actual.tipo_ajuste,
                        )
                        lista_info_map[a.id] = {
                            'precio': p,
                            'lista_nombre': lista_actual.nombre,
                        }
        except Exception:
            # Defensivo: si articulo no está instalado o algo falla en
            # la integración, no rompemos el autocomplete — solo
            # perdemos el badge de lista para esta búsqueda.
            import logging
            logging.getLogger(__name__).exception(
                'Cargar precio de lista para autocomplete falló'
            )

    return JsonResponse({
        'results': [
            _articulo_to_dict(
                a,
                cantidad,
                pactados_map.get(a.id),
                lista_info_map.get(a.id),
            )
            for a in articulos
        ],
    })


@login_required
@require_GET
def api_clientes_buscar(request):
    """
    Búsqueda de clientes por nombre/apellido/dirección.

    Incluimos `saldo` en la respuesta para que el front no tenga que
    hacer un segundo fetch — la pantalla nueva muestra el saldo en
    cuanto el operador hace click en un resultado.
    """
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    qs = (
        Cliente.objects
        .select_related('cuenta')
        .filter(
            Q(nombre__icontains=q)
            | Q(apellido__icontains=q)
            | Q(direccion__icontains=q)
        )
        .order_by('nombre', 'apellido')[:20]
    )

    results = [{
        'id': c.id,
        'nombre': c.nombre_completo(),
        'direccion': c.direccion or '',
        'telefono': c.telefono or '',
        'saldo': str(c.saldo),
    } for c in qs]
    return JsonResponse({'results': results})


def _listas_activas_de_cliente(cliente, max_resultados: int = 3) -> list[dict]:
    """
    Devuelve hasta `max_resultados` listas de precios del cliente que
    sean "aplicables" a una venta. Criterio:
      - Tienen al menos 1 item (sino no es realmente una lista).
      - Tienen un `descuento_porcentaje > 0` (sino no aporta nada
        al flujo de venta — el operador no necesita aplicar 0%).
      - Ordenadas por updated_at desc (las más nuevas arriba).

    El front muestra estas listas como botones en un banner; el
    operador decide cuál aplicar (no automático).
    """
    from articulo.models import ListaPrecios
    from django.db.models import Count

    qs = (
        ListaPrecios.objects
        .filter(cliente=cliente, descuento_porcentaje__gt=0)
        .annotate(_n_items=Count('items'))
        .filter(_n_items__gt=0)
        .order_by('-updated_at')[:max_resultados]
    )
    return [
        {
            'id': l.id,
            'nombre': l.nombre,
            'descuento_porcentaje': str(l.descuento_porcentaje),
            'descuento_motivo': l.descuento_motivo,
            'n_items': l._n_items,
        }
        for l in qs
    ]


@login_required
@require_GET
def api_cliente_saldo(request, cliente_id):
    """
    Devuelve el saldo actual del cliente + sus listas vigentes con
    descuento.

    Útil cuando el front quiere refrescar el saldo sin re-buscar (por
    ejemplo después de guardar una venta, o si en edición querés ver
    el saldo "antes" de ese pago). El endpoint de búsqueda ya devuelve
    saldo, pero este permite consultarlo por ID directo sin filtrar.

    `listas_activas` permite que el front muestre un banner ofreciendo
    aplicar el descuento de alguna lista a la venta actual (decisión
    explícita del operador, no automática).
    """
    cliente = get_object_or_404(
        Cliente.objects.select_related('cuenta'),
        pk=cliente_id,
    )
    return JsonResponse({
        'cliente_id': cliente.id,
        'nombre': cliente.nombre_completo(),
        'saldo': str(cliente.saldo),
        'listas_activas': _listas_activas_de_cliente(cliente),
    })


@login_required
@require_POST
def api_venta_guardar(request):
    """
    Crea o actualiza una Venta y sus ArticuloVenta.

    Reglas que esta vista garantiza (y que la pantalla del admin NO
    garantizaba):
      - Atomicidad: si una línea explota, no queda media venta cargada.
      - Stock coherente en EDITS: si cambia la cantidad de una línea
        existente, primero devolvemos el stock viejo y después
        descontamos el nuevo. Eso es un bug del save() del modelo,
        que descuenta sin importar si es create o update.
      - precio_decimal queda llenado siempre con el Decimal validado,
        no parseando el CharField después.
    """
    try:
        payload = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'errores': ['JSON inválido']}, status=400)

    errores: list[str] = []

    venta_id = payload.get('id')
    cliente_id = payload.get('cliente_id')
    vendedor_id = payload.get('vendedor_id') or _vendedor_for_user(request.user)
    fecha_compra = payload.get('fecha_compra')
    fecha_entrega = payload.get('fecha_entrega')
    items = payload.get('items') or []

    descuento_pct = _decimal_or_400(
        payload.get('descuento_porcentaje'),
        field='descuento_porcentaje',
        errores=errores,
    )
    if descuento_pct is not None and (descuento_pct < 0 or descuento_pct > 100):
        errores.append('descuento_porcentaje: debe estar entre 0 y 100')
        descuento_pct = None

    descuento_motivo = (payload.get('descuento_motivo') or '').strip()

    # Cuenta corriente. Campos opcionales:
    #   - aplicar_saldo: bool. Si True, descontamos `saldo_a_aplicar`
    #     del total de la venta (creando un MovimientoCuenta negativo).
    #   - saldo_a_aplicar: Decimal. Cuánto saldo a favor consumir.
    #     Tiene que ser <= saldo_actual del cliente.
    #   - monto_pagado: Decimal. Lo que el cliente entregó en efectivo.
    #     Si es menor al total efectivo (total - saldo aplicado), la
    #     diferencia queda como `venta_a_cuenta` (deuda). Si es mayor,
    #     el excedente queda como `excedente_venta` (saldo a favor).
    aplicar_saldo = bool(payload.get('aplicar_saldo'))
    saldo_a_aplicar = _decimal_or_400(
        payload.get('saldo_a_aplicar'),
        field='saldo_a_aplicar',
        errores=errores,
    )
    monto_pagado = _decimal_or_400(
        payload.get('monto_pagado'),
        field='monto_pagado',
        errores=errores,
    )

    if not cliente_id:
        errores.append('cliente_id es requerido')
    if not vendedor_id:
        errores.append('vendedor_id es requerido (y el usuario no tiene Vendedor asociado)')
    if not fecha_compra:
        errores.append('fecha_compra es requerida')
    if not fecha_entrega:
        errores.append('fecha_entrega es requerida')
    if not items:
        errores.append('La venta debe tener al menos un ítem')

    # Validamos cliente/vendedor existen ANTES de entrar a la
    # transacción, para devolver el 400 más informativo posible.
    cliente = None
    vendedor = None
    if cliente_id:
        cliente = Cliente.objects.filter(pk=cliente_id).first()
        if not cliente:
            errores.append(f'cliente_id={cliente_id} no existe')
    if vendedor_id:
        vendedor = Vendedor.objects.filter(pk=vendedor_id).first()
        if not vendedor:
            errores.append(f'vendedor_id={vendedor_id} no existe')

    # Primer paso: validar la estructura de cada item. No tocamos
    # stock todavía — solo armamos un plan limpio. Si algo falla acá
    # devolvemos 400 sin haber escrito nada en la DB.
    items_plan = []
    for idx, item in enumerate(items):
        if item.get('_delete'):
            iid = item.get('id')
            if not iid:
                errores.append(f'item[{idx}]: _delete requiere id')
                continue
            items_plan.append({'_delete': True, 'id': iid})
            continue

        articulo_id = item.get('articulo_id')
        cantidad_raw = item.get('cantidad')
        precio_raw = item.get('precio')
        descuento_item_raw = item.get('descuento_porcentaje', 0)

        if not articulo_id:
            errores.append(f'item[{idx}]: articulo_id es requerido')
            continue
        try:
            cantidad = int(cantidad_raw)
        except (TypeError, ValueError):
            errores.append(f'item[{idx}]: cantidad inválida ({cantidad_raw!r})')
            continue
        if cantidad <= 0:
            errores.append(f'item[{idx}]: cantidad debe ser > 0')
            continue
        precio = _decimal_or_400(precio_raw, field=f'item[{idx}].precio', errores=errores)
        if precio is None:
            continue
        descuento_item = _decimal_or_400(
            descuento_item_raw, field=f'item[{idx}].descuento_porcentaje', errores=errores,
        )
        if descuento_item is None:
            continue
        if descuento_item < 0 or descuento_item > 100:
            errores.append(f'item[{idx}].descuento_porcentaje: debe estar entre 0 y 100')
            continue

        items_plan.append({
            '_delete': False,
            'id': item.get('id'),
            'articulo_id': articulo_id,
            'cantidad': cantidad,
            'precio': precio,
            'descuento_porcentaje': descuento_item,
        })

    if errores:
        return JsonResponse({'ok': False, 'errores': errores}, status=400)

    # A partir de acá ya validamos la forma. Toda la mutación pasa
    # adentro de una transacción para que stock y filas queden
    # consistentes incluso si algo explota en el medio.
    try:
        with transaction.atomic():
            if venta_id:
                venta = (
                    Venta.objects
                    .select_for_update()
                    .get(pk=venta_id)
                )
                venta.cliente = cliente
                venta.vendedor = vendedor
                venta.fecha_compra = fecha_compra
                venta.fecha_entrega = fecha_entrega
                venta.descuento_porcentaje = descuento_pct or Decimal('0')
                venta.descuento_motivo = descuento_motivo
                venta.save()
            else:
                venta = Venta.objects.create(
                    cliente=cliente,
                    vendedor=vendedor,
                    fecha_compra=fecha_compra,
                    fecha_entrega=fecha_entrega,
                    descuento_porcentaje=descuento_pct or Decimal('0'),
                    descuento_motivo=descuento_motivo,
                )

            # Index de items existentes para resolver updates/deletes
            # con un solo query, en vez de uno por línea.
            existentes = {
                av.id: av
                for av in ArticuloVenta.objects.select_for_update().filter(venta=venta)
            }

            # Avisos sobre stock que NO frenan la venta. La filosofía
            # acá es: el stock del sistema vs la realidad puede estar
            # desfasado (mercadería que llegó sin cargarse, robos no
            # registrados, ventas previas no descontadas, etc.). En un
            # kiosco vale más permitir cargar la venta real y avisar
            # del desfasaje que bloquear la operación.
            #
            # El save() del modelo clampea el stock a 0 si bajaría a
            # negativo. La venta se guarda completa. Además, cada
            # warning genera un `AlertaStock` persistente para que
            # la administración pueda investigar después en una
            # bandeja de entrada propia (`/admin/venta/alertastock/`).
            warnings_list: list[str] = []
            # Diferimos la creación de AlertaStock hasta tener el
            # `venta.pk`. Si es create, el venta ya tiene PK; si es
            # edit también. Pero algunos chequeos de stock se hacen
            # ANTES de crear el ArticuloVenta nuevo, así que vamos
            # acumulando la info y creamos al final del loop.
            alertas_pendientes: list[dict] = []

            def _aviso_stock(articulo, faltante: int, cantidad_pedida: int):
                stock_actual = articulo.stock or 0
                warnings_list.append(
                    f'Stock insuficiente para "{articulo.nombre}": '
                    f'disponible {stock_actual}, faltaban {faltante}. '
                    f'La venta se guardó igual y el stock quedó en 0. '
                    f'Revisá la mercadería real.'
                )
                alertas_pendientes.append({
                    'articulo': articulo,
                    'cantidad_pedida': cantidad_pedida,
                    'stock_disponible_al_momento': stock_actual,
                    'cantidad_faltante': faltante,
                })

            for plan in items_plan:
                if plan['_delete']:
                    av = existentes.get(plan['id'])
                    if not av:
                        # El cliente nos pidió borrar algo que no es
                        # de esta venta. No fallamos — lo ignoramos.
                        # (Podría ser una doble-submit.)
                        continue
                    # El signal `pre_delete` de ArticuloVenta (en
                    # venta/signals.py) devuelve el stock automáticamente.
                    # NO toques stock acá: el signal lo hace.
                    av.delete()
                    continue

                articulo = Articulo.objects.select_for_update().get(pk=plan['articulo_id'])

                if plan['id'] and plan['id'] in existentes:
                    # Update de línea existente. El save() del modelo
                    # ajusta stock con el delta entre cantidad nueva y
                    # vieja (incluido cambio de artículo). Acá solo
                    # AVISAMOS si no alcanza — no bloqueamos.
                    av = existentes[plan['id']]
                    if av.articulo_id != articulo.id:
                        # Cambio de artículo: vamos a descontar la
                        # cantidad entera del nuevo artículo.
                        if (articulo.stock or 0) < plan['cantidad']:
                            _aviso_stock(
                                articulo,
                                plan['cantidad'] - (articulo.stock or 0),
                                plan['cantidad'],
                            )
                    else:
                        # Mismo artículo: solo importa el delta.
                        delta = plan['cantidad'] - av.cantidad
                        if delta > 0 and (articulo.stock or 0) < delta:
                            _aviso_stock(
                                articulo,
                                delta - (articulo.stock or 0),
                                delta,
                            )

                    av.articulo = articulo
                    av.cantidad = plan['cantidad']
                    av.precio = str(plan['precio'])
                    av.precio_decimal = plan['precio']
                    av.descuento_porcentaje = plan['descuento_porcentaje']
                    # `save()` sin update_fields para que el override
                    # del modelo corra y ajuste stock con el delta.
                    av.save()
                else:
                    # Línea nueva. El save() del modelo descuenta
                    # stock. Avisamos si no alcanza, no bloqueamos.
                    if (articulo.stock or 0) < plan['cantidad']:
                        _aviso_stock(
                            articulo,
                            plan['cantidad'] - (articulo.stock or 0),
                            plan['cantidad'],
                        )
                    ArticuloVenta.objects.create(
                        venta=venta,
                        articulo=articulo,
                        cantidad=plan['cantidad'],
                        precio=str(plan['precio']),
                        precio_decimal=plan['precio'],
                        descuento_porcentaje=plan['descuento_porcentaje'],
                    )

                # Upsert PrecioCliente. Cada vez que el operador
                # confirma una línea con un precio distinto al
                # sugerido_default (minorista/mayorista), interpretamos
                # que cerró un acuerdo de precio con este cliente y lo
                # persistimos para próximas ventas.
                #
                # Lo hacemos para creates Y updates (si el operador
                # cambia el precio en un edit, también cuenta como
                # "ratificación" del acuerdo). Para el caso de borrado
                # de línea no tocamos nada — borrar una línea no
                # significa olvidar el acuerdo.
                precio_default = _precio_sugerido(articulo, plan['cantidad'])
                if plan['precio'] != precio_default and cliente is not None:
                    PrecioCliente.objects.update_or_create(
                        cliente=cliente,
                        articulo=articulo,
                        defaults={
                            'precio_unitario': plan['precio'],
                            'venta_origen': venta,
                            'creado_por': request.user if request.user.is_authenticated else None,
                        },
                    )

            # Cuenta corriente. Calculamos el total real de la venta
            # leyendo las líneas que acabamos de persistir, así no
            # confiamos en lo que mandó el front (puede haber dropeado
            # algún item o cambiado precios). El total = sum(cant *
            # precio * (1 - desc_linea)) * (1 - desc_global).
            #
            # Solo creamos movimientos en CREATES por ahora. En edits
            # los movimientos viejos (de la venta original) se preservan
            # — escalar a "rehacer movimientos en edits" requiere más
            # cuidado (¿cómo se reconcilia con pagos posteriores?). Si
            # el operador necesita ajustar, lo hace desde el admin de
            # MovimientoCuenta a mano.
            es_create = not venta_id
            if es_create:
                total_venta = _total_venta(venta)

                # Aplicación de saldo a favor. Validamos contra el saldo
                # REAL del cliente al momento del guardado para evitar
                # double-spend si dos ventas se guardan en paralelo.
                cuenta = CuentaCliente.objects.select_for_update().get(cliente=cliente)
                saldo_actual = cuenta.saldo

                aplicado = Decimal('0')
                if aplicar_saldo and saldo_a_aplicar and saldo_a_aplicar > 0:
                    if saldo_a_aplicar > saldo_actual:
                        raise ValueError(
                            f'Saldo insuficiente: el cliente tiene {saldo_actual} '
                            f'pero se intenta aplicar {saldo_a_aplicar}'
                        )
                    aplicado = saldo_a_aplicar
                    MovimientoCuenta.objects.create(
                        cuenta=cuenta,
                        tipo=MovimientoCuenta.TIPO_APLICACION_SALDO,
                        monto=-aplicado,
                        venta=venta,
                        descripcion=f'Aplicado a venta #{venta.id}',
                        creado_por=request.user if request.user.is_authenticated else None,
                    )

                # Diferencia entre lo que el cliente debía pagar y lo
                # que efectivamente trajo. Si no manda monto_pagado,
                # asumimos pagó el total efectivo (venta paga al contado).
                total_a_cobrar = total_venta - aplicado
                if monto_pagado is None:
                    monto_pagado = total_a_cobrar

                diferencia = monto_pagado - total_a_cobrar
                if diferencia < 0:
                    # Pagó de menos: la diferencia queda como deuda.
                    MovimientoCuenta.objects.create(
                        cuenta=cuenta,
                        tipo=MovimientoCuenta.TIPO_VENTA_A_CUENTA,
                        monto=diferencia,  # ya viene negativo
                        venta=venta,
                        descripcion=(
                            f'Venta #{venta.id}: total {total_venta}, '
                            f'aplicado saldo {aplicado}, pagó {monto_pagado}'
                        ),
                        creado_por=request.user if request.user.is_authenticated else None,
                    )
                elif diferencia > 0:
                    # Pagó de más: el excedente queda a favor.
                    MovimientoCuenta.objects.create(
                        cuenta=cuenta,
                        tipo=MovimientoCuenta.TIPO_EXCEDENTE,
                        monto=diferencia,
                        venta=venta,
                        descripcion=(
                            f'Venta #{venta.id}: total {total_venta}, '
                            f'pagó {monto_pagado}'
                        ),
                        creado_por=request.user if request.user.is_authenticated else None,
                    )
                # Si diferencia == 0: venta pagada exacta, no hay
                # movimiento extra.

                # Marcar el pedido como pagado si quedó saldado al
                # contado (total efectivo cubierto sin generar deuda).
                if diferencia >= 0:
                    venta.pedido.pagado = True
                    venta.pedido.save(update_fields=['pagado'])

            # Persistir las alertas de stock acumuladas durante el
            # procesamiento. Lo hacemos al final porque ahora `venta`
            # ya tiene PK (incluso en create). bulk_create es chico
            # porque típicamente las alertas son 1 o 2 por venta.
            if alertas_pendientes:
                AlertaStock.objects.bulk_create([
                    AlertaStock(
                        venta=venta,
                        articulo=p['articulo'],
                        cantidad_pedida=p['cantidad_pedida'],
                        stock_disponible_al_momento=p['stock_disponible_al_momento'],
                        cantidad_faltante=p['cantidad_faltante'],
                        creado_por=request.user if request.user.is_authenticated else None,
                    )
                    for p in alertas_pendientes
                ])

            venta_pk = venta.pk
    except ValueError as e:
        return JsonResponse({'ok': False, 'errores': [str(e)]}, status=400)
    except Exception as e:
        # Defensivo: si algo no previsto explota, devolvemos 500 con
        # el mensaje. El middleware de auditoría ya logueó el intento.
        return JsonResponse({'ok': False, 'errores': [f'Error inesperado: {e}']}, status=500)

    response = {'ok': True, 'venta_id': venta_pk}
    # Warnings opcionales (ej. stock insuficiente). La venta se guardó
    # pero el operador tiene que saber que algo no cuadró. `locals()`
    # nos protege del caso donde la rama de validación nunca llegó a
    # crear la lista (ej. payload vacío o redirect temprano).
    warnings_list = locals().get('warnings_list') or []
    if warnings_list:
        response['warnings'] = warnings_list
    return JsonResponse(response)


# ---------------------------------------------------------------------------
# Vistas de página
# ---------------------------------------------------------------------------

def _vendedores_para_contexto():
    """
    Lista plana de vendedores para el <select>.

    Antes intentamos pasar el queryset crudo y el template hacía
    `{{ v.usuario.username }}` — eso disparaba un query por cada
    vendedor (N+1). Con `.values()` reducimos a un solo SELECT y el
    template solo lee dicts.
    """
    return list(
        Vendedor.objects.all()
        .order_by('nombre', 'apellido')
        .values('id', 'nombre', 'apellido', 'usuario__username')
    )


@login_required
def venta_nueva(request):
    """Pantalla de crear venta. Render-only — la lógica vive en Alpine."""
    contexto = {
        'modo': 'crear',
        'venta_id': None,
        'vendedores': _vendedores_para_contexto(),
        'vendedor_default_id': _vendedor_for_user(request.user),
        'fecha_hoy': str(timezone.now().date()),
        'venta_inicial': None,
    }
    return render(request, 'venta/venta_nueva.html', contexto)


@login_required
def venta_editar(request, id):
    """
    Pantalla de editar venta.

    Pasamos un `venta_inicial` JSON-serializable que Alpine consume en
    init() para poblar el form. Lo serializamos en el server (no via
    AJAX) para que el operador no vea un flash vacío al cargar.
    """
    venta = get_object_or_404(
        Venta.objects.select_related('cliente', 'vendedor'),
        pk=id,
    )
    items = []
    for av in venta.ventas.select_related('articulo').all():
        # `precio_decimal` puede ser None si la línea es muy vieja y el
        # backfill no la alcanzó por alguna razón rara — fallback al
        # parser legacy.
        if av.precio_decimal is not None:
            precio_str = str(av.precio_decimal)
        else:
            from venta.utils import parse_precio
            precio_str = str(parse_precio(av.precio))
        items.append({
            'id': av.id,
            'articulo_id': av.articulo_id,
            'articulo_label': f'{av.articulo.codigo_interno or ""} {av.articulo.marca or ""} {av.articulo.nombre}',
            'cantidad': av.cantidad,
            'precio': precio_str,
            'descuento_porcentaje': str(getattr(av, 'descuento_porcentaje', 0) or 0),
            'stock_disponible': av.articulo.stock,
        })

    venta_inicial = {
        'id': venta.id,
        'cliente_id': venta.cliente_id,
        'cliente_label': venta.cliente.nombre_completo() if venta.cliente else '',
        'vendedor_id': venta.vendedor_id,
        'fecha_compra': str(venta.fecha_compra),
        'fecha_entrega': str(venta.fecha_entrega),
        'descuento_porcentaje': str(getattr(venta, 'descuento_porcentaje', 0) or 0),
        'descuento_motivo': getattr(venta, 'descuento_motivo', '') or '',
        'items': items,
    }

    contexto = {
        'modo': 'editar',
        'venta_id': venta.id,
        'vendedores': _vendedores_para_contexto(),
        'vendedor_default_id': venta.vendedor_id,
        'fecha_hoy': str(timezone.now().date()),
        'venta_inicial': venta_inicial,
    }
    return render(request, 'venta/venta_nueva.html', contexto)
