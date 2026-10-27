"""Mail, calendar and live SharePoint search for Erynne's agent group, through
each person's own linked Microsoft 365 accounts (m365.py). Read-only, except
reply drafts: those become proposals (proposals.py, kind 'mail') that the
person saves to their Drafts folder with a click. Nothing here can send,
delete or move mail; there is no code path for it (and the tests fail if any
such call is made).

Tools are built per question (`context`, `specs`): an `account` choice when
the person linked accounts in several tenants, and a `mailbox` choice for
the shared mailboxes admins listed that they may use (Exchange still decides
whether they can open it). Message ids are long; the model gets short ones
(m1, m2, ...) for this question instead.

Claude through the connector gets the same mail and calendar tools (not the
live SharePoint search) only for people in the group an admin names on
Access → Connectors → Mail and calendar for Claude (meta 'claude_mail_group',
a group_refs reference; empty = off). The connector is stateless, so there a
Context is `opaque`: message ids are self-describing tokens (`encode_ref`)
instead of m1, m2, and every token is checked against the person's own linked
accounts and allowed mailboxes before it reaches Graph.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import httpx

from mnemerynne import group_refs, m365
from mnemerynne.config import Settings

TOOL_NAMES = {"search_mail", "read_mail", "recent_mail", "calendar", "search_sharepoint", "draft_reply", "draft_mail"}
CLAUDE_TOOL_NAMES = TOOL_NAMES - {"search_sharepoint"}   # the connector's share: SharePoint comes through libraries
CLAUDE_KEY = "claude_mail_group"
BODY_CHARS = 6000
FOLDERS = ("inbox", "sentitems", "drafts", "archive")
_LIST_SELECT = "id,subject,from,receivedDateTime,bodyPreview,webLink"


class MailError(m365.M365Error):
    """`code`: the connector's refusal code (invalid, not_found, not_allowed or unavailable)."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


@dataclass
class Account:
    identity_id: int
    label: str          # the tenant's name
    tenant_row: int
    upn: str


@dataclass
class Mailbox:
    label: str
    address: str
    tenant_row: int


@dataclass
class Context:
    """What one person may use in this question, and the short ids handed out.

    `opaque` (the connector): nothing is remembered between calls, so ids are
    tokens that carry the account, mailbox and message id (`encode_ref`)."""
    accounts: list[Account]
    mailboxes: list[Mailbox]
    refs: dict[str, tuple[int, str | None, str, dict]] = field(default_factory=dict)  # m1 -> (identity, mailbox, id, info)
    opaque: bool = False

    def ref(self, identity_id: int, mailbox: str | None, message_id: str, info: dict) -> str:
        if self.opaque:
            return encode_ref(identity_id, mailbox, message_id)
        for short, (i, mb, mid, _) in self.refs.items():
            if (i, mb, mid) == (identity_id, mailbox, message_id):
                return short
        short = f"m{len(self.refs) + 1}"
        self.refs[short] = (identity_id, mailbox, message_id, info)
        return short

    def lookup(self, ref: str) -> tuple[int, str | None, str, dict] | None:
        """What a message id stands for, or None. An opaque token only counts when it names one of
        this person's own accounts and, for a shared mailbox, one they may use: a token made for
        someone else, or made up, never reaches Graph."""
        if ref in self.refs:
            return self.refs[ref]
        if not self.opaque:
            return None
        decoded = decode_ref(ref)
        if decoded is None:
            return None
        identity_id, mailbox, message_id = decoded
        if identity_id not in {a.identity_id for a in self.accounts}:
            return None
        if mailbox and mailbox not in {m.address for m in self.mailboxes}:
            return None
        return identity_id, mailbox, message_id, {}


def encode_ref(identity_id: int, mailbox: str | None, message_id: str) -> str:
    raw = json.dumps([identity_id, mailbox or "", message_id], separators=(",", ":")).encode()
    return "mail_" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_ref(ref: str) -> tuple[int, str | None, str] | None:
    text = str(ref or "").strip()
    if not text.startswith("mail_"):
        return None
    try:
        raw = base64.urlsafe_b64decode(text[5:] + "=" * (-len(text[5:]) % 4))
        identity_id, mailbox, message_id = json.loads(raw)
        if not isinstance(identity_id, int) or not isinstance(message_id, str) or not message_id:
            return None
        return identity_id, (str(mailbox) or None), message_id
    except (ValueError, TypeError):
        return None


