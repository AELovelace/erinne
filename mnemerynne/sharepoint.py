"""SharePoint document libraries in Mnemerynne: indexed, searched, read-only.

An admin picks libraries on Settings → Microsoft 365 → SharePoint. Each one
becomes a Mnemerynne library (collections.source = 'sharepoint') that the
worker keeps in step with the SharePoint drive through Graph delta queries,
using the tenant's app-only token under Sites.Selected (Mnemerynne only sees
sites a tenant admin granted it). Files are downloaded into blob storage and
indexed like uploads. Nothing is ever written to SharePoint, and nobody can
change these libraries in Mnemerynne (permissions.reach gives no write).

Who sees what follows SharePoint, not Mnemerynne grants: the library is open
to everyone with a linked Microsoft account in that tenant (m365.py), and
each file is checked with that person's own token before it is shown, cited
or opened (`visible`), the answer kept for CHECK_MINUTES. This is the one
place results are filtered after search (retrieval.search's `keep`): per-file
SharePoint permissions can't be turned into a search scope.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import tempfile
from typing import Callable
from urllib.parse import urlsplit

import httpx

from mnemerynne import fs, jobs, m365, share
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import store

log = logging.getLogger("mnemerynne.sharepoint")

CHECK_MINUTES = 15
_SELECT = "id,name,folder,file,deleted,parentReference,cTag,size,webUrl,root"
_settings: Settings | None = None


class SharePointError(m365.M365Error):
    pass


def configure(settings: Settings) -> None:
    """The settings `visible` uses (fs.Access calls it without settings at hand)."""
    global _settings
    _settings = settings


def _app_graph(conn: sqlite3.Connection, settings: Settings, tenant_row: int) -> m365.Graph:
    return m365.Graph(m365.app_token(settings, m365.tenant(conn, tenant_row)))


# ------------------------------------------------------------------- admin
def resolve_site(conn: sqlite3.Connection, settings: Settings, tenant_row: int, site_url: str) -> dict:
    """A site from its address, with its document libraries."""
    parts = urlsplit((site_url or "").strip())
    if parts.scheme != "https" or not parts.hostname or not parts.hostname.endswith(".sharepoint.com"):
        raise SharePointError("Paste the site's address, e.g. https://contoso.sharepoint.com/sites/FrontOffice.")
    match = re.match(r"^/(sites|teams)/[^/]+", parts.path)
    path = f"{parts.hostname}:{match.group(0)}" if match else parts.hostname
    graph = _app_graph(conn, settings, tenant_row)
    try:
        site = graph.get(f"/sites/{path}", {"$select": "id,displayName,webUrl"})
        drives = graph.get(f"/sites/{site['id']}/drives", {"$select": "id,name,webUrl,driveType"})
    except m365.GraphError as exc:
        if exc.status in (401, 403, 404):
            raise SharePointError("Mnemerynne can't open that site. A tenant admin grants it read access per site "
                                  "(Sites.Selected; see the deploy guide, section 5i).") from None
        raise
    taken = {r["drive_id"] for r in conn.execute("SELECT drive_id FROM sharepoint_drives WHERE tenant_row = ?",
                                                 (tenant_row,))}
    return {"site_id": site["id"], "name": site.get("displayName") or path, "web_url": site.get("webUrl", ""),
            "libraries": [{"id": d["id"], "name": d["name"], "web_url": d.get("webUrl", ""), "added": d["id"] in taken}
                          for d in drives.get("value", []) if d.get("driveType") == "documentLibrary"]}


def add_libraries(conn: sqlite3.Connection, settings: Settings, tenant_row: int, site_url: str,
                  drive_ids: list[str]) -> list[int]:
    """Make the chosen libraries Mnemerynne libraries; the worker fills them."""
    site = resolve_site(conn, settings, tenant_row, site_url)
    chosen = [d for d in site["libraries"] if d["id"] in set(drive_ids) and not d["added"]]
    if not chosen:
        raise SharePointError("Pick at least one library that isn't added yet.")
    made = []
    with transaction(conn):
        for d in chosen:
            name = f"{site['name']} · {d['name']}"
            n = 2
            while conn.execute("SELECT 1 FROM collections WHERE kind = 'library' AND name = ? COLLATE NOCASE",
                               (name,)).fetchone():
                name, n = f"{site['name']} · {d['name']} ({n})", n + 1
            # Off for Claude until an admin switches it on (Access → Libraries), like the rest of the plan.
            cid = conn.execute("INSERT INTO collections(kind, name, source, claude_enabled) "
                               "VALUES ('library', ?, 'sharepoint', 0)", (name,)).lastrowid
            conn.execute("INSERT INTO sharepoint_drives(collection_id, tenant_row, site_id, site_url, drive_id, web_url) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (cid, tenant_row, site["site_id"], site["web_url"] or site_url,
                                                       d["id"], d["web_url"]))
            made.append(cid)
    return made


def remove_library(conn: sqlite3.Connection, settings: Settings, drive_row: int) -> tuple[str, int] | None:
    """Stop syncing a library and delete Mnemerynne's copy (SharePoint is untouched).
    Returns (name, files removed), or None if there's no such library."""
    row = conn.execute("SELECT d.collection_id, c.name FROM sharepoint_drives d JOIN collections c "
                       "ON c.id = d.collection_id WHERE d.id = ?", (drive_row,)).fetchone()
    if row is None:
        return None
    file_ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE collection_id = ?", (row["collection_id"],))]
    with transaction(conn):
        for file_id in file_ids:
            store.delete_record(conn, settings, file_id)
        conn.execute("DELETE FROM collections WHERE id = ?", (row["collection_id"],))   # the drive row cascades
    return row["name"], len(file_ids)


