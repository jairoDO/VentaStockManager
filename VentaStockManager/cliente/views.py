from datetime import timedelta

from django.shortcuts import render, get_object_or_404
from cliente.models import Cliente, MovimientoCuenta, PrecioCliente
from django.http import HttpResponse
from django.contrib.auth.models import User
from django.contrib.admin.views.decorators import staff_member_required
from django.views.generic import ListView
from django.utils import timezone
from articulo.models import Articulo
from dal import autocomplete
from django.db import models



#Cliente/cliente/ Create your views here.
def filtrar_por_mayor_de_edad(request):
    clientes_mayores = Cliente.objects.filter(edad__gte=18) 
    return HttpResponse(f"Clientes mayores de edad: {''.join(['<p>' + str(cliente) + '</p>' for cliente in clientes_mayores])}")

# Create your views here.
def filtrar_por_menor_de_edad(request):# Create your views here.
    clientes_menores = Cliente.objects.filter(edad__lte=18) 
    return render(request, 'clientes.html', {'clientes': clientes_menores})

def filtrar_por_de_18 (request):# Create your views here.
    clientes_de_18 = Cliente.objects.filter(edad=18) 
    return render(request, 'clientes.html', {'clientes': clientes_de_18})
    
def mostrar_todos_los_clientes(request):
    clientes = Cliente.objects.all()
    return render(request, 'clientes.html', {'clientes': clientes})


def procesar_nuevo_cliente(request):
    # process
    if request.method == 'POST':
        # saco los datos del formulario
        nombre = request.POST.get('nombre')
        apellido = request.POST.get('apellido')
        contrasena = request.POST.get('contrasena')
        cuil = request.POST.get('cuil')
        telefono = request.POST.get('telefono')
        edad = request.POST.get('edad')
        genero = request.POST.get('genero') 
        email = request.POST.get('email')
        # creo el usuario     
        perfil = User(email=email, password=contrasena, username=email)
        perfil.save()
        new_cliente = Cliente(
            nombre=nombre,
            apellido=apellido,
            cuil=cuil,
            telefono=telefono,
            sexo=genero,
            perfil=perfil,
            edad=edad
        )
        try:
            new_cliente.save()
        except ValidationError as e:
            return HttpResponse('error en validadtion de dato.')

    else:
        return render(request, 'formulario_cliente.html')
    
class ClienteAutocomplete(autocomplete.Select2QuerySetView):
    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return Cliente.objects.none()
        if self.q:
            clientes = Cliente.objects.filter(
                models.Q(nombre__icontains=self.q)|
                models.Q(codigo_interno__icontains=self.q) |
                models.Q(apellido__icontains=self.q))
        else:   
            clientes = Cliente.objects.all()
        return clientes
        
# # En tus vistas
# if request.user.has_perm('cliente.puede_acceder_lista_articulos'):
#     # Realiza alguna acción si el usuario tiene el permiso

class ListaArticulosView(ListView):
    model = Articulo
    template_name = 'lista_articulos.html'


@staff_member_required
def extracto_cliente(request, cliente_id):
    """
    Pantalla "extracto del cliente": cronología completa de un cliente
    en un solo lugar — ventas, movimientos de cuenta corriente,
    precios pactados, saldo actual.

    Pensada para responder rápido "¿qué pasó con fulano?" sin saltar
    entre 4 admins distintos. Acceso solo para staff.
    """
    # Lazy import para no romper si venta no está cargada (ej. tests
    # que solo tocan la app cliente).
    from venta.models import Venta

    cliente = get_object_or_404(
        Cliente.objects.select_related('cuenta'),
        pk=cliente_id,
    )

    try:
        dias = int(request.GET.get('dias') or '365')
    except ValueError:
        dias = 365

    if dias > 0:
        desde = timezone.now() - timedelta(days=dias)
        desde_date = desde.date()
    else:
        # `?dias=0` = traer todo. Igual lo limitamos a 5 años para
        # no hacer queries pesadas si alguien hace `?dias=99999`.
        desde = timezone.now() - timedelta(days=5 * 365)
        desde_date = desde.date()

    ventas = list(
        Venta.objects
        .filter(cliente=cliente, fecha_compra__gte=desde_date)
        .select_related('pedido', 'vendedor')
        .order_by('-fecha_compra')[:100]
    )

    movimientos = []
    cuenta = getattr(cliente, 'cuenta', None)
    if cuenta:
        movimientos = list(
            cuenta.movimientos
            .filter(created_at__gte=desde)
            .select_related('venta', 'creado_por')
            .order_by('-created_at')[:200]
        )

    # El histórico de cambios de precio pactado vive en auditlog;
    # acá mostramos el estado ACTUAL (precios vigentes con este
    # cliente), que es lo operativamente útil.
    precios_pactados = list(
        PrecioCliente.objects
        .filter(cliente=cliente)
        .select_related('articulo')
        .order_by('-updated_at')[:50]
    )

    contexto = {
        'cliente': cliente,
        'saldo': cliente.saldo,
        'ventas': ventas,
        'movimientos': movimientos,
        'precios_pactados': precios_pactados,
        'dias': dias,
        'opciones_dias': [
            (30, 'Últimos 30 días'),
            (90, 'Últimos 90 días'),
            (365, 'Último año'),
            (0, 'Todo (hasta 5 años)'),
        ],
    }
    return render(request, 'cliente/extracto.html', contexto)

 