"""Admin approvals: a request only an admin can decide waits in Access → Approvals; the person can only
withdraw it, an admin approves or rejects it with a note, and the person's Claude sees the outcome
(get_proposal, list_proposals)."""

from mnemerynne import users
from test_mcp_consistency import _caller, _web, tools, world  # noqa: F401 (fixtures)


def _boss(conn):
    boss = users.create_user(conn, "boss", "password123")
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (boss,))
    return boss


def _claude_edit(conn, library):
    return conn.execute("SELECT claude_edit FROM collections WHERE id = ?", (library,)).fetchone()[0]


def test_a_library_request_waits_for_an_admin_who_approves_it(conn, settings, world, tools):
    _boss(conn)
    alice = _caller(conn, world["alice"])
    out = tools.request_edit_access(conn, alice, library="Brand")[0]
    assert out["status"] == "awaiting_admin" and "approve" not in out
    assert "Asked for by alice" in out["changes"][0]

    with _web(settings, "alice") as web:
        assert web.post(f"/api/agent/proposals/{out['proposal']}/apply").status_code == 400
        assert web.get("/api/admin/proposals").status_code == 403
        mine = web.get("/api/agent/proposals").json()["proposals"]
        assert mine[0]["approver"] == "admin" and mine[0]["status"] == "pending"
    with _web(settings, "boss") as web:
        assert "Approvals" in web.get("/admin/libraries").text
        queue = web.get("/api/admin/proposals").json()["requests"]
        assert [(r["id"], r["requester"], r["kind"]) for r in queue] == [(out["proposal"], "alice", "access")]
        done = web.post(f"/api/admin/proposals/{out['proposal']}/approve", json={"note": "Go ahead."}).json()
        assert done["status"] == "applied" and done["decided_by"] == "boss" and done["decision_note"] == "Go ahead."
    assert _claude_edit(conn, world["brand"]) == 1
    actions = [r[0] for r in conn.execute("SELECT action FROM access_log ORDER BY id DESC LIMIT 2")]
    assert actions == ["proposal.approve", "claude.edit.on"]

    seen = tools.get_proposal(conn, alice, out["proposal"])[0]
    assert (seen["status"], seen["decided_by"], seen["decision_note"]) == ("applied", "boss", "Go ahead.")
    assert seen["note"].startswith("Done") and "approve" not in seen
    assert [p["proposal"] for p in tools.list_proposals(conn, alice, status="decided")[0]["proposals"]] \
        == [out["proposal"]]
    assert tools.list_proposals(conn, alice, status="pending")[0]["proposals"] == []


def test_an_admin_rejects_with_a_note(conn, settings, world, tools):
    _boss(conn)
    alice = _caller(conn, world["alice"])
    out = tools.request_edit_access(conn, alice, library="Brand")[0]
    with _web(settings, "boss") as web:
        done = web.post(f"/api/admin/proposals/{out['proposal']}/reject", json={"note": "Brand is locked down."})
        assert done.json()["status"] == "rejected"
        assert web.post(f"/api/admin/proposals/{out['proposal']}/approve", json={}).status_code == 400
    assert _claude_edit(conn, world["brand"]) == 0
    seen = tools.get_proposal(conn, alice, out["proposal"])[0]
    assert seen["status"] == "rejected" and seen["decision_note"] == "Brand is locked down."
    assert "turned it down" in seen["note"]
    # A fresh request after a rejection is a new one, not the decided one again.
    assert tools.request_edit_access(conn, alice, library="Brand")[0]["proposal"] != out["proposal"]


def test_the_requester_can_withdraw_and_admins_only_see_admin_requests(conn, settings, world, tools):
    _boss(conn)
    alice = _caller(conn, world["alice"])
    out = tools.request_edit_access(conn, alice, library="Brand")[0]
    with _web(settings, "alice") as web:
        assert web.post(f"/api/agent/proposals/{out['proposal']}/dismiss").json()["status"] == "dismissed"
    with _web(settings, "boss") as web:
        assert web.post(f"/api/admin/proposals/{out['proposal']}/approve", json={}).status_code == 400
        assert web.get("/api/admin/proposals").json()["requests"][0]["status"] == "dismissed"
    assert _claude_edit(conn, world["brand"]) == 0


def test_proposal_tools_only_show_your_own(conn, settings, world, tools):
    alice, rob = _caller(conn, world["alice"]), _caller(conn, world["rob"])
    out = tools.request_edit_access(conn, alice, library="Brand")[0]
    assert tools.list_proposals(conn, rob)[0] == {"proposals": [], "note": "No proposals in that period."}
    try:
        tools.get_proposal(conn, rob, out["proposal"])
        raise AssertionError("rob saw alice's proposal")
    except Exception as exc:                                            # Refused
        assert "[not_found]" in str(exc) or "No proposal" in str(exc)
    try:
        tools.list_proposals(conn, alice, status="everything")
        raise AssertionError("bad status accepted")
    except Exception as exc:
        assert "pending, decided or all" in str(exc)