def libraries(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT d.*, c.name, t.label FROM sharepoint_drives d JOIN collections c ON c.id = d.collection_id "
                        "JOIN m365_tenants t ON t.id = d.tenant_row ORDER BY c.name COLLATE NOCASE").fetchall()
    return [{"id": r["id"], "collection_id": r["collection_id"], "name": r["name"], "tenant": r["label"],
             "web_url": r["web_url"], "status": r["status"], "error": r["error"], "files": r["files"],
             "last_sync_at": r["last_sync_at"]} for r in rows]


# -------------------------------------------------------------------- sync
def sync_all(conn: sqlite3.Connection, settings: Settings) -> dict:
    stats = {"libraries": 0, "errors": 0}
    for row in conn.execute("SELECT d.id FROM sharepoint_drives d JOIN m365_tenants t ON t.id = d.tenant_row "
                            "WHERE t.enabled = 1 ORDER BY d.id").fetchall():
        result = sync_drive(conn, settings, row["id"])
        stats["libraries"] += 1
        stats["errors"] += bool(result.get("error"))
    return stats


def sync_drive(conn: sqlite3.Connection, settings: Settings, drive_row: int) -> dict:
    """Bring one library up to date. Without a delta link (first sync, or
    Microsoft asked for a full one), everything not seen is removed after."""
    d = conn.execute("SELECT * FROM sharepoint_drives WHERE id = ?", (drive_row,)).fetchone()
    stats = {"added": 0, "changed": 0, "removed": 0, "skipped": 0}
    # One sync per library at a time, across processes (the worker, "Sync now"); a claim older than an hour
    # is from a sync that died.
    if not conn.execute("UPDATE sharepoint_drives SET status = 'syncing', sync_started_at = datetime('now') "
                        "WHERE id = ? AND (status != 'syncing' OR sync_started_at < datetime('now', '-60 minutes'))",
                        (drive_row,)).rowcount:
        return {**stats, "busy": True}
    try:
        token = m365.app_token(settings, m365.tenant(conn, d["tenant_row"]))
        graph = m365.Graph(token)
        full = not d["delta_link"]
        url = d["delta_link"] or f"/drives/{d['drive_id']}/root/delta?$select={_SELECT}"
        try:
            items, delta_link = _changes(graph, url)
        except m365.GraphError as exc:
            if exc.status != 410 or full:   # 410: the delta link expired; start over
                raise
            full = True
            items, delta_link = _changes(graph, f"/drives/{d['drive_id']}/root/delta?$select={_SELECT}")
        seen = _apply(conn, settings, d, token, items, stats)
        if full:
            stats["removed"] += _sweep(conn, settings, d["collection_id"], seen)
        count = conn.execute("SELECT count(*) FROM files WHERE collection_id = ? AND deleted_at IS NULL",
                             (d["collection_id"],)).fetchone()[0]
        conn.execute("UPDATE sharepoint_drives SET delta_link = ?, last_sync_at = datetime('now'), status = 'ok', "
                     "error = NULL, files = ? WHERE id = ?", (delta_link, count, drive_row))
    except (m365.M365Error, httpx.HTTPError, OSError) as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        log.warning("SharePoint sync of drive %s failed: %s", d["drive_id"], exc)
        conn.execute("UPDATE sharepoint_drives SET status = 'error', error = ?, last_sync_at = datetime('now') "
                     "WHERE id = ?", (str(exc)[:500], drive_row))
        return {**stats, "error": str(exc)}
    return stats


