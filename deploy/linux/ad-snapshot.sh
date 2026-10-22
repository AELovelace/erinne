#!/bin/sh
# Run by mnemerynne-adsync.timer every 5 minutes, as root: read every AD
# account's and group's memberships straight from a domain controller, with the
# machine account the domain join created, into /var/lib/mnemerynne-ad/snapshot.txt.
# Mnemerynne takes group memberships from there instead of winbind, whose copy
# only refreshes when someone signs in to this box.
#
# The directory is root's (the service user can only read), so nothing the
# service user does can redirect what root writes.
set -eu

DIR=/var/lib/mnemerynne-ad
FILTER='(|(&(objectCategory=person)(objectClass=user))(objectCategory=group))'

install -d -o root -g mnemerynne -m 750 "$DIR"   # the unit's StateDirectory makes it root:root
tmp=$(mktemp "$DIR/.snapshot.XXXXXX")
trap 'rm -f "$tmp"' EXIT

net ads search -P "$FILTER" distinguishedName sAMAccountName objectSid sAMAccountType primaryGroupID memberOf >"$tmp"
if ! grep -q '^objectSid: S-1-5-21-' "$tmp"; then
    echo "no accounts in the domain controller's answer; keeping the previous snapshot" >&2
    exit 1
fi

chgrp mnemerynne "$tmp"
chmod 640 "$tmp"
mv -f "$tmp" "$DIR/snapshot.txt"
trap - EXIT
echo "AD snapshot: $(grep -c '^sAMAccountName:' "$DIR/snapshot.txt") accounts and groups"
