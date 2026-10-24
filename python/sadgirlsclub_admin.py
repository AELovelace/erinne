#!/usr/bin/python3 -I
"""One-request JSON bridge between Cockpit and the local Samba AD controller.

Install root-owned and invoke with Cockpit's superuser=require. No web listener,
shell, password arguments, domain provisioning, or share configuration lives here.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import unicodedata
from contextlib import contextmanager

SAMBA = "/usr/bin/samba-tool"
TESTPARM = "/usr/bin/testparm"
MAX_REQUEST = 16384
MUTATIONS = {"create_user", "create_group", "reset_password", "enable", "disable",
             "unlock", "delete_user", "delete_group", "add_member", "remove_member"}


class AdminError(Exception):
    """An actionable error safe to show in the browser."""


def clean_name(value: object) -> str:
    """Reject options, control characters, and Samba's list/name delimiters."""
    if not isinstance(value, str) or not value or len(value) > 256:
        raise AdminError("Choose a nonempty account or group name (maximum 256 characters).")
    if value != value.strip() or value.startswith("-") or any(
        unicodedata.category(c).startswith("C") or c in '\"/\\[]:;|=,+*?<>' for c in value
    ):
        raise AdminError("The name contains unsupported characters or surrounding spaces.")
    return value


def password_input(request: dict) -> tuple[str, str]:
    """Keep passwords in the input pipe, never in process arguments or audit messages."""
    secret = request.get("password")
    if not isinstance(secret, str) or not 1 <= len(secret) <= 256:
        raise AdminError("Enter a password of at most 256 characters. Domain policy also applies.")
    if any(unicodedata.category(c).startswith("C") for c in secret):
        raise AdminError("Passwords cannot contain control characters.")
    if secret != request.get("password_confirm"):
        raise AdminError("The two passwords do not match.")
    return secret, secret + "\n" + secret + "\n"


def run(args: list[str], *, input_text: str = "", secret: str = "") -> str:
    """Execute a fixed binary without a shell or controlling terminal; bound its runtime."""
    try:
        result = subprocess.run(
            args, input=input_text, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=40, start_new_session=True,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "HOME": "/root"},
        )
    except subprocess.TimeoutExpired:
        raise AdminError("Samba timed out. Refresh and check the account before retrying the change.") from None
    except OSError as exc:
        raise AdminError(f"Cannot run {os.path.basename(args[0])}: {exc.strerror}") from None
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "Samba rejected this operation."
        if secret:
            message = message.replace(secret, "[redacted]")
        raise AdminError(message[-3000:])
    return result.stdout


def samba(*args: str, **kwargs) -> str:
    """Route all AD operations through the local distro-provided samba-tool."""
    return run([SAMBA, *args], **kwargs)


def attributes(ldif: str) -> dict[str, list[str]]:
    """Decode folded and base64 LDIF values without treating directory text as markup."""
    unfolded: list[str] = []
    for line in ldif.splitlines():
        if line.startswith(" ") and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    result: dict[str, list[str]] = {}
    for line in unfolded:
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if value.startswith(":"):
            value = base64.b64decode(value[1:].strip(), validate=True).decode("utf-8")
        else:
            value = value.lstrip(" ")
        result.setdefault(key.lower(), []).append(value)
    return result


def first(attrs: dict, key: str, default: str = "") -> str:
    """Read a single directory attribute while tolerating optional missing values."""
    return attrs.get(key.lower(), [default])[0]


def protected(attrs: dict) -> bool:
    """Keep built-in and critical directory objects read-only in this simple UI."""
    sid = first(attrs, "objectSid")
    if first(attrs, "isCriticalSystemObject").lower() == "true":
        return True
    if sid.startswith("S-1-5-32-"):
        return True
    if not re.fullmatch(r"S-1-5-21-(?:\d+-){3}\d+", sid):
        return True  # Unknown identity types never become editable by accident.
    return int(sid.rsplit("-", 1)[1]) < 1000


def object_info(kind: str, name: str) -> dict:
    """Read the authoritative object and select only attributes needed by this UI."""
    attrs = attributes(samba(kind, "show", clean_name(name)))
    actual = first(attrs, "sAMAccountName")
    if not actual or not first(attrs, "objectSid"):
        raise AdminError("Samba did not return a complete directory object.")
    flags = int(first(attrs, "userAccountControl", "0"))
    return {"name": actual, "dn": first(attrs, "dn", first(attrs, "distinguishedName")),
            "sid": first(attrs, "objectSid"), "protected": protected(attrs),
            "disabled": bool(flags & 2), "display_name": first(attrs, "displayName"),
            "email": first(attrs, "mail"), "description": first(attrs, "description")}


def editable(kind: str, name: str) -> dict:
    """Resolve the current object and enforce built-in protections on the server."""
    info = object_info(kind, name)
    if info["protected"] or info["name"].endswith("$"):
        raise AdminError("Built-in, critical, and machine accounts are read-only in this interface.")
    return info


def lines(output: str) -> list[str]:
    """Return sorted names while preserving spaces inside group names."""
    return sorted((line for line in output.splitlines() if line), key=str.casefold)


def check_domain() -> dict:
    """Ensure the helper operates only on a local AD DC, not a member or standalone server."""
    role = run([TESTPARM, "-s", "--parameter-name=server role"]).strip()
    if role != "active directory domain controller":
        raise AdminError("This interface requires an already provisioned Samba AD domain controller.")
    return {"realm": run([TESTPARM, "-s", "--parameter-name=realm"]).strip(),
            "domain": run([TESTPARM, "-s", "--parameter-name=workgroup"]).strip()}