def _changes(graph: m365.Graph, url: str) -> tuple[list[dict], str | None]:
    items: list[dict] = []
    body = graph.get(url)
    for _ in range(10_000):
        items.extend(body.get("value", []))
        if body.get("@odata.nextLink"):
            body = graph.get(body["@odata.nextLink"])
            continue
        return items, body.get("@odata.deltaLink")
    raise SharePointError("The library's change list didn't end.")


def _apply(conn: sqlite3.Connection, settings: Settings, d: sqlite3.Row, token: str, items: list[dict],
           stats: dict) -> set[str]:
    cid = d["collection_id"]
    seen: set[str] = set()
    root_ids = {i["id"] for i in items if "root" in i}
    folders = {r["remote_id"]: r["id"] for r in conn.execute(
        "SELECT id, remote_id FROM folders WHERE collection_id = ? AND remote_id IS NOT NULL", (cid,))}

    def parent_of(item: dict) -> tuple[bool, int | None]:
        pid = (item.get("parentReference") or {}).get("id")
        if pid in root_ids or not pid:
            return True, None
        if pid in folders:
            return True, folders[pid]
        return False, None

    # Folders first, parents before children (delta doesn't promise an order).
    pending = [i for i in items if "folder" in i and "root" not in i and "deleted" not in i]
    for _ in range(64):
        waiting = []
        for item in pending:
            known, parent = parent_of(item)
            if not known:
                waiting.append(item)
                continue
            seen.add(item["id"])
            folders[item["id"]] = _folder(conn, cid, item, parent)
        if not waiting or len(waiting) == len(pending):
            break
        pending = waiting
    for item in items:
        if "deleted" in item:
            stats["removed"] += _remove(conn, settings, cid, item["id"])
            folders.pop(item["id"], None)
        elif "file" in item:
            seen.add(item["id"])
            known, parent = parent_of(item)
            if known:
                _file(conn, settings, d, token, item, parent, stats)
            else:
                stats["skipped"] += 1
    return seen


def _folder(conn: sqlite3.Connection, cid: int, item: dict, parent: int | None) -> int:
    name = fs.clean_name(item["name"])
    row = conn.execute("SELECT id, name, parent_id FROM folders WHERE collection_id = ? AND remote_id = ?",
                       (cid, item["id"])).fetchone()
    if row is None:
        folder_id = fs.ensure_path(conn, cid, parent, [name], None)
        conn.execute("UPDATE folders SET remote_id = ? WHERE id = ?", (item["id"], folder_id))
        return folder_id
    if row["name"] != name or row["parent_id"] != parent:   # renamed or moved in SharePoint
        try:
            conn.execute("UPDATE folders SET name = ?, parent_id = ? WHERE id = ?", (name, parent, row["id"]))
        except sqlite3.IntegrityError:
            log.warning("SharePoint folder %s clashes with another of the same name; keeping the old place.", name)
        files = [r[0] for r in conn.execute("SELECT id FROM files WHERE folder_id IN (%s)" % ",".join(
            "?" * len(fs.subtree(conn, row["id"]))), fs.subtree(conn, row["id"]))]
        fs._sync_scope(conn, files)
    return row["id"]


