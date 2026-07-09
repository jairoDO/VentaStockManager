"""
Wrapper de `manage.py qcluster` que apaga el worker durante la noche.

Motivación: el cliente no usa el sistema entre las 00:00 y las 07:00
hora Argentina. Mientras qcluster corre, hace polling cada N segundos
contra Postgres preguntando "¿hay tareas en la cola?", lo que mantiene
a Neon despierto y consume Compute Hours del plan.

Apagando qcluster esas 7 horas reducimos ~30% el polling diario y le
damos a Neon una ventana real para hacer scale-to-zero. Las tareas
programadas (django-q schedules) que caigan en esa ventana se ejecutan
apenas qcluster vuelve a las 07:00 — django-q persiste los `Schedule`
en DB y los recupera al arrancar.

Por qué no matar el process desde honcho/Render: cuando un proceso de
honcho termina, honcho mata a TODOS sus hijos (incluido gunicorn).
Necesitamos un proceso que se quede vivo 24h pero internamente decida
arrancar/parar qcluster.

Por qué no usar `Q_CLUSTER['stopper']`: django-q2 no expone un hook de
"pausa programada" — solo un stopper binario al startup. Manejarlo como
subprocess es más simple y se entiende leyendo el código.
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 — no debería pasar pero por las dudas
    from backports.zoneinfo import ZoneInfo


TZ_ARG = ZoneInfo("America/Argentina/Buenos_Aires")
HORA_INICIO_NOCHE = 0   # 00:00 ARG: empieza la pausa nocturna
HORA_FIN_NOCHE = 6      # 06:00 ARG: vuelve qcluster
# IMPORTANTE: esta ventana tiene que coincidir con la de
# `VentaStockManager.middleware_horario.FueraDeHorarioMiddleware`, que
# bloquea el acceso HTTP de no-superusers en el mismo rango. Si
# cambia uno, cambiar el otro.


def _ahora_arg():
    return datetime.now(TZ_ARG)


def _es_horario_nocturno():
    return HORA_INICIO_NOCHE <= _ahora_arg().hour < HORA_FIN_NOCHE


def _segundos_hasta_amanecer():
    """Segundos hasta las 07:00 ARG del próximo amanecer."""
    ahora = _ahora_arg()
    objetivo = ahora.replace(
        hour=HORA_FIN_NOCHE, minute=0, second=0, microsecond=0
    )
    if ahora >= objetivo:
        objetivo += timedelta(days=1)
    return int((objetivo - ahora).total_seconds())


class Command(BaseCommand):
    help = (
        f"qcluster con pausa nocturna automática "
        f"({HORA_INICIO_NOCHE:02d}:00–{HORA_FIN_NOCHE:02d}:00 hora Argentina)."
    )

    def handle(self, *args, **opts):
        proceso = None
        while True:
            if _es_horario_nocturno():
                if proceso and proceso.poll() is None:
                    self.stdout.write(
                        "[qcluster_horario] Entrando en horario nocturno — "
                        "parando qcluster."
                    )
                    proceso.terminate()
                    try:
                        proceso.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proceso.kill()
                        proceso.wait()
                    proceso = None
                dormir = _segundos_hasta_amanecer()
                self.stdout.write(
                    f"[qcluster_horario] Durmiendo {dormir}s hasta las "
                    f"{HORA_FIN_NOCHE:02d}:00 ARG."
                )
                # Cap a 1h para que el loop reevalúe periódicamente —
                # robustez frente a cambios de horario / reloj.
                time.sleep(min(dormir, 3600))
                continue

            if proceso is None or proceso.poll() is not None:
                if proceso is not None:
                    self.stdout.write(
                        f"[qcluster_horario] qcluster salió con código "
                        f"{proceso.returncode} — relanzando."
                    )
                else:
                    self.stdout.write("[qcluster_horario] Levantando qcluster.")
                proceso = subprocess.Popen(
                    [sys.executable, "manage.py", "qcluster"],
                    cwd=os.getcwd(),
                )

            # Reevaluar cada minuto: rápido para detectar entrada en
            # ventana nocturna, lento para no spammear logs.
            time.sleep(60)
