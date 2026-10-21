import pytest

from mnemerynne import permissions, users


@pytest.fixture
def people(conn):
    ids = {name: users.create_user(conn, name, "password123") for name in ("alice", "bob", "carol")}
    return ids


def test_user_gets_private_inbox(conn, people):
    inbox = permissions.private_collection_id(conn, people["alice"])
    assert inbox is not None
    assert permissions.readable_collection_ids(conn, people["alice"]) == {inbox}


def test_duplicate_username_is_case_insensitive(conn, people):
    with pytest.raises(users.UserExists):
        users.create_user(conn, "ALICE", "password123")


def test_authenticate_and_disable(conn, people):
    assert users.authenticate(conn, "Alice", "password123")["id"] == people["alice"]
    assert users.authenticate(conn, "alice", "nope") is None
    assert users.authenticate(conn, "nobody", "password123") is None
    users.set_active(conn, "alice", False)
    assert users.authenticate(conn, "alice", "password123") is None


def test_private_inboxes_are_isolated(conn, people):
    alice_inbox = permissions.private_collection_id(conn, people["alice"])
    bob_inbox = permissions.private_collection_id(conn, people["bob"])
    assert bob_inbox not in permissions.readable_collection_ids(conn, people["alice"])
    assert alice_inbox not in permissions.readable_collection_ids(conn, people["bob"])


def test_private_inbox_cannot_be_granted(conn, people):
    bob_inbox = permissions.private_collection_id(conn, people["bob"])
    with pytest.raises(ValueError, match="private"):
        permissions.grant_user(conn, bob_inbox, people["alice"])


def test_library_grants_direct_and_group(conn, people):
    brand = permissions.create_library(conn, "BrandResBW")
    oracle = permissions.create_library(conn, "Oracle")
    permissions.grant_user(conn, brand, people["alice"])
    group = permissions.create_group(conn, "Front desk", [people["bob"], people["carol"]])
    permissions.grant_group(conn, oracle, group)

    assert brand in permissions.readable_collection_ids(conn, people["alice"])
    assert oracle not in permissions.readable_collection_ids(conn, people["alice"])
    assert oracle in permissions.readable_collection_ids(conn, people["bob"])
    assert brand not in permissions.readable_collection_ids(conn, people["bob"])

    # Read grants don't allow uploading; write grants do; re-granting updates.
    assert brand not in permissions.writable_collection_ids(conn, people["alice"])
    permissions.grant_user(conn, brand, people["alice"], can_write=True)
    assert brand in permissions.writable_collection_ids(conn, people["alice"])

    permissions.revoke_user(conn, brand, people["alice"])
    assert brand not in permissions.readable_collection_ids(conn, people["alice"])


def test_readable_collections_lists_inbox_first(conn, people):
    permissions.grant_user(conn, permissions.create_library(conn, "Oracle"), people["alice"])
    kinds = [c["kind"] for c in permissions.readable_collections(conn, people["alice"])]
    assert kinds == ["private", "library"]


def test_deleting_user_removes_their_inbox(conn, people):
    inbox = permissions.private_collection_id(conn, people["alice"])
    assert users.delete_user(conn, "alice")
    assert conn.execute("SELECT count(*) FROM collections WHERE id = ?", (inbox,)).fetchone()[0] == 0


def test_library_names_unique_case_insensitive(conn):
    permissions.create_library(conn, "Oracle")
    with pytest.raises(Exception):
        permissions.create_library(conn, "oracle")