def _file(conn: sqlite3.Connection, settings: Settings, d: sqlite3.Row, token: str, item: dict,
          parent: int | None, stats: dict) -> None:
    cid = d["collection_id"]
    row = conn.execute("SELECT id, display_name, folder_id, remote_etag, blob_sha256 FROM files "
                       "WHERE collection_id = ? AND remote_id = ? AND deleted_at IS NULL", (cid, item["id"])).fetchone()
    tag = item.get("cTag") or item.get("eTag") or ""
    if int(item.get("size") or 0) > settings.max_file_bytes:
        stats["skipped"] += 1
        return
    if row is not None and row["remote_etag"] == tag:
        if row["display_name"] != item["name"] or row["folder_id"] != parent:   # renamed or moved only
            name = fs.unique_name(conn, cid, parent, store.clean_display_name(item["name"]), "file")
            conn.execute("UPDATE files SET display_name = ?, folder_id = ?, web_url = ? WHERE id = ?",
                         (name, parent, item.get("webUrl"), row["id"]))
            fs._sync_scope(conn, [row["id"]])
            stats["changed"] += 1
        return
    received = _download(settings, token, d["drive_id"], item)
    if row is None:
        file_id, _ = store.add_file(conn, settings, received, cid, None, folder_id=parent)
        conn.execute("UPDATE files SET remote_id = ?, remote_etag = ?, web_url = ? WHERE id = ?",
                     (item["id"], tag, item.get("webUrl"), file_id))
        stats["added"] += 1
        return
    with transaction(conn):   # new content for a file we have: same id, so citations keep working
        store.store(conn, settings, received)
        kind = conn.execute("SELECT kind FROM blobs WHERE sha256 = ?", (received.sha256,)).fetchone()["kind"]
        status = "stored" if kind == "other" else "queued"
        conn.execute("UPDATE files SET blob_sha256 = ?, status = ?, error = NULL, progress = NULL, notes = ?, "
                     "remote_etag = ?, web_url = ?, display_name = ?, folder_id = ?, updated_at = datetime('now') "
                     "WHERE id = ?", (received.sha256, status, received.note, tag, item.get("webUrl"),
                                      row["display_name"] if row["display_name"] == item["name"] else
                                      fs.unique_name(conn, cid, parent, received.display_name, "file"),
                                      parent, row["id"]))
        if status == "queued":
            jobs.enqueue(conn, "ingest", row["id"])
        share._drop_unused_blob(conn, settings, row["blob_sha256"])
        fs._sync_scope(conn, [row["id"]])
    stats["changed"] += 1


def _download(settings: Settings, token: str, drive_id: str, item: dict) -> store.Received:
    """The item's content into a temp file (Graph answers with a pre-signed download link)."""
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with m365._http() as client:
        r = client.get(f"{m365.GRAPH}/drives/{drive_id}/items/{item['id']}/content",
                       headers={"Authorization": f"Bearer {token}"})
        if r.status_code in (301, 302, 303, 307, 308):
            url, headers = r.headers["location"], {}       # pre-signed: no token goes with it
        elif r.status_code == 200:
            url, headers = None, None
        else:
            raise m365.GraphError(r.status_code, f"Couldn't download {item['name']} ({r.status_code}).")
        with tempfile.TemporaryFile(dir=settings.tmp_dir) as tmp:
            if url is None:
                tmp.write(r.content)
            else:
                with client.stream("GET", url, headers=headers) as got:
                    if got.status_code != 200:
                        raise m365.GraphError(got.status_code, f"Couldn't download {item['name']}.")
                    for block in got.iter_bytes(1 << 20):
                        tmp.write(block)
            tmp.seek(0)
            return store.receive(tmp, item["name"], settings)


def _remove(conn: sqlite3.Connection, settings: Settings, cid: int, remote_id: str) -> int:
    row = conn.execute("SELECT id FROM folders WHERE collection_id = ? AND remote_id = ?", (cid, remote_id)).fetchone()
    if row is not None:
        n = len(conn.execute(f"SELECT id FROM files WHERE folder_id IN ({','.join('?' * len(fs.subtree(conn, row['id'])))})",
                             fs.subtree(conn, row["id"])).fetchall())
        fs._purge_folder(conn, settings, row["id"])
        return n
    row = conn.execute("SELECT id FROM files WHERE collection_id = ? AND remote_id = ?", (cid, remote_id)).fetchone()
    if row is not None:
        store.delete_file(conn, settings, row["id"])
        return 1
    return 0


def _sweep(conn: sqlite3.Connection, settings: Settings, cid: int, seen: set[str]) -> int:
    """After a full sync: remove what SharePoint no longer has."""
    gone = 0
    for row in conn.execute("SELECT id, remote_id FROM files WHERE collection_id = ?", (cid,)).fetchall():
        if row["remote_id"] not in seen:
            store.delete_file(conn, settings, row["id"])
            gone += 1
    for row in conn.execute("SELECT id, remote_id FROM folders WHERE collection_id = ? ORDER BY id DESC",
                            (cid,)).fetchall():
        if row["remote_id"] not in seen and conn.execute("SELECT 1 FROM folders WHERE id = ?", (row["id"],)).fetchone():
            fs._purge_folder(conn, settings, row["id"])
    return gone


