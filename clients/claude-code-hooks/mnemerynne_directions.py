#!/usr/bin/env python3
"""Claude Code hook: directions from Mnemerynne's Scratchpad page reach this session.

The person writes directions on the Scratchpad page (Mnemerynne → Scratchpad). The connector hands them to a
session with its next tool call, but a session that is waiting, or that doesn't use the connector, never asks.
This hook asks on its behalf:

  SessionStart      tells Claude its label and board, and the directions still open for it
  UserPromptSubmit  new directions, with each prompt
  PostToolUse       new directions while Claude works (at most every MNEMERYNNE_HOOK_INTERVAL seconds)
  Stop              keeps Claude going once if a direction for it is still open

Settings (environment variables, e.g. the "env" block of ~/.claude/settings.json):
  MNEMERYNNE_URL     Mnemerynne's address, e.g. https://beepsquad.wtf (required)
  MNEMERYNNE_TOKEN   a Claude token from Mnemerynne → Preferences → Claude (or MNEMERYNNE_TOKEN_FILE: a file holding one)
  MNEMERYNNE_AGENT   this session's label (default: cc-<project>-<4 letters of the session id>)
  MNEMERYNNE_BOARD   the board this session works on (default: the project folder's name)
  MNEMERYNNE_HOOK_INTERVAL  seconds between PostToolUse checks (default 60)

Standard library only. Anything going wrong (no network, a bad token) is silent: the session carries on.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 5


def _slug(text: str, limit: int) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.")
    return s[:limit] or "session"


def identity(event: dict) -> tuple[str, str]:
    """(agent label, board) for this session."""
    project = os.path.basename(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd())
    board = os.environ.get("MNEMERYNNE_BOARD") or _slug(project, 60)
    agent = os.environ.get("MNEMERYNNE_AGENT") or \
        f"cc-{_slug(project, 24)}-{(event.get('session_id') or 'xxxx')[:4]}"
    return agent[:40], board


def _token() -> str | None:
    token = os.environ.get("MNEMERYNNE_TOKEN")
    path = os.environ.get("MNEMERYNNE_TOKEN_FILE")
    if not token and path and os.path.isfile(os.path.expanduser(path)):
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            token = fh.read().strip()
    return token or None


def fetch(agent: str, board: str, event_name: str) -> dict | None:
    """GET /api/scratchpad/directions; None when it can't be asked (not set up, offline, refused)."""
    base, token = (os.environ.get("MNEMERYNNE_URL") or "").rstrip("/"), _token()
    if not base or not token:
        return None
    query = urllib.parse.urlencode({"agent": agent, "board": board, "event": event_name})
    request = urllib.request.Request(f"{base}/api/scratchpad/directions?{query}",
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                              "User-Agent": "mnemerynne-claude-code-hook/1"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def describe(directions: list[dict]) -> str:
    lines = []
    for d in directions:
        flags = " [URGENT]" if d.get("urgent") else ""
        target = "for you" if d.get("for") not in (None, "everyone on the board") else "for everyone on the board"
        lines.append(f"- note #{d['note_id']}{flags} on board {d['board']} ({target}): {d['text']}")
    return "\n".join(lines)


HOW = ("Act on them before going on (urgent ones first): claim_note it, answer on the board with "
       "write_scratchpad(reply_to=<note_id>), and resolve_note when it's done. If one asks you to stop, finish the "
       "step you're on, hand over on the board, and stop.")


def _due(agent: str) -> bool:
    """PostToolUse: only every MNEMERYNNE_HOOK_INTERVAL seconds per session."""
    interval = int(os.environ.get("MNEMERYNNE_HOOK_INTERVAL") or 60)
    stamp = os.path.join(tempfile.gettempdir(), f"mnemerynne-hook-{_slug(agent, 40)}.last")
    try:
        if time.time() - os.path.getmtime(stamp) < interval:
            return False
    except OSError:
        pass
    try:
        with open(stamp, "w"):
            pass
    except OSError:
        pass
    return True


def handle(event: dict) -> tuple[str, int]:
    """(what to print, exit code) for one hook event."""
    name = event.get("hook_event_name") or ""
    agent, board = identity(event)
    if name == "Stop" and event.get("stop_hook_active"):
        return "", 0                                   # already kept going once: let it stop
    if name == "PostToolUse" and not _due(agent):
        return "", 0
    answer = fetch(agent, board, name)
    if answer is None:
        return "", 0
    agent = answer.get("agent") or agent
    new, still_open = answer.get("new") or [], answer.get("open") or []

    if name == "SessionStart":
        text = (f"Mnemerynne scratchpad: this session is \"{agent}\" on board \"{board}\". Pass agent=\"{agent}\" to "
                f"the scratchpad tools (read_scratchpad, write_scratchpad, claim_note) so the person's directions "
                f"find you, and keep your hand-over notes on board \"{board}\".")
        if still_open:
            text += f"\n\nDirections from the person still open for you:\n{describe(still_open)}\n{HOW}"
        return text, 0
    if not new and name != "Stop":
        return "", 0
    if name == "UserPromptSubmit":
        return f"New directions from the person on the Mnemerynne scratchpad:\n{describe(new)}\n{HOW}", 0
    if name == "PostToolUse":
        return json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"New directions from the person on the Mnemerynne scratchpad:\n{describe(new)}\n{HOW}"}}), 0
    if name == "Stop" and still_open:
        return json.dumps({"decision": "block", "reason": (
            "Before stopping: these directions from the person are still open for you on the Mnemerynne scratchpad:\n"
            f"{describe(still_open)}\n{HOW} If you can't, say why on the board (write_scratchpad reply_to) and "
            "stop.")}), 0
    return "", 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    out, code = handle(event if isinstance(event, dict) else {})
    if out:
        print(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
