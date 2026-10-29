"""Whether this computer is on a metered connection (a phone's hotspot), so backups can wait.

Windows reports it per connection; on Linux, NetworkManager does (nmcli). Elsewhere, or if asking fails, the
connection counts as not metered. Answers are kept for five minutes.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

_cache: tuple[float, bool] | None = None
TTL = 300

_PS = ("[Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,"
       "ContentType=WindowsRuntime] | Out-Null; $p = [Windows.Networking.Connectivity.NetworkInformation]::"
       "GetInternetConnectionProfile(); if ($p) { $p.GetConnectionCost().NetworkCostType }")


def _ask() -> bool:
    try:
        if sys.platform == "win32":
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
                                 capture_output=True, text=True, timeout=15,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            return out.strip().lower() in ("fixed", "variable")
        if shutil.which("nmcli"):
            out = subprocess.run(["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.METERED", "dev", "show"],
                                 capture_output=True, text=True, timeout=15).stdout
            connected = False
            for line in out.splitlines():
                key, _, value = line.partition(":")
                if key == "GENERAL.STATE":
                    connected = value.startswith("100")
                elif key == "GENERAL.METERED" and connected and value.startswith("yes"):
                    return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def metered() -> bool:
    global _cache
    now = time.monotonic()
    if _cache is None or now - _cache[0] > TTL:
        _cache = (now, _ask())
    return _cache[1]
