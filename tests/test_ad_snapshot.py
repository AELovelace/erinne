import os
import subprocess
import time

from mnemerynne import directory
from mnemerynne.directory import WinbindDirectory, parse_snapshot

DOM = "S-1-5-21-1-2-3"

# As `net ads search -P` prints it.
SNAPSHOT = f"""Got 6 replies

distinguishedName: CN=Edith Whittaker,OU=Staff,DC=ad,DC=eternalhotels,DC=COM
sAMAccountName: edith
objectSid: {DOM}-1114
sAMAccountType: 805306368
primaryGroupID: 513
memberOf: CN=IT,OU=mnemerynne,OU=EternalHotels,DC=ad,DC=eternalhotels,DC=COM
memberOf: CN=Domain Admins,CN=Users,DC=ad,DC=eternalhotels,DC=com

distinguishedName: CN=Domain Users,CN=Users,DC=ad,DC=eternalhotels,DC=com
sAMAccountName: Domain Users
objectSid: {DOM}-513
sAMAccountType: 268435456
memberOf: CN=Users,CN=Builtin,DC=ad,DC=eternalhotels,DC=com

distinguishedName: CN=IT,OU=mnemerynne,OU=EternalHotels,DC=ad,DC=eternalhotels,DC=com
sAMAccountName: IT
objectSid: {DOM}-1148
sAMAccountType: 268435456
memberOf: CN=Staff Tools,OU=Groups,DC=ad,DC=eternalhotels,DC=com

distinguishedName: CN=Staff Tools,OU=Groups,DC=ad,DC=eternalhotels,DC=com
sAMAccountName: Staff Tools
objectSid: {DOM}-1200
sAMAccountType: 268435456
memberOf: CN=IT,OU=mnemerynne,OU=EternalHotels,DC=ad,DC=eternalhotels,DC=com

distinguishedName: CN=Domain Admins,CN=Users,DC=ad,DC=eternalhotels,DC=com
sAMAccountName: Domain Admins
objectSid: {DOM}-512
sAMAccountType: 268435456

distinguishedName: CN=Users,CN=Builtin,DC=ad,DC=eternalhotels,DC=com
sAMAccountName: Users
objectSid: S-1-5-32-545
sAMAccountType: 536870912

distinguishedName: CN=RLP,OU=mnemerynne,OU=EternalHotels,DC=ad,DC=eternalhotels,DC=com
sAMAccountName: RLP
objectSid: {DOM}-1150
sAMAccountType: 268435456
"""


def _directory(tmp_path, text=SNAPSHOT, age=0.0):
    path = tmp_path / "snapshot.txt"
    path.write_text(text, encoding="utf-8")
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return WinbindDirectory("ETERNALHOTELS", snapshot_path=path)


def test_memberships_include_primary_nested_and_cyclic_groups(tmp_path):
    d = _directory(tmp_path)
    # Direct (IT, Domain Admins), primary (Domain Users), nested (Users through
    # Domain Users; Staff Tools through IT, which loops back to IT).
    assert d.groups_of("edith") == {"it", "domain admins", "domain users", "users", "staff tools"}
    assert d.groups_of("ETERNALHOTELS\\Edith") == d.groups_of("edith")
    assert d.account_exists("edith")
    assert d.groups() == ["Domain Admins", "Domain Users", "IT", "RLP", "Staff Tools", "Users"]
    assert d.accounts() == ["edith"]


def test_parse_skips_noise_and_groups_are_not_accounts(tmp_path):
    snap = parse_snapshot("Got 0 replies\n\n" + SNAPSHOT + "\nsome: junk\n\n")
    assert snap.account("edith") is not None
    assert snap.account("IT") is None


def test_ids_come_from_idmap_for_every_sid(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        sids = cmd[1].split("=", 1)[1].split(",")
        out = "\n".join(f"{s} -> uid/gid {2000000 + int(s.rsplit('-', 1)[1])}" if s.startswith(DOM)
                        else f"{s} -> gid 10001" for s in sids)
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

    monkeypatch.setattr(directory.subprocess, "run", fake_run)
    d = _directory(tmp_path)
    uid, gids = d.ids_of("edith")
    assert uid == 2001114
    assert gids == {2001114, 2000513, 2000512, 2001148, 2001200, 10001}
    d.ids_of("edith")
    assert len(calls) == 1          # SID -> id mappings are kept


def test_stale_or_missing_snapshot_falls_back_to_winbind(tmp_path, monkeypatch):
    monkeypatch.setattr(WinbindDirectory, "_nss_groups_of", lambda self, a: {"from winbind"})
    stale = _directory(tmp_path, age=directory.SNAPSHOT_MAX_AGE + 60)
    assert stale.groups_of("edith") == {"from winbind"}
    missing = WinbindDirectory("ETERNALHOTELS", snapshot_path=tmp_path / "nope.txt")
    assert missing.groups_of("edith") == {"from winbind"}
    # An account the snapshot doesn't know also goes to winbind.
    assert _directory(tmp_path).groups_of("newhire") == {"from winbind"}


def test_new_snapshot_is_picked_up(tmp_path):
    d = _directory(tmp_path)
    assert "rlp" not in d.groups_of("edith")
    changed = SNAPSHOT.replace("memberOf: CN=Domain Admins,CN=Users",
                               "memberOf: CN=RLP,OU=mnemerynne,OU=EternalHotels,DC=ad,DC=eternalhotels,DC=com\n"
                               "memberOf: CN=Domain Admins,CN=Users")
    path = tmp_path / "snapshot.txt"
    path.write_text(changed, encoding="utf-8")
    t = time.time() + 5
    os.utime(path, (t, t))
    assert "rlp" in d.groups_of("edith")
