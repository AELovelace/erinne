"""The box's nginx front door, written from the service registry (docs/plans/services-rewrite.md, "nginx").

    python -m mnemerynne.services.nginx --listen 192.168.1.5:38200 [--web 127.0.0.1:38210]

prints the config deploy/linux/front-door.sh installs: nginx takes the address the reverse proxy sends to and
hands each service's paths to that service (registry.Service.paths and .port, all on 127.0.0.1), everything else
to the web app. A service added to the registry gets its route the next time front-door.sh writes the config.
"""

from __future__ import annotations

import argparse

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
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Connection "";              # keep the connections to the processes open between requests
"""


def _upstream(name: str) -> str:
    return "mnemerynne_" + name.replace("-", "_")


def render(listen: str, web: str = "127.0.0.1:38210") -> str:
    """The whole nginx config: one upstream per service and the web app, one location per service path."""
    services = list(registry.SERVICES.values())
    lines = [HEADER]
    lines.append(f"upstream {_upstream('web')} {{ server {web}; keepalive 32; }}")
    for s in services:
        lines.append(f"upstream {_upstream(s.name)} {{ server 127.0.0.1:{s.port}; keepalive {16 * s.processes()}; }}"
                     f"   # {s.name}: {s.processes()} process(es)")
    lines += ["", "server {", f"    listen {listen};", "    server_name _;", "", SERVER_SETTINGS]
    for s in services:
        for path in s.paths:
            lines += [f"    # {s.name}: {s.serves}",
                      f"    location {path} {{", f"        proxy_pass http://{_upstream(s.name)};", "    }", ""]
    lines += ["    # Remote's LAN apps: websockets.",
              "    location /remote/ {",
              f"        proxy_pass http://{_upstream('web')};",
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Print the nginx front-door config for the registered services")
    parser.add_argument("--listen", required=True, help="Where the reverse proxy sends to, e.g. 192.168.1.5:38200")
    parser.add_argument("--web", default="127.0.0.1:38210", help="The web app's own address behind nginx")
    args = parser.parse_args(argv)
    print(render(args.listen, args.web), end="")


if __name__ == "__main__":
    main()
