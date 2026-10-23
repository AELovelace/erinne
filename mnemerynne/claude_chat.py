"""Erynne on Claude: members of the Claude group (picked on Access → Settings,
meta 'claude_group') may have Claude answer a question in chat, through the
Anthropic API, with their own API key.

- Each person's key is encrypted like API credentials (apis.encrypt_secret),
  never sent back, and used only for their own questions, billed to them.
- Claude sees what it sees through /mcp: libraries shared with Claude, APIs
  with Claude's switch on, and no mail, calendar or SharePoint. Edits stay
  proposals the person applies (agent.py).
- Earlier answers in the conversation go along only if Claude wrote them, and
  memories never do: either could carry what isn't shared with Claude.
"""

from __future__ import annotations

import re
import sqlite3

from mnemerynne import apis, group_refs
from mnemerynne.config import Settings

CLAUDE_GROUP_KEY = "claude_group"
MODELS = {"claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
          "claude-haiku-5-5": "Claude Haiku 5.5"}
DEFAULT_MODEL = "claude-opus-5-5"
_KEY_SHAPE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{10,300}$")


def claude_group(conn: sqlite3.Connection) -> str:
    """The group(s) whose members may ask Claude in chat; '' = nobody."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (CLAUDE_GROUP_KEY,)).fetchone()
    return row["value"] if row else ""


def set_claude_group(conn: sqlite3.Connection, name: str) -> str:
    name = group_refs.join(group_refs.split(name))
    if len(name) > 2000:
        raise ValueError("Pick a group.")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (CLAUDE_GROUP_KEY, name))
    return name


def is_claude_user(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> bool:
    return group_refs.in_any(conn, user, directory, claude_group(conn))


def check_shape(key: str) -> str:
    """The key, trimmed, if it looks like an Anthropic API key."""
    key = (key or "").strip()
    if not _KEY_SHAPE.match(key):
        raise ValueError("That doesn't look like an Anthropic API key (they start with sk-ant-).")
    return key


def check_model(model: str | None) -> str:
    model = model or DEFAULT_MODEL
    if model not in MODELS:
        raise ValueError(f"Pick one of: {', '.join(MODELS.values())}.")
    return model


def status(conn: sqlite3.Connection, settings: Settings, user_id: int) -> dict:
    """Whether the person has a usable key (never the key itself) and their model."""
    row = conn.execute("SELECT key_enc, model, updated_at FROM user_claude WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return {"key": "missing", "model": DEFAULT_MODEL, "updated_at": None}
    usable = apis.decrypt_secret(settings, row["key_enc"]) is not None
    return {"key": "set" if usable else "needs re-entering", "model": row["model"], "updated_at": row["updated_at"]}


def save_key(conn: sqlite3.Connection, settings: Settings, user_id: int, key: str, model: str) -> None:
    blob = apis.encrypt_secret(settings, key)
    conn.execute("INSERT INTO user_claude(user_id, key_enc, model) VALUES (?, ?, ?) ON CONFLICT(user_id) DO "
                 "UPDATE SET key_enc = excluded.key_enc, model = excluded.model, updated_at = datetime('now')",
                 (user_id, blob, model))


def save_model(conn: sqlite3.Connection, user_id: int, model: str) -> bool:
    """Change the model of a saved key; False when there's no key yet."""
    return conn.execute("UPDATE user_claude SET model = ?, updated_at = datetime('now') WHERE user_id = ?",
                        (model, user_id)).rowcount > 0


def remove_key(conn: sqlite3.Connection, user_id: int) -> bool:
    return conn.execute("DELETE FROM user_claude WHERE user_id = ?", (user_id,)).rowcount > 0


def credentials(conn: sqlite3.Connection, settings: Settings, user_id: int) -> tuple[str, str] | None:
    """(key, model) for the person's own questions, or None."""
    row = conn.execute("SELECT key_enc, model FROM user_claude WHERE user_id = ?", (user_id,)).fetchone()
    key = apis.decrypt_secret(settings, row["key_enc"]) if row else None
    return (key, row["model"] if row["model"] in MODELS else DEFAULT_MODEL) if key else None