# ---------------------------------------------------------------- trimming
def sharepoint_ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT id FROM collections WHERE source = 'sharepoint'")}


def visible(conn: sqlite3.Connection, user_id: int, file_ids) -> set[int]:
    """Of these files, the ones this person may see. Files outside SharePoint
    always; SharePoint files when SharePoint lets the person's own linked
    account open them (checked in batches of 20, cached CHECK_MINUTES). No
    linked account in that tenant, or Microsoft unreachable: not visible."""
    file_ids = list(dict.fromkeys(file_ids))
    if not file_ids:
        return set()
    marks = ",".join("?" * len(file_ids))
    rows = conn.execute(f"SELECT f.id, f.remote_id, d.drive_id, d.tenant_row FROM files f "
                        f"JOIN sharepoint_drives d ON d.collection_id = f.collection_id WHERE f.id IN ({marks})",
                        file_ids).fetchall()
    out = set(file_ids) - {r["id"] for r in rows}
    if not rows:
        return out
    keys = {r["id"]: f"{r['drive_id']}:{r['remote_id']}" for r in rows}
    cached = {r["item_key"]: bool(r["ok"]) for r in conn.execute(
        f"SELECT item_key, ok FROM sharepoint_access WHERE user_id = ? AND checked_at > datetime('now', ?) "
        f"AND item_key IN ({','.join('?' * len(keys))})", (user_id, f"-{CHECK_MINUTES} minutes", *keys.values()))}
    todo: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        if keys[r["id"]] in cached:
            if cached[keys[r["id"]]]:
                out.add(r["id"])
        elif r["remote_id"]:
            todo.setdefault(r["tenant_row"], []).append(r)
    for tenant_row, items in todo.items():
        identity = conn.execute("SELECT id FROM m365_identities WHERE user_id = ? AND tenant_row = ? AND status = 'ok' "
                                "ORDER BY id LIMIT 1", (user_id, tenant_row)).fetchone()
        if identity is None or _settings is None:
            continue
        try:
            graph = m365.graph_for(conn, _settings, identity["id"])
            answers = graph.batch([{"id": str(r["id"]), "url": f"/drives/{r['drive_id']}/items/{r['remote_id']}?$select=id"}
                                   for r in items])
        except (m365.M365Error, httpx.HTTPError) as exc:
            log.warning("Couldn't check SharePoint access for user %s: %s", user_id, exc)
            continue                                   # fail closed, and don't remember it
        now = [(user_id, keys[r["id"]], int(answers.get(str(r["id"]), {}).get("status") == 200)) for r in items]
        conn.executemany("INSERT INTO sharepoint_access(user_id, item_key, ok, checked_at) VALUES (?, ?, ?, "
                         "datetime('now')) ON CONFLICT(user_id, item_key) DO UPDATE SET ok = excluded.ok, "
                         "checked_at = excluded.checked_at", now)
        out |= {r["id"] for r, (_, _, ok) in zip(items, now) if ok}
    return out


def trimmer(conn: sqlite3.Connection, user_id: int) -> Callable[[list], list] | None:
    """For retrieval.search: drop SharePoint hits the person can't open (None
    when no SharePoint library is involved, so nothing is checked)."""
    if not sharepoint_ids(conn):
        return None

    def keep(hits: list) -> list:
        ok = visible(conn, user_id, [h.file_id for h in hits])
        return [h for h in hits if h.file_id in ok]
    return keep


def web_urls(conn: sqlite3.Connection, file_ids) -> dict[int, str]:
    """SharePoint addresses of these files ("Open in SharePoint")."""
    ids = [i for i in dict.fromkeys(file_ids) if i]
    if not ids:
        return {}
    return {r[0]: r[1] for r in conn.execute(f"SELECT id, web_url FROM files WHERE web_url IS NOT NULL AND id IN "
                                             f"({','.join('?' * len(ids))})", ids)}


def purge_access_cache(conn: sqlite3.Connection) -> int:
    return conn.execute("DELETE FROM sharepoint_access WHERE checked_at < datetime('now', '-1 day')").rowcount
