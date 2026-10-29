"""Talking to Mnemerynne's backup API (/api/sync/*, mnemerynne/web/sync.py) over HTTPS."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import quote

import httpx

from mnemerynne_backup import __version__

SMALL = 32 * 1024 * 1024       # the server takes files up to this in one request; bigger ones go in parts
BLOCK = 256 * 1024
RETRIES = 3


class TransportError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class Offline(TransportError):
    """The server can't be reached (no network, the server down): try again later."""


class SignedOut(TransportError):
    """The sign-in was revoked or expired: the person has to sign the app in again."""


class Precondition(TransportError):
    """The server's copy isn't the version we said (412); `current` is the server's version, None if gone."""

    def __init__(self, message: str, current: str | None):
        super().__init__(message, 412)
        self.current = current


class RootGone(TransportError):
    """The backed-up folder isn't on the server any more (deleted there): register it again."""


def sha256_file(path: str | Path, start: int = 0, length: int | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        fh.seek(start)
        left = length
        while left is None or left > 0:
            block = fh.read(BLOCK if left is None else min(BLOCK, left))
            if not block:
                break
            h.update(block)
            if left is not None:
                left -= len(block)
    return h.hexdigest()


class Throttle:
    """At most `kbps` KB a second across every upload (0 = no limit)."""

    def __init__(self, kbps: int = 0):
        self.rate = kbps * 1024
        self.next = time.monotonic()

    def take(self, n: int) -> None:
        if not self.rate:
            return
        now = time.monotonic()
        self.next = max(self.next, now) + n / self.rate
        if self.next - now > 0.01:
            time.sleep(self.next - now)


class Transport:
    def __init__(self, base_url: str, token: Callable[[bool], str], *, client: httpx.Client | None = None,
                 throttle: Throttle | None = None, timeout: float = 120):
        """`token(refresh)` gives the bearer token; with True after a 401 it may get a fresh one (OAuth)."""
        self.base = base_url.rstrip("/")
        self.token = token
        self.http = client or httpx.Client(timeout=timeout, follow_redirects=False,
                                           headers={"User-Agent": f"mnemerynne-backup/{__version__}"})
        self.throttle = throttle or Throttle()

    def close(self) -> None:
        self.http.close()

    # ------------------------------------------------------------- plumbing
    def _send(self, method: str, path: str, *, auth: bool = True, body: Callable[[], object] | None = None,
              headers: dict | None = None, **kw) -> httpx.Response:
        """One request, retried on network errors and busy answers; once more with a fresh token after a 401.
        `body` makes the request body again for each try (a file is read from the start)."""
        refreshed = False
        attempt = 0
        while True:
            sent = dict(headers or {})
            if auth:
                sent["Authorization"] = f"Bearer {self.token(refreshed)}"
            try:
                r = self.http.request(method, self.base + path, headers=sent,
                                      **({"content": body()} if body else {}), **kw)
            except httpx.TransportError as exc:
                if attempt == RETRIES:
                    raise Offline(f"Can't reach {self.base}: {exc.__class__.__name__}") from exc
                time.sleep(2 ** attempt)
                attempt += 1
                continue
            if r.status_code == 401 and auth and not refreshed:
                refreshed = True
                continue
            if r.status_code in (429, 502, 503, 504) and attempt < RETRIES:
                wait = r.headers.get("retry-after", "")
                time.sleep(min(60, int(wait) if wait.isdigit() else 2 ** (attempt + 1)))
                attempt += 1
                continue
            return self._check(r)

    @staticmethod
    def _check(r: httpx.Response) -> httpx.Response:
        if r.status_code < 400:
            return r
        try:
            detail = r.json().get("detail") or r.text
        except ValueError:
            detail = r.text or r.reason_phrase
        if r.status_code == 401:
            raise SignedOut(f"Signed out: {detail}", 401)
        if r.status_code == 412:
            etag = r.headers.get("etag")
            raise Precondition(str(detail), etag.strip('"') if etag else None)
        if r.status_code in (502, 503, 504):
            raise Offline(f"The server is busy or down ({r.status_code}): {detail}", r.status_code)
        raise TransportError(str(detail), r.status_code)

    def _file_body(self, path: Path, start: int = 0, length: int | None = None) -> Callable[[], Iterator[bytes]]:
        def gen() -> Iterator[bytes]:
            with open(path, "rb") as fh:
                fh.seek(start)
                left = length
                while left is None or left > 0:
                    block = fh.read(BLOCK if left is None else min(BLOCK, left))
                    if not block:
                        return
                    self.throttle.take(len(block))
                    yield block
                    if left is not None:
                        left -= len(block)
        return gen

    @staticmethod
    def _conditions(version: str | None, mtime_ns: int | None) -> dict:
        headers = {"If-Match": f'"{version}"'} if version else {"If-None-Match": "*"}
        if mtime_ns is not None:
            headers["X-Mtime"] = f"{mtime_ns / 1e9:.6f}"
        return headers

    # ----------------------------------------------------------------- calls
    def client_info(self) -> dict:
        """The server's backup address and the newest app (no sign-in needed)."""
        return self._send("GET", "/api/sync/client", auth=False).json()

    def roots(self) -> list[dict]:
        return self._send("GET", "/api/sync/roots").json()["roots"]

    def register(self, computer: str, label: str) -> dict:
        return self._send("POST", "/api/sync/roots", json={"computer": computer, "label": label}).json()

    def set_searchable(self, root_id: int, searchable: bool) -> dict:
        return self._root_call("PATCH", root_id, "", json={"searchable": searchable}).json()

    def _root_call(self, method: str, root_id: int, tail: str, **kw) -> httpx.Response:
        try:
            return self._send(method, f"/api/sync/roots/{root_id}{tail}", **kw)
        except TransportError as exc:
            if (exc.status == 404 and "backed-up folder" in str(exc)) or \
                    (exc.status == 409 and "register it again" in str(exc)):
                raise RootGone(str(exc), exc.status) from None
            raise

    def manifest(self, root_id: int) -> dict:
        """{"folders": [...], "files": {path: entry}} over every page."""
        folders, files, after = [], {}, 0
        while True:
            page = self._root_call("GET", root_id, "/manifest", params={"after": after}).json()
            folders += page["folders"]
            files.update({f["path"]: f for f in page["files"]})
            if page["next"] is None:
                return {"folders": folders, "files": files}
            after = page["next"]

    def put_file(self, root_id: int, rel: str, local: Path, sha: str, size: int, *, version: str | None,
                 mtime_ns: int | None = None, state=None) -> str:
        """Send one file; returns the server's new version. Big files go in parts (resumed through `state`)."""
        if size > SMALL:
            return self._put_parts(root_id, rel, local, sha, size, version=version, mtime_ns=mtime_ns, state=state)
        headers = {"X-Sha256": sha, "Content-Length": str(size), **self._conditions(version, mtime_ns)}
        r = self._root_call("PUT", root_id, "/files", params={"path": rel}, headers=headers,
                            body=self._file_body(local))
        return r.json()["version"]

    def _put_parts(self, root_id, rel, local, sha, size, *, version, mtime_ns, state) -> str:
        upload_id = state.upload(root_id, rel, sha) if state else None
        info = None
        if upload_id:
            try:
                info = self._send("GET", f"/api/sync/uploads/{upload_id}").json()
            except TransportError as exc:
                if exc.status != 404:
                    raise
        if info is None:
            info = self._send("POST", "/api/sync/uploads",
                              json={"root_id": root_id, "path": rel, "size": size, "sha256": sha}).json()
            if state:
                state.set_upload(root_id, rel, sha, info["id"])
        part, have = info["part_size"], set(info["parts"])
        for n in range(-(-size // part)):
            if n in have:
                continue
            start = n * part
            length = min(part, size - start)
            self._send("PUT", f"/api/sync/uploads/{info['id']}/parts/{n}", body=self._file_body(local, start, length),
                       headers={"X-Sha256": sha256_file(local, start, length), "Content-Length": str(length)})
        try:
            r = self._send("POST", f"/api/sync/uploads/{info['id']}/finish",
                           headers=self._conditions(version, mtime_ns))
        except TransportError as exc:
            if exc.status != 412 and state:
                state.set_upload(root_id, rel, sha, None)   # the server dropped it; start over next time
            raise
        if state:
            state.set_upload(root_id, rel, sha, None)
        return r.json()["version"]

    def delete(self, root_id: int, paths: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(paths), 500):
            out += self._root_call("POST", root_id, "/delete", json={"paths": paths[i:i + 500]}).json()["results"]
        return out

    def move(self, root_id: int, moves: list[tuple[str, str]]) -> list[dict]:
        out = []
        for i in range(0, len(moves), 500):
            batch = [{"from": a, "to": b} for a, b in moves[i:i + 500]]
            out += self._root_call("POST", root_id, "/move", json={"moves": batch}).json()["results"]
        return out

    def versions(self, root_id: int, rel: str) -> list[dict]:
        return self._root_call("GET", root_id, "/versions", params={"path": rel}).json()["versions"]

    def download(self, root_id: int, rel: str, dest: Path, *, version_id: int | None = None) -> None:
        """Save the server's copy (or one of its earlier versions) to `dest`, through a temporary file."""
        params = {"path": rel, **({"version": version_id} if version_id else {})}
        tmp = dest.with_name(f".{dest.name}.mnemerynne-restore")
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = {"Authorization": f"Bearer {self.token(False)}"}
        try:
            with self.http.stream("GET", f"{self.base}/api/sync/roots/{root_id}/files", params=params,
                                  headers=headers) as r:
                if r.status_code >= 400:
                    r.read()
                    self._check(r)
                with open(tmp, "wb") as fh:
                    for block in r.iter_bytes(BLOCK):
                        fh.write(block)
                mtime = r.headers.get("x-mtime")
            if mtime:
                os.utime(tmp, (float(mtime), float(mtime)))
            os.replace(tmp, dest)
        except httpx.TransportError as exc:
            raise Offline(f"Can't reach {self.base}: {exc.__class__.__name__}") from exc
        finally:
            tmp.unlink(missing_ok=True)
