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
                  f"        allow {probe_from};", "        allow 127.0.0.1;", "        deny all;",
                  f"        alias {_quoted(str(root).rstrip('/') + '/' + PROBE)};",
                  "    }"]
    return lines + [""]


def render(listen: str, web: str = "127.0.0.1:38210", accel: dict[str, Path] | None = None,
           running: list[str] | None = None) -> str:
    """The whole nginx config: one upstream per service and the web app, one location per service path, and with
    `accel` (folder key -> path, web/sendfile.roots) nginx's own file locations. `running`: only these services get
    their paths (front-door.sh passes the ones whose units are enabled); the rest stay with the web app."""
    services = [s for s in registry.SERVICES.values() if running is None or s.name in running]
    lines = [HEADER + (ACCEL_HEADER if accel else "")]
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
    if accel:
        lines += _accel_locations(accel, listen.rsplit(":", 1)[0].strip("[]"))
    for s in services:
        for path in s.paths:
            lines += [f"    # {s.name}: {s.serves}",
                      f"    location {path} {{", f"        proxy_pass http://{_upstream(s.name)};", "    }", ""]
    lines += ["    # Remote's LAN apps: websockets.",
              "    location /remote/ {",
              f"        proxy_pass http://{_upstream('web')};",
              *("        " + h for h in PASS + [accel_header]),
              "        proxy_set_header Upgrade $http_upgrade;",
              "        proxy_set_header Connection $front_connection_upgrade;",
              "    }",
              "",
              "    # Everything else: the web app.",
              "    location / {",
              f"        proxy_pass http://{_upstream('web')};",
              "    }",
              "}",
              ""]
    return "\n".join(lines)


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
    args = parser.parse_args(argv)
    if args.roots:
        for key, path in accel_roots(args.env_file).items():
            print(key, path)
        return
    if not args.listen:
        parser.error("--listen is required")
    accel = accel_roots(args.env_file) if args.accel else None
    running = None if args.services is None else [n for n in args.services.split(",") if n]
    sys.stdout.write(render(args.listen, args.web, accel, running))


if __name__ == "__main__":
    main()
