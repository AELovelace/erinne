from mnemerynne import permissions, users


def _login(client, name):
    client.post("/login", data={"username": name, "password": "password123"})


def test_library_admin(client, conn):
    boss = users.create_user(conn, "boss", "password123", role="admin")
    alice = users.create_user(conn, "alice", "password123")
    _login(client, "alice")
    assert client.get("/api/admin/libraries").status_code == 403
    assert client.get("/admin/libraries").status_code == 403
    client.post("/logout")

    _login(client, "boss")
    page = client.get("/admin/libraries").text
    assert "Who can see what" in page
    # The Settings tab is one tab panel holding two cards: this box's settings, then connectors.
    assert page.count('data-panel="settings"') == 1 and 'class="ax-settings" data-panel="settings"' in page
    local, connectors = page.index('id="ax-local-title"'), page.index('id="ax-connectors-title"')
    assert local < page.index('id="admin-group"') < page.index('id="agent-settings"') \
        < page.index('id="balancer-settings"') < page.index('id="updates"') < connectors
    assert connectors < page.index('id="m365-settings"') < page.index('id="claude-settings"') \
        < page.index('id="claude-mail-settings"') < page.index('id="claude-links-settings"')
    for block in ("admin-group", "agent-settings", "balancer-settings", "updates", "m365-settings",
                  "claude-settings", "claude-mail-settings", "claude-links-settings", "domain-groups"):
        assert page.count(f'id="{block}"') == 1, block
    lib = client.post("/api/admin/libraries", json={"name": "Brand Standards"}).json()["id"]
    assert client.post("/api/admin/libraries", json={"name": "brand standards"}).status_code == 409
    assert lib in permissions.writable_collection_ids(conn, boss)  # the creator can fill it

    grants = client.put(f"/api/admin/libraries/{lib}/grants", json={"type": "user", "id": alice, "access": "read"})
    assert {"type": "user", "id": alice, "name": "alice", "access": "read", "claude": True} in grants.json()["grants"]
    assert lib in permissions.readable_collection_ids(conn, alice)
    assert lib not in permissions.writable_collection_ids(conn, alice)
    client.put(f"/api/admin/libraries/{lib}/grants", json={"type": "user", "id": alice, "access": "write"})
    assert lib in permissions.writable_collection_ids(conn, alice)
    client.put(f"/api/admin/libraries/{lib}/grants", json={"type": "user", "id": alice, "access": "none"})
    assert lib not in permissions.readable_collection_ids(conn, alice)

    group = permissions.create_group(conn, "Front desk", [alice])
    client.put(f"/api/admin/libraries/{lib}/grants", json={"type": "group", "id": group, "access": "read"})
    assert lib in permissions.readable_collection_ids(conn, alice)
    client.put(f"/api/admin/libraries/{lib}/grants", json={"type": "group", "id": group, "access": "none"})
    assert lib not in permissions.readable_collection_ids(conn, alice)

    assert client.patch(f"/api/admin/libraries/{lib}", json={"name": "Brand"}).json()["name"] == "Brand"
    listed = client.get("/api/admin/libraries").json()["libraries"]
    assert [(lib_["name"], lib_["files"]) for lib_ in listed] == [("Brand", 0)]

    upload = client.post("/api/files", files={"file": ("a.md", b"# A\n\ntext\n", "text/markdown")},
                         data={"collection_id": str(lib)})
    assert upload.status_code == 201
    gone = client.delete(f"/api/admin/libraries/{lib}").json()
    assert gone["files"] == 1
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM blobs").fetchone()[0] == 0
    # Private inboxes can't be managed (or deleted) as libraries.
    inbox = permissions.private_collection_id(conn, alice)
    assert client.delete(f"/api/admin/libraries/{inbox}").status_code == 404


