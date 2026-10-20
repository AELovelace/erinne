import pytest
from passlib.hash import pbkdf2_sha256

from erinne import auth


def test_hash_round_trip():
    hashed = auth.hash_password("correct horse")
    assert hashed.startswith("$pbkdf2-sha256$")
    assert auth.verify_password("correct horse", hashed)
    assert not auth.verify_password("wrong", hashed)


def test_verifies_microjames_style_hashes():
    # MicroJames stored passlib pbkdf2_sha256 hashes; they must keep working.
    legacy = pbkdf2_sha256.hash("legacy-pass")
    assert auth.verify_password("legacy-pass", legacy)


@pytest.mark.parametrize("bad", [None, "", "not-a-hash", "$unknown$abc"])
def test_verify_rejects_missing_or_garbage_hashes(bad):
    assert auth.verify_password("anything", bad) is False


@pytest.mark.parametrize("name, expected", [
    ("Rob", "rob"), (" james ", "james"), ("front.desk-2", "front.desk-2"), ("a", "a"),
])
def test_valid_usernames(name, expected):
    assert auth.validate_username(name) == expected


@pytest.mark.parametrize("name", ["", "-rob", "rob-", "../evil", "a b", "x" * 65, "evil<script>"])
def test_invalid_usernames(name):
    with pytest.raises(ValueError):
        auth.validate_username(name)


def test_short_passwords_rejected():
    with pytest.raises(ValueError):
        auth.validate_password("short")


def test_throttle_locks_after_failures_and_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    throttle = auth.LoginThrottle(max_failures=3, window_seconds=60)
    for _ in range(3):
        assert not throttle.locked_out("Rob", "10.0.0.1")
        throttle.record_failure("rob", "10.0.0.1")
    assert throttle.locked_out("ROB", "10.0.0.1")
    assert not throttle.locked_out("rob", "10.0.0.2")  # other IP unaffected
    now[0] += 61
    assert not throttle.locked_out("rob", "10.0.0.1")


def test_throttle_clear():
    throttle = auth.LoginThrottle(max_failures=1, window_seconds=60)
    throttle.record_failure("rob", "ip")
    assert throttle.locked_out("rob", "ip")
    throttle.clear("rob", "ip")
    assert not throttle.locked_out("rob", "ip")
