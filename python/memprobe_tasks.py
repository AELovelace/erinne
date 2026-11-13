# Stuck-request probe: run INSIDE the live web app (Python 3.14 sys.remote_exec). It only reads.
# Groups every live asyncio task by the URL it is serving and the line it is waiting on, and writes
# /var/lib/mnemerynne/memprobe_tasks.txt (the service's state folder).
#
#   sudo install -o mnemerynne -m 644 memprobe_tasks.py /var/lib/mnemerynne/memprobe_tasks.py
#   PID=$(systemctl show -p MainPID --value mnemerynne-web)
#   sudo python3.14 -c "import sys; sys.remote_exec($PID, '/var/lib/mnemerynne/memprobe_tasks.py')"
#   sleep 10; sudo cat /var/lib/mnemerynne/memprobe_tasks.txt
import asyncio
import collections
import gc
import time

OUT = "/var/lib/mnemerynne/memprobe_tasks.txt"


def _frames(coro):
    """The frames of a coroutine chain, outermost first (what awaits what, down to where it waits)."""
    seen = 0
    while coro is not None and seen < 200:          # a safety bound on very deep chains
        frame = getattr(coro, "cr_frame", None) or getattr(coro, "ag_frame", None) or getattr(coro, "gi_frame", None)
        if frame is not None:
            yield frame
        coro = getattr(coro, "cr_await", None) or getattr(coro, "ag_await", None) or getattr(coro, "gi_yieldfrom", None)
        seen += 1


def _path(frames):
    """The request this task serves: the first frame holding an ASGI scope (or a Request) with a path."""
    for frame in frames:
        for value in frame.f_locals.values():
            if isinstance(value, dict) and value.get("type") in ("http", "websocket") and "path" in value:
                return f"{value.get('method', 'WS')} {value['path']}"
            scope = getattr(value, "scope", None)
            if isinstance(scope, dict) and "path" in scope:
                return f"{scope.get('method', 'WS')} {scope['path']}"
    return "(no request)"


by_path, by_wait = collections.Counter(), collections.Counter()   # how many tasks per URL, per waiting line
tasks = [obj for obj in gc.get_objects() if isinstance(obj, asyncio.Task) and not obj.done()]
for task in tasks:
    frames = list(_frames(task.get_coro()))
    path = _path(frames)
    if "/api/" in path or "/media/" in path:        # group URLs with ids in them: /parties/65BF36B2 -> /parties/*
        parts = path.split("/")
        path = "/".join("*" if any(ch.isdigit() for ch in p) and len(p) > 3 else p for p in parts)
    by_path[path] += 1
    if frames:
        inner = frames[-1]                          # the innermost frame: where it is waiting right now
        code = inner.f_code
        by_wait[f"{code.co_filename.split('site-packages/')[-1].split('/opt/mnemerynne/')[-1]}:{inner.f_lineno} {code.co_name}"] += 1

lines = [time.ctime(), f"live tasks: {len(tasks):,}", "--- by request (tasks, request)"]
lines += [f"{n:>10,}  {p}" for p, n in by_path.most_common(25)]
lines += ["--- by where they wait (tasks, file:line function)"]
lines += [f"{n:>10,}  {w}" for w, n in by_wait.most_common(15)]
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
