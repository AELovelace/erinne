# Thread probe: run INSIDE the live web app (Python 3.14 sys.remote_exec). It only reads.
# Shows what every thread is doing right now (the worker threads that run database and file work), grouped by
# the same stack, and writes /var/lib/mnemerynne/memprobe_threads.txt (the service's state folder).
#
#   sudo install -o mnemerynne -m 644 memprobe_threads.py /var/lib/mnemerynne/memprobe_threads.py
#   PID=$(systemctl show -p MainPID --value mnemerynne-web)
#   sudo python3.14 -c "import sys; sys.remote_exec($PID, '/var/lib/mnemerynne/memprobe_threads.py')"
#   sleep 10; sudo cat /var/lib/mnemerynne/memprobe_threads.txt
import collections
import sys
import threading
import time
import traceback

OUT = "/var/lib/mnemerynne/memprobe_threads.txt"
names = {t.ident: t.name for t in threading.enumerate()}           # thread id -> its name
stacks = collections.Counter()                                     # the same stack, how many threads show it
for ident, frame in sys._current_frames().items():
    summary = traceback.extract_stack(frame)[-8:]                   # the innermost 8 calls: where it is and why
    ours = [f for f in summary if "/opt/mnemerynne/mnemerynne/" in f.filename]
    lines = [f"    {f.filename.split('site-packages/')[-1].split('/opt/mnemerynne/')[-1]}:{f.lineno} {f.name}"
             f"  | {(f.line or '').strip()[:90]}" for f in summary]
    key = "\n".join(lines)
    stacks[key] += 1
    if ours:                                                        # also note which of our own lines it came from
        stacks[f"  (from {ours[-1].filename.split('/opt/mnemerynne/')[-1]}:{ours[-1].lineno} {ours[-1].name})"] += 0

out = [time.ctime(), f"threads: {len(names)}"]
for stack, n in stacks.most_common():
    if n:
        out += [f"--- {n} thread(s):", stack]
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
