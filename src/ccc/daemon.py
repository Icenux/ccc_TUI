from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import date

from ccc import i18n, watch
from ccc.i18n import _
from ccc.backup import daily_backup, default_backup_dir
from ccc.heartbeat import HEARTBEAT_SECONDS, beat
from ccc.notify import CAT_UPDATES, Notifier
from ccc.analytics_archive import ARCHIVE_CHECK_SECONDS, archive, configured_folder
from ccc.orchestrator import Orchestrator, RunFn
from ccc.runner import execute
from ccc.session_usage import USAGE_POLL_SECONDS, UsageCollector
from ccc.storage import Storage

logger = logging.getLogger(__name__)


async def heartbeat_loop(storage: Storage, interval: float = HEARTBEAT_SECONDS) -> None:
    while True:
        try:
            beat(storage)
            # Notifications and notes follow the language set in the TUI.
            i18n.load(storage)
        except Exception:  # a locked DB moment must not kill the loop
            logger.exception("heartbeat write failed")
        await asyncio.sleep(interval)


BACKUP_CHECK_SECONDS = 3600.0


async def backup_loop(db_path: str, out_dir, interval: float = BACKUP_CHECK_SECONDS,
                      notifier: Notifier | None = None) -> None:
    """Check hourly; daily_backup itself makes at most one backup a day. A failure
    is told (category Health) once a day, not every hour."""
    told_on = None
    while True:
        try:
            path = await asyncio.to_thread(daily_backup, db_path, out_dir)
            if path is not None:
                logger.info("database backed up to %s", path)
        except Exception as exc:
            logger.exception("database backup failed")
            if notifier is not None and told_on != date.today():
                told_on = date.today()
                await asyncio.to_thread(watch.backup_failed, notifier, exc)
        await asyncio.sleep(interval)


async def usage_loop(storage: Storage, interval: float = USAGE_POLL_SECONDS) -> None:
    """Token usage of remote-control conversations, from their transcripts."""
    collector = UsageCollector()
    while True:
        try:
            await asyncio.to_thread(collector.collect, storage)
        except Exception:
            logger.exception("reading remote-control token usage failed")
        await asyncio.sleep(interval)


async def archive_loop(storage: Storage, interval: float = ARCHIVE_CHECK_SECONDS) -> None:
    """Hourly: archive months that have ended, when a folder is set."""
    while True:
        try:
            folder = configured_folder(storage)
            added = await asyncio.to_thread(archive, storage, folder) if folder else []
            for year, month in added:
                logger.info("Analytics: %04d-%02d archived to %s", year, month, folder)
        except Exception:
            logger.exception("archiving analytics failed")
        await asyncio.sleep(interval)


ROUTINE_CHECK_SECONDS = 20.0


async def routines_loop(storage: Storage, interval: float = ROUTINE_CHECK_SECONDS,
                        notifier: Notifier | None = None) -> None:
    """Queue the routines that are due (ccc.routines); the queues' own
    runtimes pick their tasks up."""
    from ccc import routines

    while True:
        try:
            fired = await asyncio.to_thread(routines.tick, storage)
            for routine, status in fired:
                logger.info("routine %d (%s): %s", routine.id, routine.name, status)
            if notifier is not None and fired:
                await asyncio.to_thread(watch.routines_tell, notifier, fired)
        except Exception:
            logger.exception("running routines failed")
        await asyncio.sleep(interval)


REVIEW_CHECK_SECONDS = 10.0


async def reviews_loop(storage: Storage, notifier: Notifier, interval: float = REVIEW_CHECK_SECONDS) -> None:
    """Run the reviews that wait (ccc.reviews) once their conversation is idle."""
    from ccc import reviews

    while True:
        try:
            for review in await reviews.tick(storage, notifier=notifier):
                logger.info("review %d of queue %d: %s %s", review.id, review.queue_id, review.status.value,
                            review.verdict.value if review.verdict else "")
        except Exception:
            logger.exception("running reviews failed")
        await asyncio.sleep(interval)


GOAL_CHECK_SECONDS = 5.0


async def goals_loop(storage: Storage, notifier: Notifier, interval: float = GOAL_CHECK_SECONDS) -> None:
    """Move the goal loops on (ccc.goals): check, review, next round."""
    from ccc import goals

    while True:
        try:
            for goal in await goals.tick(storage, notifier):
                logger.info("goal %d of queue %d: %s after %d round(s)", goal.id, goal.queue_id, goal.status.value,
                            goal.round)
        except Exception:
            logger.exception("running goal loops failed")
        await asyncio.sleep(interval)


