"""The box's nginx front door, written from the service registry (docs/plans/services-rewrite.md, "nginx").

    python -m mnemerynne.services.nginx --listen 192.168.1.5:38200 [--web 127.0.0.1:38210]
                                        [--accel --env-file /etc/mnemerynne/mnemerynne.env]
    python -m mnemerynne.services.nginx --roots --env-file /etc/mnemerynne/mnemerynne.env

prints the config deploy/linux/front-door.sh installs: nginx takes the address the reverse proxy sends to and
hands each service's paths to that service (registry.Service.paths and .port, all on 127.0.0.1), everything else
to the web app. A service added to the registry gets its route the next time front-door.sh writes the config.

With --accel (Phase 1, `front-door.sh accel on`), nginx also serves /static/ from disk and sends file bytes for
the app (X-Accel-Redirect, mnemerynne/web/sendfile.py): one internal location per folder the app's settings name
(sendfile.roots) and per header set, plus a probe address per folder that only the box itself may ask for.
--roots prints those folders ("key path" per line) for front-door.sh's permission checks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mnemerynne.services import registry

HEADER = """\
# Written by deploy/linux/front-door.sh from mnemerynne/services/registry.py: edit the registry, not this file.
# nginx as the box's front door behind the reverse proxy that holds the certificates (README "nginx in front of
# the app processes"): each service's paths to its own processes, everything else to the web app.

# Websockets (Remote's LAN apps) pass their upgrade on.
map $http_upgrade $front_connection_upgrade {
    default upgrade;
    ''      close;
}
"""

ACCEL_HEADER = """
# HSTS on files nginx sends itself, as the app adds it to its own answers over https.
map $http_x_forwarded_proto $front_hsts {
    https   "max-age=31536000";
    default "";
}
"""

SERVER_SETTINGS = """\
    # The reverse proxy already limits sizes (uploads up to 60 GB to /api/files): no second limit here.
    client_max_body_size 0;
    # Bodies and answers straight through: uploads, live streams, films with Range. Nothing spooled here.
    proxy_request_buffering off;
    proxy_buffering off;
    proxy_http_version 1.1;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    # nginx's own redirects (it answers "/remote" with "/remote/" when only "/remote/" has a location) stay
    # relative: an absolute one names this box's port (:38200), which nobody outside can reach.
    absolute_redirect off;
    # What the reverse proxy said, passed on: the site's name, https, and the client's address with the reverse
    # proxy's after it. The app trusts this box and the reverse proxy (FORWARDED_ALLOW_IPS), so it still sees
    # each person's own address for login throttling and rate limits.