# ------------------------------------------------- the connector's group
def claude_group(conn: sqlite3.Connection) -> str:
    """The group whose members' Claude gets the mail and calendar tools (group_refs text); '' = off."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (CLAUDE_KEY,)).fetchone()
    return (row["value"] if row else "") or ""


def set_claude_group(conn: sqlite3.Connection, value: str) -> str:
    """Store a resolved group reference (group_refs.resolve), or '' to keep mail away from Claude."""
    value = (value or "").strip()
    if value:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (CLAUDE_KEY, value))
    else:
        conn.execute("DELETE FROM meta WHERE key = ?", (CLAUDE_KEY,))
    return value


def claude_enabled(conn: sqlite3.Connection) -> bool:
    return bool(claude_group(conn))


def claude_allowed(conn: sqlite3.Connection, user, directory) -> bool:
    """A group is set and this person is in it (through their linked domain or Microsoft account)."""
    value = claude_group(conn)
    return bool(value) and group_refs.in_any(conn, user, directory, value)


def context(conn: sqlite3.Connection, user, directory, *, opaque: bool = False) -> Context | None:
    """The person's usable linked accounts and allowed shared mailboxes (None: no accounts)."""
    rows = conn.execute("SELECT i.id, i.upn, i.tenant_row, t.label FROM m365_identities i JOIN m365_tenants t "
                        "ON t.id = i.tenant_row WHERE i.user_id = ? AND i.status = 'ok' AND t.enabled = 1 "
                        "ORDER BY t.label COLLATE NOCASE, i.id", (user["id"],)).fetchall()
    if not rows:
        return None
    accounts = [Account(r["id"], r["label"], r["tenant_row"], r["upn"]) for r in rows]
    tenants = {a.tenant_row for a in accounts}
    groups = group_refs.member_of(conn, user, directory)
    mailboxes = [Mailbox(r["label"], r["address"], r["tenant_row"]) for r in conn.execute(
        "SELECT * FROM m365_shared_mailboxes ORDER BY label COLLATE NOCASE").fetchall()
        if r["tenant_row"] in tenants and (not r["group_ref"] or group_refs.in_any(conn, user, directory,
                                                                                     r["group_ref"], groups))]
    return Context(accounts, mailboxes, opaque=opaque)


# ------------------------------------------------------------------- specs
def specs(ctx: Context) -> list[dict]:
    """Tool definitions for the model, with choices this person actually has."""
    extra: dict = {}
    if len(ctx.accounts) > 1:
        extra["account"] = {"type": "string", "enum": [a.label for a in ctx.accounts],
                            "description": "Which Microsoft 365 account (organisation)."}
    box = ({"mailbox": {"type": "string", "enum": [m.label for m in ctx.mailboxes],
                        "description": "A shared mailbox instead of the person's own."}} if ctx.mailboxes else {})
    date_ = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}

    def tool(name: str, description: str, props: dict, required: list[str]) -> dict:
        return {"type": "function", "function": {"name": name, "description": description, "parameters": {
            "type": "object", "properties": props, "required": required}}}

    return [
        tool("search_mail", "Search the person's email (or a shared mailbox) for messages about something. "
             "Returns short ids (m1, m2…) to use with read_mail and draft_reply.",
             {"query": {"type": "string"}, "from": {"type": "string", "description": "Sender name or address."},
              "since": {**date_, "description": "Only messages from this day on (YYYY-MM-DD)."},
              "count": {"type": "integer", "minimum": 1, "maximum": 20}, **extra, **box}, ["query"]),
        tool("recent_mail", "The newest messages in a folder of the person's email (default the inbox).",
             {"folder": {"type": "string", "enum": list(FOLDERS)}, "count": {"type": "integer", "minimum": 1,
                                                                             "maximum": 20}, **extra, **box}, []),
        tool("read_mail", "Read one message in full (from search_mail or recent_mail).",
             {"id": {"type": "string", "description": "Its short id, e.g. m2."}}, ["id"]),
        tool("calendar", "The person's calendar events between two days (inclusive).",
             {"start": date_, "end": date_, **extra}, ["start", "end"]),
        tool("search_sharepoint", "Search all of SharePoint the person can open, live (for sites that aren't "
             "in their documents). Returns titles and summaries with links for the person.",
             {"query": {"type": "string"}, **extra}, ["query"]),
        tool("draft_reply", "Draft a reply to a message (by its short id). It is only a proposal: the person "
             "reviews it and presses Save to Drafts. It is never sent.",
             {"id": {"type": "string"}, "body": {"type": "string"}, "reply_all": {"type": "boolean"}}, ["id", "body"]),
        tool("draft_mail", "Draft a new email. Only a proposal: the person saves it to Drafts; it is never sent.",
             {"to": {"type": "array", "items": {"type": "string"}}, "cc": {"type": "array", "items": {"type": "string"}},
              "subject": {"type": "string"}, "body": {"type": "string"}, **extra}, ["to", "subject", "body"]),
    ]