def test_claude_sharing(client, conn):
    users.create_user(conn, "boss", "password123", role="admin")
    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    _login(client, "boss")
    lib = client.post("/api/admin/libraries", json={"name": "Manuals"}).json()["id"]
    inbox = permissions.private_collection_id(conn, alice)

    # Everything is shared with Claude by default: the inbox and granted libraries.
    assert permissions.claude_collection_ids(conn, alice) == {inbox}
    client.put(f"/api/admin/libraries/{lib}/grants", json={"type": "user", "id": alice, "access": "read"})
    assert permissions.claude_collection_ids(conn, alice) == {inbox, lib}

    # Grant-level switch; a later grant change without `claude` keeps it.
    client.put(f"/api/admin/libraries/{lib}/grants",
               json={"type": "user", "id": alice, "access": "read", "claude": False})
    client.put(f"/api/admin/libraries/{lib}/grants", json={"type": "user", "id": alice, "access": "write"})
    assert lib not in permissions.claude_collection_ids(conn, alice)
    assert lib in permissions.readable_collection_ids(conn, alice)  # web access is untouched

    # A group grant that allows Claude is enough on its own.
    group = client.post("/api/admin/groups", json={"name": "Front desk", "members": [alice]}).json()["id"]
    client.put(f"/api/admin/libraries/{lib}/grants", json={"type": "group", "id": group, "access": "read"})
    assert lib in permissions.claude_collection_ids(conn, alice)

    # The library switch overrides every grant.
    assert client.put(f"/api/admin/libraries/{lib}/claude", json={"enabled": False}).status_code == 200
    assert lib not in permissions.claude_collection_ids(conn, alice)
    listed = client.get("/api/admin/libraries").json()["libraries"][0]
    assert listed["claude_enabled"] is False and listed["claude_changed_by"] == "boss"

    # Inbox switch.
    client.put(f"/api/admin/users/{alice}/inbox-claude", json={"enabled": False})
    assert permissions.claude_collection_ids(conn, alice) == set()
    assert client.put("/api/admin/users/999/inbox-claude", json={"enabled": False}).status_code == 404

    # Claude access never exceeds read access.
    assert permissions.claude_collection_ids(conn, bob) <= permissions.readable_collection_ids(conn, bob)

    # Overview: alice sees her inbox and the library, via both grants.
    overview = {u["name"]: u["libraries"] for u in client.get("/api/admin/access").json()["users"]}
    alice_libs = {c["name"]: c for c in overview["alice"]}
    assert alice_libs["Manuals"]["read"] == "everything" and alice_libs["Manuals"]["edit"] == "everything"
    assert alice_libs["Manuals"]["via"] == ["direct", "Front desk"]
    assert alice_libs["Manuals"]["claude"] == "nothing"
    assert [c["kind"] for c in overview["alice"]] == ["private", "library"]

    # Every change is logged with who made it.
    entries = client.get("/api/admin/log").json()["entries"]
    assert {e["actor_name"] for e in entries} == {"boss"}
    assert ("claude.disable", "Manuals") in {(e["action"], e["target"]) for e in entries}
    assert ("claude.disable", "inbox of alice") in {(e["action"], e["target"]) for e in entries}

    client.post("/logout")
    _login(client, "alice")
    for url in ("/api/admin/access", "/api/admin/log", "/api/admin/groups"):
        assert client.get(url).status_code == 403
    assert client.put(f"/api/admin/libraries/{lib}/claude", json={"enabled": True}).status_code == 403


def test_groups(client, conn):
    users.create_user(conn, "boss", "password123", role="admin")
    alice = users.create_user(conn, "alice", "password123")
    bob = users.create_user(conn, "bob", "password123")
    _login(client, "boss")
    lib = client.post("/api/admin/libraries", json={"name": "Brand"}).json()["id"]

    group = client.post("/api/admin/groups", json={"name": "Sales", "members": [alice]}).json()["id"]
    assert client.post("/api/admin/groups", json={"name": "sales"}).status_code == 409
    assert client.post("/api/admin/groups", json={"name": "X", "members": [999]}).status_code == 404
    client.put(f"/api/admin/libraries/{lib}/grants", json={"type": "group", "id": group, "access": "read"})
    assert lib in permissions.readable_collection_ids(conn, alice)
    assert lib not in permissions.readable_collection_ids(conn, bob)

    client.put(f"/api/admin/groups/{group}/members", json={"members": [bob]})
    assert lib not in permissions.readable_collection_ids(conn, alice)
    assert lib in permissions.readable_collection_ids(conn, bob)

    assert client.patch(f"/api/admin/groups/{group}", json={"name": "Sales team"}).json()["name"] == "Sales team"
    listed = client.get("/api/admin/groups").json()["groups"]
    assert listed == [{"id": group, "name": "Sales team", "entra": None, "members": [{"id": bob, "name": "bob"}]}]

    client.delete(f"/api/admin/groups/{group}")
    assert lib not in permissions.readable_collection_ids(conn, bob)
    assert client.get("/api/admin/groups").json()["groups"] == []
