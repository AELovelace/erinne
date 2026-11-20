#!/usr/bin/env bash
# The public landing page (deploy/landing/) on its own hostname, served by nginx as a static folder only
# (README "The landing page"):
#   sudo bash /opt/mnemerynne/deploy/linux/landing-install.sh mnemerynne.io     install or update it
#
# What it does, in order:
#   1. copies deploy/landing/* and the app's city art (mnemerynne/web/static/parallax) to /var/www/mnemerynne-landing
#   2. takes the hostname out of the edge file's server_name lines (it belongs to the landing page now)
#   3. gets a Let's Encrypt certificate for it, if there isn't one (port 80 must reach this box)
#   4. writes /etc/nginx/conf.d/mnemerynne-landing.conf from nginx-mnemerynne-landing.conf and reloads nginx
# If nginx -t fails at any step, the files it changed are put back and nginx is left as it was.
# Running it again after `git pull` just refreshes the files (step 1) and the config (step 4).
set -euo pipefail

HOST=${1:-}
APP=/opt/mnemerynne
WEBROOT=/var/www/mnemerynne-landing                  # where the page is served from
ACME=/var/www/letsencrypt                            # certbot's webroot, shared with the edge
CONF=/etc/nginx/conf.d/mnemerynne-landing.conf
EDGE=/etc/nginx/conf.d/mnemerynne-edge.conf
STAMP=$(date +%Y%m%d-%H%M%S)

say() { printf '\n== %s\n' "$*"; }                   # a step heading
die() { printf 'landing-install: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run it with sudo"
[[ $HOST =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "usage: landing-install.sh HOSTNAME   (e.g. mnemerynne.io)"
[[ -d $APP/deploy/landing ]] || die "$APP/deploy/landing is missing: update the app first (git pull)"
HOST_RE=${HOST//./\\.}                               # the hostname as a sed pattern (dots literal)

# Put back what we changed and stop: used whenever nginx -t says no.
restore() {
    say "nginx -t failed: putting the config back"
    [[ -f $EDGE.bak-$STAMP ]] && cp -a "$EDGE.bak-$STAMP" "$EDGE"
    if [[ -f $CONF.bak-$STAMP ]]; then cp -a "$CONF.bak-$STAMP" "$CONF"; else rm -f "$CONF"; fi
    nginx -t && systemctl reload nginx
    die "nothing changed in nginx; see the error above"
}

# 1. The files ---------------------------------------------------------------------------------------------
say "Copying the page to $WEBROOT"
install -d -m 755 "$WEBROOT" "$WEBROOT/parallax"
cp -a "$APP"/deploy/landing/. "$WEBROOT"/                                    # index.html, landing.css/js, favicon
cp -a "$APP"/mnemerynne/web/static/parallax/. "$WEBROOT"/parallax/            # the city art the app uses
chown -R root:root "$WEBROOT" && chmod -R u=rwX,go=rX "$WEBROOT"              # nginx reads, nobody else writes
command -v restorecon >/dev/null && restorecon -R "$WEBROOT"                  # SELinux: web content label

# 2. The hostname leaves the edge --------------------------------------------------------------------------
if [[ -f $EDGE ]] && grep -Eq "server_name[^;]*[[:space:]]$HOST_RE([[:space:]]|;)" "$EDGE"; then
    say "Taking $HOST out of $EDGE (backup: $EDGE.bak-$STAMP)"
    cp -a "$EDGE" "$EDGE.bak-$STAMP"
    sed -Ei "/server_name/ s/[[:space:]]$HOST_RE([[:space:];])/\1/" "$EDGE"    # drop just that name from the list
    if grep -Eq 'server_name[[:space:]]*;' "$EDGE"; then                       # it was the edge's only name
        cp -a "$EDGE.bak-$STAMP" "$EDGE"
        die "$HOST is the app's only hostname in $EDGE: give the app its new name first (README)"
    fi
fi
[[ -f $CONF ]] && cp -a "$CONF" "$CONF.bak-$STAMP"

# 3. The certificate ---------------------------------------------------------------------------------------
if [[ ! -f /etc/letsencrypt/live/$HOST/fullchain.pem ]]; then
    say "No certificate for $HOST yet: answering Let's Encrypt's check on port 80 first"
    install -d -m 755 "$ACME"; command -v restorecon >/dev/null && restorecon -R "$ACME"
    # Only the port-80 half for now: the HTTPS half can't load without the certificate.
    sed "s/landing\.example\.org/$HOST/g" "$APP/deploy/linux/nginx-mnemerynne-landing.conf" \
        | awk '/^server \{/{n++} n<2' > "$CONF"
    nginx -t || restore
    systemctl reload nginx
    certbot certonly --webroot -w "$ACME" -d "$HOST" --agree-tos --register-unsafely-without-email --non-interactive \
        || { restore; }
fi

# 4. The real config ---------------------------------------------------------------------------------------
say "Writing $CONF"
sed "s/landing\.example\.org/$HOST/g" "$APP/deploy/linux/nginx-mnemerynne-landing.conf" > "$CONF"
nginx -t || restore
systemctl reload nginx

say "Done: https://$HOST/ is the landing page (and https://$HOST/login is a 404)"
curl -s -o /dev/null -w "  / -> %{http_code}\n" --resolve "$HOST:443:127.0.0.1" "https://$HOST/" || true
curl -s -o /dev/null -w "  /login -> %{http_code}\n" --resolve "$HOST:443:127.0.0.1" "https://$HOST/login" || true