"""

# Passed on with every request. A location that sets any proxy_set_header of its own drops all of the server's
# (nginx doesn't merge them), so /remote/ repeats these.
PASS = ["proxy_set_header Host $host;",
        "proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header Authorization $http_authorization;"]

# The headers SecurityHeaders (web/security.py) gives the app's own answers, for the files nginx sends instead:
# after X-Accel-Redirect nginx keeps only Content-Type, Content-Disposition, Cache-Control, Expires and Set-Cookie.
CSP = {
    "plain": "frame-ancestors 'self'; base-uri 'self'; object-src 'none'",
    "sandbox": "default-src 'none'; sandbox",          # files people uploaded, shown inline (web/sendfile.py)
}
PERMISSIONS = "camera=(), microphone=(), geolocation=(), payment=()"
PROBE = ".mnemerynne-accel-probe"                      # front-door.sh writes it, asks nginx for it, removes it
STAMP_PATH = "/_front-door/stamp"                      # which config nginx is serving: front-door.sh asks


def _box_only(probe_from: str) -> list[str]:
    """A location (serving a file) only this box may ask for: its address, and nothing the reverse proxy sent
    (that always carries X-Forwarded-For; front-door.sh's own asks never do)."""
    return ["        if ($http_x_forwarded_for) { return 404; }   # came through the reverse proxy: not this box",
            f"        allow {probe_from};", "        allow 127.0.0.1;", "        deny all;"]


def _box_geo(probe_from: str) -> str:
    """$front_door_box: 1 for this box's own addresses. The stamp answers with `return`, which nginx runs in its
    rewrite phase, before allow/deny are ever looked at, so its check has to be an `if` on this instead."""
    return ("\n# This box's own addresses, for the stamp's check (see _stamp_location).\n"
            "geo $front_door_box {\n    default 0;\n    127.0.0.1 1;\n" + f"    {probe_from} 1;\n" + "}\n")


def _upstream(name: str) -> str:
    return "mnemerynne_" + name.replace("-", "_")


def _quoted(path: Path | str) -> str:
    """A path for the config, in double quotes; one nginx couldn't read back as written is refused."""
    text = str(path).replace("\\", "/")
    if any(c in text for c in '"\n;{}$'):
        raise ValueError(f"Can't put {text!r} in the nginx config")
    return f'"{text}"'


def _security_headers(kind: str) -> list[str]:
    return ["        add_header X-Content-Type-Options nosniff always;",
            "        add_header X-Frame-Options SAMEORIGIN always;",
            "        add_header Referrer-Policy same-origin always;",
            f'        add_header Permissions-Policy "{PERMISSIONS}" always;',
            "        add_header Strict-Transport-Security $front_hsts always;",
            f'        add_header Content-Security-Policy "{CSP[kind]}" always;']


def _accel_locations(roots: dict[str, Path], probe_from: str) -> list[str]:
    lines = []
    static = roots.get("static")
    if static is not None:
        lines += ["    # The app's own files, straight from disk.",
                  "    location ^~ /static/ {",
                  f"        alias {_quoted(str(static).rstrip('/') + '/')};",
                  *_security_headers("plain"),
                  "    }", ""]
    lines.append("    # Files the app checked and handed over (X-Accel-Redirect): never reachable from outside.")
    for key, root in roots.items():
        for kind in CSP:
            lines += [f"    location ^~ /_accel/{kind}/{key}/ {{",
                      "        internal;",
                      f"        alias {_quoted(str(root).rstrip('/') + '/')};",
                      "        sendfile on;",
                      "        tcp_nopush on;",
                      "        gzip off;",
                      *_security_headers(kind),
                      "    }"]
    lines += ["", "    # front-door.sh accel: can nginx read each folder? Only this box may ask."]
    for key, root in roots.items():
        if key == "static":
            continue
        lines += [f"    location = /_accel-probe/{key} {{",
                  *_box_only(probe_from),
                  f"        alias {_quoted(str(root).rstrip('/') + '/' + PROBE)};",
                  "    }"]
    return lines + [""]


def _stamp_location(stamp: str, probe_from: str) -> list[str]:
    """The address front-door.sh asks to learn which config the running nginx loaded: the stamp it wrote into this
    one. Only this box may ask."""
    if not stamp.replace("-", "").isalnum():
        raise ValueError(f"Can't put stamp {stamp!r} in the nginx config")      # letters, digits and dashes only
    return ["    # Which config nginx is serving (front-door.sh checks after each reload). Only this box may ask:",
            "    # `return` runs before allow/deny would, so the checks are ifs, ahead of it.",
            f"    location = {STAMP_PATH} {{",
            "        if ($http_x_forwarded_for) { return 404; }   # came through the reverse proxy: not this box",
            "        if ($front_door_box = 0) { return 404; }     # from anywhere but this box",
            "        default_type text/plain;",
            f'        return 200 "{stamp}";',
            "    }", ""]


def render(listen: str, web: str = "127.0.0.1:38210", accel: dict[str, Path] | None = None,
           running: list[str] | None = None, stamp: str | None = None) -> str:
    """The whole nginx config: one upstream per service and the web app, one location per service path, and with
    `accel` (folder key -> path, web/sendfile.roots) nginx's own file locations. `running`: only these services get
    their paths (front-door.sh passes the ones whose units are enabled); the rest stay with the web app. `stamp`:
    what nginx answers at STAMP_PATH, so front-door.sh can tell this config is the one being served."""
    services = [s for s in registry.SERVICES.values() if running is None or s.name in running]
    probe_from = listen.rsplit(":", 1)[0].strip("[]")              # the box's own address, as nginx sees it
    lines = [HEADER + (ACCEL_HEADER if accel else "") + (_box_geo(probe_from) if stamp else "")]
    lines.append(f"upstream {_upstream('web')} {{ server {web}; keepalive 32; }}")
    for s in services:
        lines.append(f"upstream {_upstream(s.name)} {{ server 127.0.0.1:{s.port}; keepalive {16 * s.processes()}; }}"
                     f"   # {s.name}: {s.processes()} process(es)")
    # The app hands file bytes to nginx only for requests this nginx passed on (web/sendfile.py); without accel
    # the header is cleared, so nobody can claim it.
    accel_header = "proxy_set_header X-Mnemerynne-Accel " + ("1;" if accel else '"";')
    lines += ["", "server {", f"    listen {listen};", "    server_name _;", "", SERVER_SETTINGS.rstrip("\n")]
    lines += ["    " + h for h in PASS + [accel_header]]
    lines += ['    proxy_set_header Connection "";              # keep the connections to the processes open', ""]
    if stamp:
        lines += _stamp_location(stamp, probe_from)
    if accel:
        lines += _accel_locations(accel, probe_from)
    for s in services:
        for path in s.paths:
            lines += [f"    # {s.name}: {s.serves}", f"    location {path} {{",
                      f"        proxy_pass http://{_upstream(s.name)};", *(_websocket(accel_header) if s.websockets
                                                                            else []), "    }", ""]
    taken = {path for s in services for path in s.paths}
    stay = [(f"{s.name} leaves this to the web app", p, False) for s in services for p in s.web_paths]
    stay += [(f"{s.name} isn't running here: the web app serves it", p, s.websockets)
             for s in registry.SERVICES.values() if s not in services for p in s.paths if p not in taken]
    for why, path, websockets in stay:
        lines += [f"    # {why}", f"    location {path} {{", f"        proxy_pass http://{_upstream('web')};",
                  *(_websocket(accel_header) if websockets else []), "    }", ""]
    # A page at a slashed location's bare name (/files, /media, /remote): its own exact location, or nginx answers
    # it with a redirect to the slashed one instead of passing it on. The web app serves every page.
    slashed = [p for s in services for p in s.paths] + [p for _, p, _ in stay] + ["/remote/"]
    bare = sorted({p.rstrip("/") for p in slashed if p.endswith("/") and not p.startswith("=") and p != "/"})
    for path in bare:
        lines += [f"    # {path}: a page, not {path}/'s prefix", f"    location = {path} {{",
                  f"        proxy_pass http://{_upstream('web')};", "    }", ""]
    if "/remote/" not in taken | {p for _, p, _ in stay}:   # no remote service in the registry: the web app's
        lines += ["    # Remote's LAN apps: websockets.",
                  "    location /remote/ {",
                  f"        proxy_pass http://{_upstream('web')};",
                  *_websocket(accel_header),
                  "    }",
                  ""]
    lines += ["    # Everything else: the web app.",
              "    location / {",
              f"        proxy_pass http://{_upstream('web')};",
              "    }",
              "}",
              ""]
    return "\n".join(lines)


def _websocket(accel_header: str) -> list[str]:
    """A location's lines for websockets: the upgrade passed on (and, as it sets headers of its own, the server's
    passed-on headers again: nginx doesn't merge them)."""
    return [*("        " + h for h in PASS + [accel_header]),
            "        proxy_set_header Upgrade $http_upgrade;",
            "        proxy_set_header Connection $front_connection_upgrade;"]


def accel_roots(env_file: str | None) -> dict[str, Path]:
    """The folders the app's settings name (the same ones the app hands over), read from the service's .env."""
    from mnemerynne.config import Settings
    from mnemerynne.web import sendfile
    if env_file == "-":
        env_file = "/dev/stdin" if Path("/dev/stdin").exists() else None
    settings = Settings(_env_file=env_file) if env_file else Settings()
    return sendfile.roots(settings)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Print the nginx front-door config for the registered services")
    parser.add_argument("--listen", help="Where the reverse proxy sends to, e.g. 192.168.1.5:38200")
    parser.add_argument("--web", default="127.0.0.1:38210", help="The web app's own address behind nginx")
    parser.add_argument("--accel", action="store_true", help="nginx serves /static/ and the app's file bytes")
    parser.add_argument("--roots", action="store_true", help="Print the folders nginx sends files from, and stop")
    parser.add_argument("--env-file", help="The service's .env (MNEMERYNNE_DATA_DIR, SHARE_ROOT...); - for stdin")
    parser.add_argument("--services", help="Comma-separated: only these services get their paths (default: all)")
    parser.add_argument("--stamp", help=f"What nginx answers at {STAMP_PATH} (front-door.sh's check)")
    args = parser.parse_args(argv)
    if args.roots:
        for key, path in accel_roots(args.env_file).items():
            print(key, path)
        return
    if not args.listen:
        parser.error("--listen is required")
    accel = accel_roots(args.env_file) if args.accel else None
    running = None if args.services is None else [n for n in args.services.split(",") if n]
    sys.stdout.write(render(args.listen, args.web, accel, running, args.stamp))


if __name__ == "__main__":
    main()
