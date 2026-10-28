"""Run django-q on demand between 08:00 and 18:00 Argentina.

No queue polling while idle: local enqueue/changed-schedule hints wake us.
Cached schedule times and hourly reconciliation preserve automatic maintenance
and recover work enqueued by another machine. Queue rows (including locked
rows) remain in Postgres until acknowledgement, so a running task is never
mistaken for an empty queue. The web process is never stopped by this command.
"""
import signal
import subprocess
import sys
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.models import Q
from django.utils import timezone
from django_q.conf import Conf
from django_q.models import OrmQ, Schedule
from django_q.scheduler import scheduler

from venta.worker_wakeup import operating_time, wake_token


def next_work(now):
    """Read once, then close DB connections before sleeping or spawning."""
    try:
        queue = OrmQ.objects.using(Conf.ORM).filter(key=Conf.CLUSTER_NAME)
        schedules = Schedule.objects.exclude(repeats=0)
        clusters = Q(cluster=Conf.CLUSTER_NAME)
        if Conf.CLUSTER_NAME == Conf.PREFIX:
            clusters |= Q(cluster__isnull=True)
        scheduled = schedules.filter(clusters).order_by('next_run').values_list(
            'next_run', flat=True,
        ).first()
        pending = queue.exists()
        return pending, scheduled
    finally:
        connections.close_all()


def queue_pending():
    try:
        # Include locks: dequeued but unacknowledged work must finish first.
        return OrmQ.objects.using(Conf.ORM).filter(key=Conf.CLUSTER_NAME).exists()
    finally:
        connections.close_all()


class Command(BaseCommand):
    help = 'Task worker on demand, 08:00–18:00 Argentina, without idle DB polling.'

    def handle(self, *args, **opts):
        self.stopping = False
        self.process = None
        signal.signal(signal.SIGTERM, self._stop_requested)
        signal.signal(signal.SIGINT, self._stop_requested)
        deadline = timezone.now()
        seen = None
        self.stdout.write('[qcluster_horario] Modo bajo demanda 08:00–18:00 ARG.')
        try:
            while not self.stopping:
                now = timezone.now()
                allowed = operating_time(now)
                if allowed > now:
                    # Keep hints unconsumed: work queued overnight wakes at 08:00.
                    time.sleep(min(2, max(0.1, (allowed - now).total_seconds())))
                    continue
                token = wake_token()
                if token != seen or now >= deadline:
                    # Snapshot before DB read, so a concurrent enqueue cannot be lost.
                    seen = token
                    try:
                        pending, scheduled = next_work(now)
                        if pending or (scheduled and scheduled <= now):
                            self._run_batch()
                            deadline = timezone.now()
                            continue
                        recovery = getattr(settings, 'Q_WORKER_RECOVERY_SECONDS', 3600)
                        deadline = now + timedelta(seconds=max(60, recovery))
                        if scheduled:
                            deadline = min(deadline, scheduled + timedelta(seconds=1))
                        self.stdout.write(
                            '[qcluster_horario] Cola vacía; dormido sin consultar DB.'
                        )
                    except Exception as exc:
                        connections.close_all()
                        self.stderr.write(f'[qcluster_horario] Reintento en 60s: {type(exc).__name__}')
                        deadline = now + timedelta(seconds=60)
                time.sleep(2)
        finally:
            self._stop_child()
            connections.close_all()

    def _stop_requested(self, signum, frame):
        self.stopping = True

    def _run_batch(self):
        # Dispatch due schedules immediately instead of waiting for qcluster's timer.
        try:
            scheduler()
        finally:
            connections.close_all()
        if self.stopping:
            return
        self.stdout.write('[qcluster_horario] Trabajo pendiente; levantando qcluster.')
        self.process = subprocess.Popen([sys.executable, 'manage.py', 'qcluster'])
        try:
            while not self.stopping:
                time.sleep(10)
                if self.process.poll() is not None:
                    raise RuntimeError('qcluster exited before draining its queue')
                now = timezone.now()
                if operating_time(now) > now or not queue_pending():
                    break
        finally:
            self._stop_child()

    def _stop_child(self):
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                # django-q SIGTERM drains work and joins its children gracefully.
                self.process.terminate()
                self.process.wait()
        finally:
            self.process = None
