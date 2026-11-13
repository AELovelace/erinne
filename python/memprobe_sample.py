# Sampling probe: run INSIDE the live web app (Python 3.14 sys.remote_exec). It only reads.
# For 20 seconds it looks at every thread 100 times a second and counts where the busy ones are (threads waiting
# on a lock, a queue, a socket or a sleep don't count): a CPU profile of the live app. It returns at once, does its
# sampling on a thread of its own, and writes /var/lib/mnemerynne/memprobe_sample.txt when done.
#
#   sudo install -o mnemerynne -m 644 memprobe_sample.py /var/lib/mnemerynne/memprobe_sample.py
#   PID=$(systemctl show -p MainPID --value mnemerynne-web)
#   sudo python3.14 -c "import sys; sys.remote_exec($PID, '/var/lib/mnemerynne/memprobe_sample.py')"
#   sleep 30; sudo cat /var/lib/mnemerynne/memprobe_sample.txt
import collections
import sys
import threading
import time

OUT = "/var/lib/mnemerynne/memprobe_sample.txt"
SECONDS, EVERY = 20.0, 0.01

# The innermost function of a thread that is waiting, not working.
_WAITING = {"wait", "acquire", "get", "select", "poll", "sleep", "_worker", "read", "recv", "recv_into", "accept",
            "readline", "_wait_for_tstate_lock", "join", "run_forever", "_run_once", "epoll", "__sleep0"}


def _where(frame) -> str:
    code = frame.f_code
    name = code.co_filename.split("site-packages/")[-1].split("/opt/mnemerynne/")[-1].split("/python3.14/")[-1]
    return f"{name}:{frame.f_lineno} {code.co_name}"


def _sample() -> None:
    me = threading.get_ident()
    busy_at, busy_ours, samples, busy = collections.Counter(), collections.Counter(), 0, 0
    until = time.monotonic() + SECONDS
    while time.monotonic() < until:
        for ident, frame in sys._current_frames().items():
            if ident == me or frame.f_code.co_name in _WAITING:
                continue                                   # this probe, or a thread that's waiting
            busy += 1
            busy_at[_where(frame)] += 1                    # exactly where it is
            walk = frame
            while walk is not None and "/opt/mnemerynne/mnemerynne/" not in walk.f_code.co_filename:
                walk = walk.f_back
            busy_ours[_where(walk) if walk else "(outside our code)"] += 1   # the app line that led there
        samples += 1
        time.sleep(EVERY)
    lines = [time.ctime(), f"{samples} samples over {SECONDS:.0f} s; busy thread-samples: {busy}",
             f"(busy {busy / max(samples, 1):.2f} threads on average)",
             "--- our code that was busy (or led to the busy code), share of busy samples"]
    lines += [f"{n * 100 / max(busy, 1):6.1f}%  {w}" for w, n in busy_ours.most_common(25)]
    lines += ["--- exactly where busy threads were"]
    lines += [f"{n * 100 / max(busy, 1):6.1f}%  {w}" for w, n in busy_at.most_common(20)]
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


threading.Thread(target=_sample, name="memprobe-sample", daemon=True).start()
