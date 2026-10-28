# Background work and Neon compute

The `qcluster_horario` supervisor runs with the web process on ONE Render
instance. It starts django-q only when there is queued work or a due schedule,
between 08:00 and 18:00 America/Argentina/Buenos_Aires. The web's existing
access hours are unchanged. Work submitted after 18:00 waits until 08:00;
work already running is drained gracefully.

`venta.worker_wakeup` uses post-save / transaction.on_commit signals for OrmQ
and Schedule to update a local file. While idle, the supervisor checks only
that file and its cached next-schedule timestamp, not Postgres. On startup
and once per hour during operating hours it reconciles the durable queue,
recovering external enqueues or a missing local hint. The database can still
wake for real web traffic, scheduled work, and these hourly reconciliations.

The queue and schedule records remain in Postgres. No queue data is moved to
local disk. Locked queue rows count as pending so the supervisor never shuts
down merely because a worker has dequeued a job. Signals and reconciliation
cover enqueue/commit races and process restarts.

Settings: Q_WORKER_START_HOUR (8), Q_WORKER_END_HOUR (18),
Q_WORKER_RECOVERY_SECONDS (3600), optional Q_WORKER_WAKE_FILE.

Do not scale to multiple web instances without replacing the local wake hint
with a shared mechanism; hourly recovery alone does not give prompt delivery
across instances. Bot availability is independent: a suspended Render wa-bot
must be resumed before using WhatsApp. This change does not resume it.

Observe logs for `Modo bajo demanda`, `Trabajo pendiente`, and `Cola vacía`.
Verify Neon enters idle after five minutes without traffic. Opening the SQL
editor or refreshing live DB tools can itself wake Neon. Real users keep the
DB active; this change removes idle task polling, not legitimate requests.

Rollback: redeploy the preceding Render commit. No schema migration or queue
conversion is involved. Leave both unused Render resources suspended unless
there is a reason to restore them.
