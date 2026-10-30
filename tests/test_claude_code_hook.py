"""The Claude Code hook (clients/claude-code-hooks): each hook event against the real server."""

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakes import FakeEmbedder
from mnemerynne import api_tokens, users
from mnemerynne.web.app import Services, create_app

HOOK = Path(__file__).resolve().parents[1] / "clients" / "claude-code-hooks" / "mnemerynne_directions.py"


@pytest.fixture
def hook(settings, conn, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("mnemerynne_directions", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    uid = users.create_user(conn, "alice", "password123")
    token = api_tokens.create(conn, uid, "laptop")[1]
    with TestClient(create_app(settings, Services(llm=None, embedder=FakeEmbedder()))) as client:
        def fetch(agent, board, event_name):         # the script's GET, through the test client
            r = client.get("/api/scratchpad/directions", params={"agent": agent, "board": board, "event": event_name},
                           headers={"Authorization": f"Bearer {token}"})
            return r.json() if r.status_code == 200 else None

        monkeypatch.setattr(module, "fetch", fetch)
        monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path))
        monkeypatch.delenv("MNEMERYNNE_AGENT", raising=False)
        monkeypatch.delenv("MNEMERYNNE_BOARD", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/home/doll/erinne")
        client.post("/login", data={"username": "alice", "password": "password123"})
        yield module, client


def _event(name, **extra):
    return {"hook_event_name": name, "session_id": "abcd1234", "cwd": "/home/doll/erinne", **extra}


def test_a_session_hears_about_directions_at_every_turn(hook):
    module, client = hook
    text, _ = module.handle(_event("SessionStart"))
    assert '"cc-erinne-abcd" on board "erinne"' in text and "still open" not in text
    assert module.handle(_event("UserPromptSubmit", prompt="hi"))[0] == ""         # nothing new

    note = client.post("/api/scratchpad/notes", json={"board": "erinne", "text": "Push what you have.",
                                                      "for_agent": "cc-erinne-abcd", "urgent": True}).json()["note_id"]
    text, _ = module.handle(_event("UserPromptSubmit", prompt="carry on"))
    assert f"note #{note} [URGENT] on board erinne (for you): Push what you have." in text
    assert module.handle(_event("UserPromptSubmit", prompt="again"))[0] == ""      # each once

    # While Claude works: a check at most every interval, as hook JSON.
    other = client.post("/api/scratchpad/notes", json={"board": "erinne", "text": "Freeze main."}).json()["note_id"]
    out, _ = module.handle(_event("PostToolUse", tool_name="Bash"))
    payload = json.loads(out)["hookSpecificOutput"]
    assert payload["hookEventName"] == "PostToolUse" and f"note #{other}" in payload["additionalContext"]
    client.post("/api/scratchpad/notes", json={"board": "erinne", "text": "Third."})
    assert module.handle(_event("PostToolUse", tool_name="Bash"))[0] == ""          # within the interval

    # Stopping with directions still open: kept going once, then let go.
    out, _ = module.handle(_event("Stop", stop_hook_active=False))
    blocked = json.loads(out)
    assert blocked["decision"] == "block" and f"note #{note}" in blocked["reason"]
    assert module.handle(_event("Stop", stop_hook_active=True))[0] == ""
    for n in (note, other):
        client.patch(f"/api/scratchpad/notes/{n}", json={"action": "resolve"})
    client.delete("/api/scratchpad", params={"board": "erinne"})
    assert module.handle(_event("Stop", stop_hook_active=False))[0] == ""


def test_labels_boards_and_silence_when_unreachable(hook, monkeypatch):
    module, _ = hook
    monkeypatch.setenv("MNEMERYNNE_AGENT", "doll-laptop")
    monkeypatch.setenv("MNEMERYNNE_BOARD", "nas-migration")
    assert module.identity(_event("SessionStart")) == ("doll-laptop", "nas-migration")
    monkeypatch.setattr(module, "fetch", lambda *a: None)                          # offline, or a bad token
    for name in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"):
        assert module.handle(_event(name)) == ("", 0)
    monkeypatch.delenv("MNEMERYNNE_URL", raising=False)
    import importlib
    fresh = importlib.util.module_from_spec(importlib.util.spec_from_file_location("h2", HOOK))
    importlib.util.spec_from_file_location("h2", HOOK).loader.exec_module(fresh)
    assert fresh.fetch("a", "b", "SessionStart") is None                           # not set up: no request at all