# --------------------------------------------------------------- running
def _account(ctx: Context, label: str | None) -> Account:
    if label is None:
        if len(ctx.accounts) == 1:
            return ctx.accounts[0]
        raise MailError("Say which account: " + ", ".join(a.label for a in ctx.accounts) + ".")
    for a in ctx.accounts:
        if a.label.lower() == str(label).lower():
            return a
    raise MailError(f"There's no linked account called {label}.")


def _mailbox(ctx: Context, account: Account, label: str | None) -> str | None:
    if not label:
        return None
    for m in ctx.mailboxes:
        if m.label.lower() == str(label).lower():
            if m.tenant_row != account.tenant_row:
                raise MailError(f"{m.label} belongs to another organisation; pick that account.")
            return m.address
    raise MailError(f"There's no shared mailbox called {label}.")


def _base(mailbox: str | None) -> str:
    return f"/users/{mailbox}" if mailbox else "/me"


def _day(text, name: str) -> date:
    try:
        return date.fromisoformat(str(text))
    except ValueError:
        raise MailError(f"Give {name} as YYYY-MM-DD.") from None


def _utc(day: date) -> str:
    """Local midnight of that day, in UTC, as Graph wants it."""
    local = datetime(day.year, day.month, day.day).astimezone()
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sender(msg: dict) -> str:
    who = (msg.get("from") or {}).get("emailAddress") or {}
    return who.get("name") or who.get("address") or "unknown sender"


