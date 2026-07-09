"""
Bloqueo de acceso fuera de horario operativo (00:00–06:00 hora Argentina).

Decisión del cliente (junio 2026): el sistema no se usa entre la
medianoche y las 6 AM. Aprovechamos eso para:

1. Apagar qcluster esas horas → Neon hace scale-to-zero
   (ver `venta/management/commands/qcluster_horario.py`).

2. Bloquear acceso HTTP de los vendedores / no-admin con este
   middleware. Sin esto, si un vendedor entra a las 3 AM y arranca
   a operar, hace queries contra una DB que el plan asume dormida,
   y nos volvemos a comer Compute Hours.

Excepciones (no se bloquean):
- `is_superuser=True`: los dueños / admins pueden entrar siempre.
  Caso raro pero el cliente lo pidió expresamente.
- `/admin/login/`, `/admin/logout/`, `/static/*`, `/favicon.ico`:
  necesarios para que el admin se pueda loguear durante la ventana.

Si cambia la ventana acá, también hay que ajustar la del comando
`qcluster_horario` — el supuesto es que coinciden.
"""

from datetime import datetime

from django.shortcuts import render

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 fallback
    from backports.zoneinfo import ZoneInfo


TZ_ARG = ZoneInfo("America/Argentina/Buenos_Aires")
HORA_INICIO_NOCHE = 0   # 00:00 ARG
HORA_FIN_NOCHE = 6      # 06:00 ARG

PREFIJOS_PERMITIDOS = (
    '/admin/login/',
    '/admin/logout/',
    '/static/',
    '/favicon.ico',
)


def _en_horario_nocturno():
    return HORA_INICIO_NOCHE <= datetime.now(TZ_ARG).hour < HORA_FIN_NOCHE


class FueraDeHorarioMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._bloquea(request):
            # render() (no TemplateResponse): CommonMiddleware lee
            # response.content para setear Content-Length, y la
            # TemplateResponse lazy todavía no se renderizó → tira
            # ContentNotRenderedError. render() devuelve un HttpResponse
            # ya materializado, sin ese problema.
            #
            # 503 Service Unavailable: hint para clientes HTTP / health
            # checks de que es una pausa esperada, no un error.
            return render(
                request,
                'venta/fuera_de_horario.html',
                {
                    'hora_inicio': f'{HORA_INICIO_NOCHE:02d}:00',
                    'hora_fin': f'{HORA_FIN_NOCHE:02d}:00',
                },
                status=503,
            )
        return self.get_response(request)

    def _bloquea(self, request):
        if not _en_horario_nocturno():
            return False
        # Permitir login/static siempre para que un admin pueda
        # entrar en plena ventana nocturna.
        for prefijo in PREFIJOS_PERMITIDOS:
            if request.path.startswith(prefijo):
                return False
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and user.is_superuser:
            return False
        return True