WORKFLOW_CHECK_SECONDS = 5.0


async def workflows_loop(storage: Storage, notifier: Notifier, interval: float = WORKFLOW_CHECK_SECONDS) -> None:
    """Advance the multi-agent workflows (ccc.workflows), each in its own task."""
    from ccc import workflows

    while True:
        try:
            await workflows.tick(storage, notifier)
        except Exception:
            logger.exception("running workflows failed")
        await asyncio.sleep(interval)


WATCH_CHECK_SECONDS = 30.0
HEALTH_CHECK_SECONDS = 6 * 3600.0


async def watch_loop(storage: Storage, notifier: Notifier, db_path: str, interval: float = WATCH_CHECK_SECONDS,
                     health_interval: float = HEALTH_CHECK_SECONDS) -> None:
    """Devices signed out, the doctor's new warnings and the daily
    summary (ccc.watch)."""
    next_health = 0.0
    while True:
        try:
            await asyncio.to_thread(watch.devices_tick, storage, notifier)
            await asyncio.to_thread(watch.digest_tick, storage, notifier)
            if time.monotonic() >= next_health:
                next_health = time.monotonic() + health_interval
                await asyncio.to_thread(watch.health_tick, storage, notifier, db_path)
        except Exception:
            logger.exception("watching devices / health failed")
        await asyncio.sleep(interval)


UPDATE_FIRST_CHECK_SECONDS = 120.0


async def update_loop(storage: Storage, notifier: Notifier, interval: float = None) -> None:
    """Look for a new release on GitHub (ccc.updates) now and then and
    tell Telegram / Discord once per new version; the TUI and the phone read
    what was found from the settings."""
    from ccc import updates

    await asyncio.sleep(UPDATE_FIRST_CHECK_SECONDS)
    while True:
        try:
            await asyncio.to_thread(update_tick, storage, notifier)
        except Exception:
            logger.exception("looking for a ccc update failed")
        await asyncio.sleep(interval or updates.CHECK_SECONDS)


def update_tick(storage: Storage, notifier: Notifier, repo=None, latest=None) -> str | None:
    """One look; returns the notice it sent (for tests)."""
    from ccc import updates

    if not updates.check_enabled(storage):
        return None
    state = updates.check(storage, *([repo] if repo else [updates.PROJECT_DIR]), *([latest] if latest else []))
    if not state.available or state.version == state.notified_version:
        return None
    text = updates.notice(state)
    state.notified_version = state.version
    updates.save(storage, state)
    notifier.send_now(text, CAT_UPDATES)
    return text


async def _run(db_path: str, run_fn: RunFn = execute) -> None:
    storage = Storage(db_path)
    i18n.load(storage)
    notifier = Notifier(storage)
    orchestrator = Orchestrator(storage, run_fn=run_fn, notifier=notifier)
    await orchestrator.start_all()
    logger.info("ccc daemon started, db=%s, queues=%d", db_path, len(orchestrator.runtimes))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    heartbeat = asyncio.create_task(heartbeat_loop(storage))
    backups = asyncio.create_task(backup_loop(db_path, default_backup_dir(db_path), notifier=notifier))
    usage = asyncio.create_task(usage_loop(storage))
    archives = asyncio.create_task(archive_loop(storage))
    scheduled = asyncio.create_task(routines_loop(storage, notifier=notifier))
    reviewing = asyncio.create_task(reviews_loop(storage, notifier))
    goal_loops = asyncio.create_task(goals_loop(storage, notifier))
    flows = asyncio.create_task(workflows_loop(storage, notifier))
    updating = asyncio.create_task(update_loop(storage, notifier))
    watching = asyncio.create_task(watch_loop(storage, notifier, db_path))
    await stop_event.wait()
    logger.info("ccc daemon shutting down")
    heartbeat.cancel()
    backups.cancel()
    usage.cancel()
    archives.cancel()
    scheduled.cancel()
    reviewing.cancel()
    goal_loops.cancel()
    flows.cancel()
    updating.cancel()
    watching.cancel()
    await orchestrator.stop_all()


def run_daemon(db_path: str, run_fn: RunFn = execute) -> None:
    asyncio.run(_run(db_path, run_fn))


def main() -> None:
    from ccc.cli import default_db_path

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_daemon(str(default_db_path()))


if __name__ == "__main__":
    main()
