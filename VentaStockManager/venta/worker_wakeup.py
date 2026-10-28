"""Local wake-up for the single Render instance; sleeping never queries Postgres.

The queue remains durable in Postgres. The file is only a hint: startup and an
hourly reconciliation recover queued work from other producers or a lost hint.
Web and worker must share one instance (as they do in the honcho Procfile).
"""
import logging
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.utils import timezone

logger = logging.getLogger(__name__)
TZ_ARG = ZoneInfo('America/Argentina/Buenos_Aires')


def operating_time(now):
    """First allowed instant at/after now. Only background work is restricted."""
    was_naive = timezone.is_naive(now)
    default_zone = timezone.get_default_timezone()
    if was_naive:
        now = timezone.make_aware(now, default_zone)
    now = now.astimezone(TZ_ARG)
    start = getattr(settings, 'Q_WORKER_START_HOUR', 8)
    end = getattr(settings, 'Q_WORKER_END_HOUR', 18)
    if not 0 <= start < end <= 24:
        raise ValueError('Q worker hours must satisfy 0 <= start < end <= 24')
    if now.hour < start:
        now = now.replace(hour=start, minute=0, second=0, microsecond=0)
    elif now.hour >= end:
        now = (now + timedelta(days=1)).replace(
            hour=start, minute=0, second=0, microsecond=0,
        )
    # Match timezone.now() and Schedule.next_run under either USE_TZ setting.
    return timezone.make_naive(now, default_zone) if was_naive else now


def wake_path():
    return Path(getattr(settings, 'Q_WORKER_WAKE_FILE',
                        '/tmp/ventastockmanager-qcluster.wake'))


def wake_token():
    try:
        return wake_path().read_text()
    except OSError:
        return ''


def wake_worker():
    path = wake_path()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent,
                                         prefix=path.name, delete=False) as hint:
            temporary = hint.name
            hint.write(uuid4().hex)
        os.replace(temporary, path)
    except OSError:
        logger.exception('Could not wake task worker; hourly recovery will retry')
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _queue_saved(sender, instance, using, raw=False, **kwargs):
    if not raw:
        transaction.on_commit(wake_worker, using=using)


def register_wakeup_signals():
    for model in ('django_q.OrmQ', 'django_q.Schedule'):
        post_save.connect(_queue_saved, sender=model, weak=False,
                          dispatch_uid=f'venta.worker_wakeup.{model}')
