"""The app's work, for the command line and the tray alike: the folders, signing in, and running passes."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import httpx

from mnemerynne_backup import netstate
from mnemerynne_backup.config import Config, Folder, state_dir
from mnemerynne_backup.engine import Engine, Report
from mnemerynne_backup.signin import Tokens
from mnemerynne_backup.state import State
from mnemerynne_backup.transport import Offline, RootGone, SignedOut, Throttle, Transport

log = logging.getLogger(__name__)


class BackupError(Exception):
    """Something the person has to fix; the message says what."""


class Backup:
    def __init__(self, config: Config, state: State | None = None, transport: Transport | None = None,
                 *, config_path: Path | None = None):
        self.config, self.config_path = config, config_path
        self.state = state or State(state_dir() / "state.db")
        self._transport = transport

    @property
    def transport(self) -> Transport:
        if self._transport is None:
            if not self.config.server:
                raise BackupError("Sign in first: mnemerynne-backup login https://your-mnemerynne")
            self._transport = Transport(self.config.base_url, Tokens(self.config.server, self.config.base_url),
                                        throttle=Throttle(self.config.bandwidth_kbps))
        return self._transport

    def save(self) -> None:
        self.config.save(self.config_path)

    # ------------------------------------------------------------- folders
    def add(self, path: str, label: str | None = None, excludes: list[str] | None = None) -> Folder:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            raise BackupError(f"{path} isn't a folder.")
        if self.config.folder(path):
            raise BackupError(f"{path} is already backed up.")
        label = (label or Path(path).name or "Drive").strip()
        if self.config.folder(label):
            raise BackupError(f"Another folder is already called “{label}”; pick another label.")
        root = self.transport.register(self.config.computer, label)
        folder = Folder(path=path, label=root["label"], root_id=root["id"], excludes=list(excludes or []))
        self.config.folders.append(folder)
        self.save()
        return folder

    def remove(self, key: str) -> Folder:
        """Stop backing a folder up. Its copy on the NAS stays until it is deleted in Files."""
        folder = self._folder(key)
        self.config.folders.remove(folder)
        if folder.root_id is not None:
            self.state.forget_root(folder.root_id)
        self.save()
        return folder

    def _folder(self, key: str) -> Folder:
        folder = self.config.folder(key)
        if folder is None:
            raise BackupError(f"No backed-up folder is called {key}.")
        return folder

    def set_paused(self, key: str, paused: bool) -> None:
        self._folder(key).paused = paused
        self.save()

    def set_excludes(self, key: str, excludes: list[str]) -> None:
        self._folder(key).excludes = list(excludes)
        self.save()

    def set_searchable(self, key: str, on: bool) -> dict:
        folder = self._folder(key)
        return self.transport.set_searchable(self._root_id(folder), on)

    def _root_id(self, folder: Folder) -> int:
        if folder.root_id is None:
            folder.root_id = self.transport.register(self.config.computer, folder.label)["id"]
            self.save()
        return folder.root_id

    # -------------------------------------------------------------- passes
    def check_due(self, folder: Folder) -> bool:
        last = self.state.get(f"checked:{folder.root_id}", 0)
        return time.time() - last > self.config.check_hours * 3600

    def run(self, *, check: bool | None = None, only: list[str] | None = None) -> list[Report]:
        """One pass over every folder that isn't paused (or the ones named). `check` None: when it's due."""
        reports = []
        if self.config.pause_on_metered and netstate.metered():
            log.info("On a metered connection; backups wait.")
            return [Report(label=f.label, offline=True) for f in self.config.folders]
        engine = Engine(self.transport, self.state, settle=self.config.settle_seconds)
        try:
            present = {r["id"] for r in self.transport.roots() if r["present"]}
        except Offline:
            return [Report(label=f.label, offline=True) for f in self.config.folders]
        except SignedOut:
            return [Report(label=f.label, signed_out=True) for f in self.config.folders]
        for folder in self.config.folders:
            if folder.root_id is not None and folder.root_id not in present:
                # Deleted on the NAS (or never made): make it again; everything goes up again.
                self.state.forget_root(folder.root_id)
                folder.root_id = None
            if folder.paused or (only and folder.label not in only):
                continue
            if not os.path.isdir(folder.path):
                report = Report(label=folder.label, errors=[(folder.path, "the folder isn't there (a drive "
                                                                         "unplugged?); nothing was deleted")])
                reports.append(self._keep(folder, report))
                continue
            reports.append(self._keep(folder, self._run_one(engine, folder, check)))
        return reports

    def _run_one(self, engine: Engine, folder: Folder, check: bool | None) -> Report:
        root_id = self._root_id(folder)
        do_check = self.check_due(folder) if check is None else check
        try:
            report = engine.run(root_id, folder.path, label=folder.label, excludes=folder.excludes, check=do_check)
        except RootGone:
            # Deleted on the NAS: make it again and send everything (the server's list is empty).
            self.state.forget_root(root_id)
            folder.root_id = None
            root_id = self._root_id(folder)
            report = engine.run(root_id, folder.path, label=folder.label, excludes=folder.excludes, check=True)
            do_check = True
        if do_check and report.ok:
            self.state.set(f"checked:{root_id}", time.time())
        return report

    def _keep(self, folder: Folder, report: Report) -> Report:
        self.state.set(f"report:{folder.label}", report.json())
        return report

    def last_reports(self) -> dict[str, dict]:
        return {f.label: self.state.get(f"report:{f.label}") for f in self.config.folders}

    # ------------------------------------------------------------- restore
    def restore(self, key: str, dest: str, path: str | None = None, *, version_id: int | None = None) -> int:
        """Copy the folder's backup (or one file or folder in it, or one earlier version of a file) into
        `dest`. Never overwrites: a file already at its place is skipped. Returns how many were saved."""
        folder = self._folder(key)
        root_id = self._root_id(folder)
        dest_dir = Path(os.path.abspath(os.path.expanduser(dest)))
        if version_id is not None:
            if not path:
                raise BackupError("Say which file the version is of.")
            target = dest_dir / Path(path).name
            if target.exists():
                raise BackupError(f"{target} is already there; restore it somewhere else.")
            self.transport.download(root_id, path, target, version_id=version_id)
            return 1
        files = self.transport.manifest(root_id)["files"]
        prefix = (path or "").strip("/")
        chosen = [p for p in files if not prefix or p == prefix or p.startswith(prefix + "/")]
        if prefix and not chosen:
            raise BackupError(f"{prefix} isn't in the backup.")
        saved = 0
        for rel in sorted(chosen):
            target = dest_dir.joinpath(*rel.split("/"))
            if target.exists():
                continue
            self.transport.download(root_id, rel, target)
            saved += 1
        return saved


def connect(server: str, client: httpx.Client | None = None) -> dict:
    """What the server says about backups: where to send them (its backup address) and the newest app."""
    probe = Transport(server, lambda refresh: "", client=client)
    try:
        return probe.client_info()
    finally:
        if client is None:
            probe.close()