def group_catalog() -> list[dict]:
    """Read group identities in one local query, including renamed/localized built-ins."""
    import ldb
    from samba.auth import system_session
    from samba.dcerpc.security import dom_sid
    from samba.ndr import ndr_unpack
    from samba.param import LoadParm
    from samba.samdb import SamDB

    lp = LoadParm()
    lp.load_default()
    try:
        db = SamDB(session_info=system_session(), lp=lp)
        records = db.search(db.domain_dn(), scope=ldb.SCOPE_SUBTREE,
                            expression="(objectClass=group)",
                            attrs=["sAMAccountName", "objectSid", "isCriticalSystemObject"])
        groups = []
        for record in records:
            def text_attr(key: str) -> str:
                """Normalize LDB text attributes across distro binding versions."""
                value = record.get(key, idx=0) or ""
                return value.decode("utf-8") if isinstance(value, bytes) else str(value)

            name = text_attr("sAMAccountName")
            if not name:
                continue
            raw_sid = record.get("objectSid", idx=0)
            attrs = {"objectsid": [str(ndr_unpack(dom_sid, raw_sid)) if raw_sid else ""],
                     "iscriticalsystemobject": [text_attr("isCriticalSystemObject")]}
            groups.append({"name": name, "protected": protected(attrs)})
        return sorted(groups, key=lambda group: group["name"].casefold())
    except Exception as exc:
        raise AdminError("Cannot read group metadata from the local Samba directory. Check Samba and refresh.") from exc


def dispatch(request: dict) -> dict:
    """Map a small allow-list of browser operations to explicit Samba argument lists."""
    action = request.get("action")
    if action == "overview":
        domain = check_domain()
        groups = group_catalog()  # One read avoids spawning samba-tool for every group.
        return {**domain, "users": lines(samba("user", "list")),
                "groups": [group["name"] for group in groups],
                "builtin_groups": [group["name"] for group in groups if group["protected"]]}
    if action in {"user", "group"}:
        name = clean_name(request.get("name"))
        data = object_info(action, name)
        data["memberships" if action == "user" else "members"] = lines(
            samba("user", "getgroups", name) if action == "user"
            else samba("group", "listmembers", name))
        return data
    if action not in MUTATIONS:
        raise AdminError("Unknown operation.")
    name = clean_name(request.get("name"))
    if action == "create_user":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}", name):
            raise AdminError("Use 1–20 letters, numbers, dots, underscores, or hyphens for a new username.")
        secret, stdin = password_input(request)
        args = ["user", "add", name]
        for field, option in (("given_name", "--given-name"), ("surname", "--surname"), ("email", "--mail-address")):
            value = request.get(field, "")
            if not isinstance(value, str) or len(value) > 200 or any(unicodedata.category(c).startswith("C") for c in value):
                raise AdminError(f"Invalid {field.replace('_', ' ')}.")
            if value:
                args.append(option + "=" + value)
        if request.get("must_change") is True:
            args.append("--must-change-at-next-login")
        samba(*args, input_text=stdin, secret=secret)
    elif action == "create_group":
        samba("group", "add", name, "--group-type=Security", "--group-scope=Global")
    elif action in {"add_member", "remove_member"}:
        editable("group", name)
        member = clean_name(request.get("member"))
        editable("user", member)  # This simple menu manages user memberships, not nested groups.
        samba("group", "addmembers" if action == "add_member" else "removemembers", name, member)
    else:
        kind = "group" if action == "delete_group" else "user"
        editable(kind, name)
        if action.startswith("delete_"):
            if request.get("confirmation") != "DELETE " + name:
                raise AdminError("Type DELETE followed by the exact account or group name to confirm.")
            samba(kind, "delete", name)
        elif action == "reset_password":
            secret, stdin = password_input(request)
            args = ["user", "setpassword", name]
            if request.get("must_change") is True:
                args.append("--must-change-at-next-login")
            samba(*args, input_text=stdin, secret=secret)
        else:
            samba("user", action, name)
    return {"message": "Change saved.", "name": name}


@contextmanager
def mutation_lock():
    """Serialize this UI's writes and prevent duplicate clicks from racing each other."""
    import fcntl  # The installed helper runs on Linux; tests can import the module on Windows.
    fd = os.open("/run/sadgirlsclub-admin.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AdminError("Another directory change is running. Refresh and try again.") from None
        yield
    finally:
        os.close(fd)


def audit(request: dict, success: bool) -> None:
    """Write only action, target, and outcome to the journal; never serialize a request."""
    import syslog
    syslog.openlog("sadgirlsclub-admin")
    # JSON quoting prevents a malicious display name from injecting log lines.
    syslog.syslog(syslog.LOG_NOTICE, json.dumps({"action": request.get("action"),
                   "target": request.get("name"), "member": request.get("member"), "success": success}))


def main() -> int:
    """Accept one bounded JSON request and return one JSON response for Cockpit."""
    request: dict = {}
    try:
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise AdminError("Enable Administrative access in Cockpit to manage the domain.")
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise AdminError("Request is too large.")
        request = json.loads(raw)
        if not isinstance(request, dict) or not isinstance(request.get("action"), str):
            request = {}
            raise AdminError("Expected a JSON operation.")
        if request["action"] in MUTATIONS:
            with mutation_lock():
                check_domain()
                data = dispatch(request)
            audit(request, True)
        else:
            data = dispatch(request)
        response = {"ok": True, "data": data}
    except (AdminError, ValueError, UnicodeError) as exc:
        if request.get("action") in MUTATIONS:
            audit(request, False)
        response = {"ok": False, "error": str(exc)}
    except Exception:
        response = {"ok": False, "error": "Unexpected helper failure. Check Samba and the installed helper; no automatic retry was made."}
    print(json.dumps(response, ensure_ascii=True))
    return 0  # Application errors stay structured; Cockpit transport failures are separate.


if __name__ == "__main__":
    raise SystemExit(main())
