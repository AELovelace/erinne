# Memory probe: run INSIDE the live web app (Python 3.14 sys.remote_exec) to see what fills its memory.
# It only reads and counts; it changes nothing. It writes /var/lib/mnemerynne/memprobe.txt
# (the service's own state folder: the one place it may write, and readable from outside its private /tmp).
#
#   sudo install -o mnemerynne -m 644 memprobe.py /var/lib/mnemerynne/memprobe.py
#   PID=$(systemctl show -p MainPID --value mnemerynne-web)
#   sudo python3.14 -c "import sys; sys.remote_exec($PID, '/var/lib/mnemerynne/memprobe.py')"
#   sleep 10; sudo cat /var/lib/mnemerynne/memprobe.txt
import collections
import gc
import sys
import threading
import time

OUT = "/var/lib/mnemerynne/memprobe.txt"          # where the report goes
lines = [time.ctime(), f"threads: {threading.active_count()}"]   # when, and how many threads are alive

# 1. Every object the garbage collector tracks (containers, class instances), counted by type.
counts, sizes = collections.Counter(), collections.Counter()   # how many of each type, and their own sizes
for obj in gc.get_objects():
    kind = type(obj)
    name = f"{kind.__module__}.{kind.__qualname__}"
    counts[name] += 1
    try:
        sizes[name] += sys.getsizeof(obj)           # the object itself, not what it points to
    except Exception:
        pass
lines.append(f"objects tracked: {sum(counts.values()):,}")
lines.append("--- most common types (count, own size, type)")
for name, n in counts.most_common(30):
    lines.append(f"{n:>12,} {sizes[name] / 1048576:>9.1f} MB  {name}")

# 2. Big containers kept at module level or on a module's objects: unbounded caches show up here.
found = []                                          # (length, where)
for mod_name, mod in list(sys.modules.items()):
    if not mod_name.startswith(("mnemerynne", "starlette", "fastapi", "uvicorn", "anyio", "asyncio")):
        continue                                    # our app and the web stack only
    for attr, value in list(vars(mod).items()):
        holders = [(f"{mod_name}.{attr}", value)]
        inner = getattr(value, "__dict__", None)    # a module-level object (a hub, a limiter): look one level in
        if isinstance(inner, dict) and not isinstance(value, type):
            holders += [(f"{mod_name}.{attr}.{k}", v) for k, v in list(inner.items())]
        for where, thing in holders:
            if isinstance(thing, (dict, list, set, collections.deque)):
                try:
                    found.append((len(thing), where))
                except Exception:
                    pass
lines.append("--- biggest module-level containers (items, where)")
for n, where in sorted(found, reverse=True)[:25]:
    lines.append(f"{n:>12,}  {where}")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")               # the report
