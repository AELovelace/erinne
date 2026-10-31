"""Running all the time: a pass soon after files change, and a full one every `rescan_minutes` (file events get
missed: sleep, network drives, too many at once) and after the computer wakes up."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import time
from collections.abc import Callable

from mnemerynne_backup.backup import Backup
from mnemerynne_backup.config import Config, config_dir, state_dir
from mnemerynne_backup.engine import Report

log = logging.getLogger(__name__)
QUIET = 10          # seconds without new events before a pass starts
TICK = 5

# The background service (`mnemerynne-backup run`) and the terminal app talk through state.db's meta table:
SERVICE = "service:status"       # {"pid", "busy", "paused"} while the service runs; written by the service
ASK_PAUSED = "service:paused"    # True to pause it; written by the terminal app
ASK_NOW = "service:now"          # when Back up now was last pressed; written by the terminal app


class Runner:
    def __init__(self, backup: Backup, on_report: Callable[[list[Report]], None] | None = None, *,
                 service: bool = False):
        """service: run as the background service, which the terminal app can watch and steer (see SERVICE),
        and reload the settings whenever their file changes."""
        self.backup = backup
        self.service = service
        self._asked_at = time.time()
        self._config_mtime = _mtime(self._config_path())
        self._published = None
        self.on_report = on_report or (lambda reports: None)
        self.dirty: dict[str, float] = {}          # label -> time of the last event
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.busy = False
        self.paused = False
        self._observer = None

    # file events
    def _watch(self) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.info("watchdog isn't installed: only the regular full passes run.")
            return
        runner = self

        class Handler(FileSystemEventHandler):
            def __init__(self, label: str):
                self.label = label

            def on_any_event(self, event):
                if event.event_type in ("opened", "closed_no_write"):
                    return
                with runner.lock:
                    runner.dirty[self.label] = time.monotonic()

        self._observer = Observer()
        for folder in self.backup.config.folders:
            try:
                self._observer.schedule(Handler(folder.label), folder.path, recursive=True)
            except OSError as exc:      # inotify out of watches, folder gone: the regular passes still run
                log.warning("Can't watch %s (%s); it's checked every %s minutes instead.", folder.path, exc,
                            self.backup.config.rescan_minutes)
        self._observer.start()

    def restart_watch(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(5)
            self._observer = None
        self._watch()

    def run_now(self) -> None:
        """Ask for a full pass as soon as possible (the tray's Back up now)."""
        with self.lock:
            for f in self.backup.config.folders:
                self.dirty[f.label] = 0
        self.wake.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()

    def _pass(self, only: list[str] | None) -> None:
        self.busy = True
        self._publish()
        try:
            reports = self.backup.run(only=only)
        except Exception as exc:          # keep running; say what happened
            log.exception("Backup pass failed")
            reports = [Report(errors=[("", f"{exc.__class__.__name__}: {exc}")])]
        finally:
            self.busy = False
            self._config_mtime = _mtime(self._config_path())     # a pass can save the settings itself
            self._publish()
        self.on_report(reports)

    # the background service
    def _config_path(self):
        return self.backup.config_path or config_dir() / "config.json"

    def _publish(self) -> None:
        if not self.service:
            return
        status = {"pid": os.getpid(), "busy": self.busy, "paused": self.paused}
        if status != self._published:
            self.backup.state.set(SERVICE, status)
            self._published = status

    def _service_tick(self) -> None:
        """Follow what the terminal app asked for, and pick up settings it (or anything else) changed."""
        state = self.backup.state
        self.paused = bool(state.get(ASK_PAUSED, False))
        asked = state.get(ASK_NOW, 0) or 0
        if asked > self._asked_at:
            self._asked_at = asked
            self.run_now()
        mtime = _mtime(self._config_path())
        if mtime != self._config_mtime:
            self._config_mtime = mtime
            log.info("The settings changed; reloading them.")
            self.backup = Backup(Config.load(self.backup.config_path), state, config_path=self.backup.config_path)
            self.restart_watch()
            self.run_now()
        self._publish()

    def loop(self) -> None:
        self._watch()
        rescan = self.backup.config.rescan_minutes * 60
        last_full, last_tick = 0.0, time.time()
        try:
            while not self.stop_event.is_set():
                if self.service:
                    self._service_tick()
                now = time.time()
                woke = now - last_tick > 3 * TICK + 60        # the clock jumped: the computer slept
                last_tick = now
                if not self.paused and self.backup.config.server:
                    if woke or now - last_full > rescan:
                        last_full = now
                        with self.lock:
                            self.dirty.clear()
                        self._pass(None)
                    else:
                        with self.lock:
                            ready = [label for label, at in self.dirty.items() if time.monotonic() - at > QUIET]
                            for label in ready:
                                del self.dirty[label]
                        if ready:
                            self._pass(ready)
                self.wake.wait(TICK)
                self.wake.clear()
        finally:
            if self._observer is not None:
                self._observer.stop()
            if self.service:
                self.backup.state.set(SERVICE, None)


def _mtime(path) -> int | None:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def service_status(state) -> dict | None:
    """The background service's {"pid", "busy", "paused"}, or None when it isn't running."""
    status = state.get(SERVICE)
    if not isinstance(status, dict):
        return None
    if sys.platform != "win32":        # (on Windows, os.kill would end the process)
        try:
            os.kill(int(status.get("pid", 0)), 0)      # still there? signal 0 only checks
        except ProcessLookupError:
            return None                 # it died without clearing its status
        except (OSError, ValueError):
            pass                        # there, but not ours to signal: believe the status
    return status


def single_instance():
    """A lock only one running copy can hold (the tray, the TUI and the service share it); None when another
    copy has it."""
    path = state_dir() / "tray.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def log_to_file() -> None:
    """Log to backup.log in the state folder (the tray and the TUI have no console to log to)."""
    path = state_dir() / "backup.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