def _when(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value[:16]


_QUOTE = [re.compile(p, re.I) for p in (r"^-{2,}\s*original message\s*-{2,}", r"^from:\s.+", r"^on .{5,300} wrote:$",
                                        r"^_{10,}$", r"^sent from my ")]


def clean_body(text: str, limit: int = BODY_CHARS) -> str:
    """A message's own words: quoted earlier messages, '>' lines and the
    signature (after '-- ') cut, blank runs squeezed, length capped."""
    out: list[str] = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if out and any(p.match(stripped) for p in _QUOTE):
            break
        if stripped == "--" or line == "-- ":
            break
        if stripped.startswith(">"):
            continue
        out.append(line.rstrip())
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return body if len(body) <= limit else body[:limit] + " … (cut short)"


def run(conn: sqlite3.Connection, settings: Settings, ctx: Context, name: str, args: dict,
        cite: Callable[..., str]) -> dict:
    """Run one tool. `cite(kind, key, title, location, url)` numbers a source and returns its [n]."""
    try:
        return _run(conn, settings, ctx, name, dict(args), cite)
    except m365.NeedsRelink as exc:
        raise MailError(str(exc), "not_allowed") from None
    except m365.GraphError as exc:
        if exc.status in (401, 403):
            raise MailError("Microsoft didn't allow that (no access to that mailbox or item).", "not_allowed") from None
        if exc.status == 404:
            raise MailError("Microsoft couldn't find that (it may have been moved or deleted).", "not_found") from None
        raise MailError(f"Microsoft answered: {exc}", "unavailable") from None
    except httpx.HTTPError as exc:
        raise MailError(f"Couldn't reach Microsoft: {exc}", "unavailable") from None


def _run(conn, settings, ctx: Context, name: str, args: dict, cite) -> dict:
    if name in ("read_mail", "draft_reply"):
        short = str(args.get("id", "")).strip()
        found = ctx.lookup(short)
        if found is None:
            raise MailError(f"There's no message {short or '(none given)'} " +
                            ("here; use the `id` search_mail or recent_mail returned." if ctx.opaque else
                             "in this conversation turn; use search_mail or recent_mail first and pass its short id."))
        identity_id, mailbox, message_id, info = found
        if name == "draft_reply":
            body = str(args.get("body", "")).strip()
            if not body:
                raise MailError("Give the text of the reply.")
            if "from_address" not in info:   # an opaque id carries no subject or sender: ask Graph
                msg = m365.graph_for(conn, settings, identity_id).get(f"{_base(mailbox)}/messages/{message_id}",
                                                                      {"$select": "subject,from"})
                info = {"subject": msg.get("subject"),
                        "from_address": ((msg.get("from") or {}).get("emailAddress") or {}).get("address", "")}
            return {"draft": {"identity_id": identity_id, "reply_to": message_id, "mailbox": mailbox,
                              "reply_all": bool(args.get("reply_all")), "to": [info.get("from_address", "")],
                              "subject": "RE: " + (info.get("subject") or ""), "body": body}}
        graph = m365.graph_for(conn, settings, identity_id)
        msg = graph.get(f"{_base(mailbox)}/messages/{message_id}",
                        {"$select": "subject,from,toRecipients,ccRecipients,receivedDateTime,body,webLink"},
                        headers={"Prefer": 'outlook.body-content-type="text"'})
        names = lambda key: [(r.get("emailAddress") or {}).get("name") or (r.get("emailAddress") or {}).get(  # noqa: E731
            "address", "") for r in msg.get(key) or []][:15]
        return {"id": short, "subject": msg.get("subject") or "(no subject)", "from": _sender(msg),
                "to": names("toRecipients"), "cc": names("ccRecipients"), "received": _when(msg.get("receivedDateTime")),
                "body": clean_body((msg.get("body") or {}).get("content", "")),
                "cite": cite("mail", f"{identity_id}:{message_id}", msg.get("subject") or "(no subject)",
                             f"{_sender(msg)} · {_when(msg.get('receivedDateTime'))}", msg.get("webLink"))}

    account = _account(ctx, args.get("account"))
    graph = m365.graph_for(conn, settings, account.identity_id)
    if name in ("search_mail", "recent_mail"):
        mailbox = _mailbox(ctx, account, args.get("mailbox"))
        count = max(1, min(int(args.get("count") or 10), 20))
        if name == "search_mail":
            terms = str(args.get("query", "")).replace('"', " ").strip()
            if args.get("from"):
                terms += f" from:{str(args['from']).replace(chr(34), ' ').strip()}"
            if not terms:
                raise MailError("Give some words to search for.")
            found = graph.get(f"{_base(mailbox)}/messages", {"$search": f'"{terms}"', "$top": str(count * 2),
                                                             "$select": _LIST_SELECT})
            since = _day(args["since"], "since") if args.get("since") else None
            items = [m for m in found.get("value", []) if not since or (m.get("receivedDateTime") or "")[:10]
                     >= since.isoformat()][:count]
        else:
            folder = args.get("folder") or "inbox"
            if folder not in FOLDERS:
                raise MailError("The folder is one of " + ", ".join(FOLDERS) + ".")
            items = graph.get(f"{_base(mailbox)}/mailFolders/{folder}/messages", {
                "$top": str(count), "$orderby": "receivedDateTime desc", "$select": _LIST_SELECT}).get("value", [])
        out = []
        for m in items:
            sender = ((m.get("from") or {}).get("emailAddress") or {})
            short = ctx.ref(account.identity_id, mailbox, m["id"], {"subject": m.get("subject"),
                                                                     "from_address": sender.get("address", "")})
            out.append({"id": short, "subject": m.get("subject") or "(no subject)", "from": _sender(m),
                        "received": _when(m.get("receivedDateTime")), "preview": (m.get("bodyPreview") or "")[:300],
                        "cite": cite("mail", f"{account.identity_id}:{m['id']}", m.get("subject") or "(no subject)",
                                     f"{_sender(m)} · {_when(m.get('receivedDateTime'))}", m.get("webLink"))})
        where = next((mb.label for mb in ctx.mailboxes if mb.address == mailbox), None) if mailbox else None
        return {"account": account.label, "mailbox": where or "own", "messages": out,
                **({} if out else {"note": "Nothing matched."})}

    if name == "calendar":
        start, end = _day(args.get("start"), "start"), _day(args.get("end"), "end")
        if end < start or (end - start).days > 62:
            raise MailError("Ask for at most two months, with the end after the start.")
        found = graph.get("/me/calendarView", {
            "startDateTime": _utc(start), "endDateTime": _utc(end + timedelta(days=1)), "$top": "50",
            "$orderby": "start/dateTime", "$select": "subject,start,end,location,organizer,attendees,isAllDay,webLink"},
            headers={"Prefer": 'outlook.timezone="UTC"'})
        events = []
        for e in found.get("value", []):
            begin = _when((e.get("start") or {}).get("dateTime", "") + "Z")
            events.append({
                "subject": e.get("subject") or "(no title)", "start": begin,
                "end": _when((e.get("end") or {}).get("dateTime", "") + "Z"), "all_day": bool(e.get("isAllDay")),
                "location": (e.get("location") or {}).get("displayName") or "",
                "organizer": ((e.get("organizer") or {}).get("emailAddress") or {}).get("name", ""),
                "attendees": [((a.get("emailAddress") or {}).get("name") or "") for a in e.get("attendees") or []][:10],
                "cite": cite("event", f"{account.identity_id}:{e.get('webLink')}:{begin}", e.get("subject") or "(no title)",
                             begin, e.get("webLink"))})
        return {"account": account.label, "events": events, **({} if events else {"note": "No events."})}

    if name == "search_sharepoint":
        query = str(args.get("query", "")).strip()
        if not query:
            raise MailError("Give some words to search for.")
        found = graph.post("/search/query", {"requests": [{"entityTypes": ["driveItem", "listItem"],
                                                           "query": {"queryString": query}, "from": 0, "size": 10}]})
        hits = [h for c in (found.get("value") or [{}])[0].get("hitsContainers", []) for h in c.get("hits", [])]
        results = []
        for h in hits:
            res = h.get("resource") or {}
            title = res.get("name") or (res.get("fields") or {}).get("title") or "SharePoint item"
            results.append({"title": title, "summary": re.sub(r"<[^>]+>", "", h.get("summary") or "")[:400],
                            "modified": _when(res.get("lastModifiedDateTime")),
                            "cite": cite("sharepoint", f"{account.identity_id}:{res.get('webUrl')}", title,
                                         "SharePoint", res.get("webUrl"))})
        return {"account": account.label, "results": results, **({} if results else {"note": "Nothing found."})}

    if name == "draft_mail":
        to = [str(a).strip() for a in args.get("to") or [] if str(a).strip()]
        if not to or not all("@" in a for a in to):
            raise MailError("Give the recipients' email addresses.")
        return {"draft": {"identity_id": account.identity_id, "reply_to": None, "mailbox": None, "reply_all": False,
                          "to": to, "cc": [str(a).strip() for a in args.get("cc") or [] if str(a).strip()],
                          "subject": str(args.get("subject", "")).strip() or "(no subject)",
                          "body": str(args.get("body", "")).strip()}}
    raise MailError(f"There's no tool called {name}.")


# ------------------------------------------------------------------ drafts
def save_draft(conn: sqlite3.Connection, settings: Settings, user_id: int, payload: dict) -> str:
    """Put a proposed draft into the person's own Drafts folder. Returns its
    Outlook link. Only ever creates a draft: nothing here sends."""
    owner = conn.execute("SELECT user_id, status FROM m365_identities WHERE id = ?",
                         (payload["identity_id"],)).fetchone()
    if owner is None or owner["user_id"] != user_id:
        raise MailError("That Microsoft account isn't linked to you any more.")
    graph = m365.graph_for(conn, settings, payload["identity_id"])
    try:
        if payload.get("reply_to") and not payload.get("mailbox"):
            action = "createReplyAll" if payload.get("reply_all") else "createReply"
            draft = graph.post(f"/me/messages/{payload['reply_to']}/{action}", {"comment": payload["body"]})
        else:
            # A new message (or a reply to a shared mailbox's message, drafted in the person's own mailbox).
            draft = graph.post("/me/messages", {
                "subject": payload.get("subject") or "", "body": {"contentType": "Text", "content": payload["body"]},
                "toRecipients": [{"emailAddress": {"address": a}} for a in payload.get("to") or [] if a],
                "ccRecipients": [{"emailAddress": {"address": a}} for a in payload.get("cc") or [] if a]})
    except m365.GraphError as exc:
        raise MailError(f"Microsoft didn't save the draft: {exc}") from None
    return draft.get("webLink") or "https://outlook.office.com/mail/drafts"


def draft_changes(payload: dict) -> list[str]:
    lines = [("Reply to " if payload.get("reply_to") else "To: ") + ", ".join(a for a in payload.get("to") or [] if a)]
    if payload.get("cc"):
        lines.append("Cc: " + ", ".join(payload["cc"]))
    lines.append("Subject: " + (payload.get("subject") or ""))
    return lines


def payload_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
