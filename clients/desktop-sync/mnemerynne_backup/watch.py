"""Running all the time: a pass soon after files change, and a full one every `rescan_minutes` (file events get
missed: sleep, network drives, too many at once) and after the computer wakes up."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from mnemerynne_backup.backup import Backup
from mnemerynne_backup.engine import Report

log = logging.getLogger(__name__)
QUIET = 10          # seconds without new events before a pass starts
TICK = 5


class Runner:
    def __init__(self, backup: Backup, on_report: Callable[[list[Report]], None] | None = None):
        self.backup = backup
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
        try:
            reports = self.backup.run(only=only)
        except Exception as exc:          # keep running; say what happened
            log.exception("Backup pass failed")
            reports = [Report(errors=[("", f"{exc.__class__.__name__}: {exc}")])]
        finally:
            self.busy = False
        self.on_report(reports)

    def loop(self) -> None:
        self._watch()
        rescan = self.backup.config.rescan_minutes * 60
        last_full, last_tick = 0.0, time.time()
        try:
            while not self.stop_event.is_set():
                now = time.time()
                woke = now - last_tick > 3 * TICK + 60        # the clock jumped: the computer slept
                last_tick = now
                if not self.paused:
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
