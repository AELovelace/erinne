# Accounts, domain accounts and admins

## Accounts

People sign in with a Mnemerynne username and password. Accounts are made on **Access → Accounts → New user** (or `mnemerynne-admin create-user NAME [--role admin]`). A new user gets their own private File box straight away, with an Agents folder in it. The dialog suggests a password to hand over; they change it on Preferences.

Click a name to open the account:

- **Password**: reset it. This signs them out of every browser; Claude tokens keep working. `mnemerynne-admin reset-password NAME` does the same.
- **Access**: the role (`user` or local `admin`), and **Can sign in**. Turning that off ends every session at once and stops Claude connections; the account and its files stay.
- **Domain account**: link or unlink (below).
- **Groups**: their Mnemerynne groups.
- **Their files and Claude**: the Claude switches for their own File box (whether Claude may search it and edit in it), and **Revoke all** (shown while they have Claude tokens), which ends every token and OAuth connection they have: Claude's, ChatGPT's, and their Mnemerynne Backup apps' sign-ins too. Single OAuth connections have their own **Disconnect**.
- **Microsoft 365**: linked accounts, which you can unlink.

You can't deactivate yourself, and can't drop your own admin role unless the domain admin group keeps you an admin. Deleting a user, with their File box and memories, is a CLI step: `mnemerynne-admin delete-user NAME`. `forget-memories NAME` wipes only what Erynne remembers about them; admins can wipe memories but never read them.

## Roles

| Role | Means |
|---|---|
| `user` | Everything on the user side. |
| `admin` (local) | The Access page and the admin API, whatever the domain says. Keep at least one as a fallback for when AD is unreachable. |

Admin rights also come from:

- **The admin domain group** (Access → Settings → Administrators, default `Domain Admins`): members of that AD or Entra group, through their linked account.
- **Bootstrap usernames** in `MNEMERYNNE_ADMIN_USERS`.

And they only count from **admin networks** when `MNEMERYNNE_ADMIN_NETWORKS` is set: an admin on a phone off the VPN is an ordinary user. Preferences shows each person which of these makes them an admin.

## Domain accounts

With `MNEMERYNNE_AD_DOMAIN` set and the box joined to the domain (winbind: `wbinfo -u` lists accounts, `id 'DOMAIN\someone'` resolves), each user can be **linked** to their domain account on Access → Accounts. Linking checks the account exists; unlinking keeps the user and their files.

The link is what gives a person:

- access to libraries on the [SMB share](share.md) (their Windows permissions);
- admin rights through the admin group;
- Erynne's tools (the agent group), Claude in chat (the Claude group), mail and calendar for Claude (its group), each internal API (its group), shared mailboxes;
- the right to link only the matching Microsoft 365 account, where a tenant is set to "synced from our AD".

Unlinked people see their File box and the libraries granted to them in Mnemerynne, nothing from the share.

## Group memberships: the AD snapshot

Winbind can't be trusted for memberships (it refreshes a person's groups only when they sign in to the box, and caches "doesn't exist" for new groups). So the `mnemerynne-adsync` timer runs `deploy/linux/ad-snapshot.sh` as root every 5 minutes: it reads every account's and group's memberships from a domain controller with `net ads search` (the machine account; no extra credentials) into `/var/lib/mnemerynne-ad/snapshot.txt`. Mnemerynne works out nested groups from it. Adding or removing someone in AD takes effect within about 5 minutes, without restarts, for sessions already signed in and for Claude.

```sh
sudo cp /opt/mnemerynne/deploy/linux/mnemerynne-adsync.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mnemerynne-adsync.timer
journalctl -u mnemerynne-adsync -n 5 --no-pager     # "AD snapshot: N accounts and groups"
```

If the snapshot is missing or over 30 minutes old, Mnemerynne falls back to winbind and logs a warning. Never "fix" stale groups with `net cache flush` on a schedule: right after a flush winbind reports only each person's primary group, which briefly locks people out of everything else.

## Planned: domain sign-in and Microsoft sign-in

Two future projects, not started: signing in with the AD password (through winbind) instead of a separate Mnemerynne password, and single sign-on through Entra ID. Group memberships would keep coming from the snapshot either way.
