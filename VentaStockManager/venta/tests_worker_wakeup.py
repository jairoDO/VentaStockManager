import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from django_q.conf import Conf
from django_q.models import OrmQ, Schedule

from venta.management.commands.qcluster_horario import Command, next_work, queue_pending
from venta.worker_wakeup import TZ_ARG, operating_time, wake_token, wake_worker


class WorkerWakeupTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        options = override_settings(Q_WORKER_WAKE_FILE=str(Path(directory.name) / 'wake'),
                                    Q_WORKER_START_HOUR=8, Q_WORKER_END_HOUR=18)
        options.enable()
        self.addCleanup(options.disable)

    def test_business_hours_boundaries(self):
        for hour, day, expected in [(0, 28, 8), (7, 28, 8), (8, 28, 8),
                                    (17, 28, 17), (18, 29, 8), (23, 29, 8)]:
            now = datetime(2026, 10, 28, hour, tzinfo=TZ_ARG)
            actual = operating_time(now)
            self.assertEqual((actual.day, actual.hour), (day, expected))
            self.assertEqual(operating_time(now.astimezone(timezone.utc)), actual)

    def test_hint_changes_without_database_queries(self):
        with self.assertNumQueries(0):
            self.assertEqual(wake_token(), '')
            wake_worker()
            first = wake_token()
            wake_worker()
            self.assertTrue(first)
            self.assertNotEqual(first, wake_token())

    def test_enqueue_wakes_only_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            OrmQ.objects.create(key=Conf.CLUSTER_NAME, payload='test', lock=timezone.now())
            self.assertEqual(wake_token(), '')
        self.assertTrue(wake_token())

    def test_rollback_does_not_wake(self):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    OrmQ.objects.create(key=Conf.CLUSTER_NAME, payload='test', lock=timezone.now())
                    raise ValueError('rollback')
            except ValueError:
                pass
        self.assertEqual(wake_token(), '')

    @mock.patch('venta.management.commands.qcluster_horario.connections.close_all')
    def test_locked_task_stays_pending_until_acknowledged(self, close):
        task = OrmQ.objects.create(key=Conf.CLUSTER_NAME, payload='test',
                                   lock=timezone.now() + timedelta(minutes=5))
        self.assertTrue(queue_pending())
        task.delete()
        self.assertFalse(queue_pending())

    @mock.patch('venta.management.commands.qcluster_horario.connections.close_all')
    def test_ignores_disabled_and_other_cluster_schedules(self, close):
        now = timezone.now()
        Schedule.objects.create(func='builtins.str', repeats=0, next_run=now)
        Schedule.objects.create(func='builtins.str', cluster='other', next_run=now)
        target = now + timedelta(hours=2)
        Schedule.objects.create(func='builtins.str', next_run=target)
        self.assertEqual(next_work(now), (False, target))
        close.assert_called_once()

    @mock.patch('venta.management.commands.qcluster_horario.signal.signal')
    @mock.patch('venta.management.commands.qcluster_horario.connections.close_all')
    @mock.patch('venta.management.commands.qcluster_horario.next_work')
    @mock.patch('venta.management.commands.qcluster_horario.time.sleep')
    def test_idle_loop_reads_once_then_sleeps_without_queries(self, sleep, read, close, signals):
        now = datetime(2026, 10, 28, 12, tzinfo=TZ_ARG)
        read.return_value = (False, now + timedelta(days=1))
        command = Command()
        def tick(seconds):
            if sleep.call_count >= 6:
                command.stopping = True
        sleep.side_effect = tick
        with mock.patch('venta.management.commands.qcluster_horario.timezone.now', return_value=now):
            with self.assertNumQueries(0):
                command.handle()
        read.assert_called_once()

    @mock.patch('venta.management.commands.qcluster_horario.signal.signal')
    @mock.patch('venta.management.commands.qcluster_horario.connections.close_all')
    @mock.patch('venta.management.commands.qcluster_horario.next_work')
    @mock.patch('venta.management.commands.qcluster_horario.time.sleep')
    def test_overnight_does_not_access_database(self, sleep, read, close, signals):
        command = Command()
        sleep.side_effect = lambda _: setattr(command, 'stopping', True)
        with mock.patch('venta.management.commands.qcluster_horario.timezone.now',
                        return_value=datetime(2026, 10, 28, 20, tzinfo=TZ_ARG)):
            command.handle()
        read.assert_not_called()

    @mock.patch('venta.management.commands.qcluster_horario.scheduler')
    @mock.patch('venta.management.commands.qcluster_horario.connections.close_all')
    @mock.patch('venta.management.commands.qcluster_horario.subprocess.Popen')
    @mock.patch('venta.management.commands.qcluster_horario.queue_pending')
    @mock.patch('venta.management.commands.qcluster_horario.time.sleep')
    def test_batch_finishes_before_graceful_stop(self, sleep, pending, popen, close, scheduler):
        process = popen.return_value
        process.poll.return_value = None
        pending.side_effect = [True, False]
        command = Command()
        command.stopping = False
        command.process = None
        with mock.patch('venta.management.commands.qcluster_horario.timezone.now',
                        return_value=datetime(2026, 10, 28, 12, tzinfo=TZ_ARG)):
            command._run_batch()
        self.assertEqual(pending.call_count, 2)
        scheduler.assert_called_once()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with()

    @mock.patch('venta.management.commands.qcluster_horario.signal.signal')
    @mock.patch('venta.management.commands.qcluster_horario.connections.close_all')
    @mock.patch('venta.management.commands.qcluster_horario.next_work')
    @mock.patch('venta.management.commands.qcluster_horario.time.sleep')
    def test_new_local_enqueue_wakes_before_hourly_recovery(self, sleep, read, close, signals):
        now = datetime(2026, 10, 28, 12, tzinfo=TZ_ARG)
        read.side_effect = [(False, None), (True, None)]
        command = Command()
        sleep.side_effect = lambda _: wake_worker()
        with mock.patch('venta.management.commands.qcluster_horario.timezone.now', return_value=now):
            with mock.patch.object(command, '_run_batch',
                                   side_effect=lambda: setattr(command, 'stopping', True)) as run:
                command.handle()
        self.assertEqual(read.call_count, 2)
        run.assert_called_once()

    @mock.patch('venta.management.commands.qcluster_horario.signal.signal')
    @mock.patch('venta.management.commands.qcluster_horario.connections.close_all')
    @mock.patch('venta.management.commands.qcluster_horario.next_work')
    def test_due_schedule_wakes_worker_without_user_visit(self, read, close, signals):
        now = datetime(2026, 10, 28, 12, tzinfo=TZ_ARG)
        read.return_value = (False, now - timedelta(minutes=1))
        command = Command()
        with mock.patch('venta.management.commands.qcluster_horario.timezone.now', return_value=now):
            with mock.patch.object(command, '_run_batch',
                                   side_effect=lambda: setattr(command, 'stopping', True)) as run:
                command.handle()
        run.assert_called_once()

    @override_settings(USE_TZ=False, TIME_ZONE='UTC')
    def test_production_naive_datetime_boundaries(self):
        for now, expected in [
            (datetime(2026, 10, 28, 10, 59), datetime(2026, 10, 28, 11)),
            (datetime(2026, 10, 28, 11), datetime(2026, 10, 28, 11)),
            (datetime(2026, 10, 28, 20, 59), datetime(2026, 10, 28, 20, 59)),
            (datetime(2026, 10, 28, 21), datetime(2026, 10, 29, 11)),
        ]:
            allowed = operating_time(now)
            self.assertEqual(allowed, expected)
            self.assertGreaterEqual(allowed, now)
            self.assertTrue(timezone.is_naive(allowed))

    @override_settings(USE_TZ=False, TIME_ZONE='UTC')
    @mock.patch('venta.management.commands.qcluster_horario.signal.signal')
    @mock.patch('venta.management.commands.qcluster_horario.connections.close_all')
    @mock.patch('venta.management.commands.qcluster_horario.next_work')
    @mock.patch('venta.management.commands.qcluster_horario.time.sleep')
    def test_production_naive_idle_loop_stays_alive(self, sleep, read, close, signals):
        now = datetime(2026, 10, 28, 18)
        read.return_value = (False, now + timedelta(hours=4))
        command = Command()
        sleep.side_effect = lambda _: setattr(command, 'stopping', True)
        with mock.patch('venta.management.commands.qcluster_horario.timezone.now', return_value=now):
            command.handle()
        read.assert_called_once()
