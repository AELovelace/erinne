"""Web Push (mnemerynne/push.py) and Lumi Pet's reminders (lumipet.sweep): the encryption, who we will talk to,
and that each need is mentioned once, never at night and never in a flurry. The reminder cases are ported from
MommyBot's "pet reminders require opt-in, coalesce episodes, cancel resolved needs" test."""

import base64
import json
import struct

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from mnemerynne import lumipet, push, users
from mnemerynne.db import transaction
from test_media import (_login, _no_network, _pair, _screens_for, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings)

HOUR = lumipet.HOUR
NOON = 1_800_014_400_000                # 12:00 UTC on a fixed day, in milliseconds
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Phone:
    """A stand-in browser: its own key pair and secret, able to open what push.encrypt sealed (RFC 8291's other half)."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.raw = self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.secret = b"0123456789abcdef"
        self.p256dh, self.auth = _b64(self.raw), _b64(self.secret)

    def open(self, body):
        salt, (size, keylen) = body[:16], struct.unpack(">IB", body[16:21])
        theirs_raw, sealed = body[21:21 + keylen], body[21 + keylen:]
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), theirs_raw))
        ikm = HKDF(hashes.SHA256(), 32, self.secret, b"WebPush: info\x00" + self.raw + theirs_raw).derive(shared)
        cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
        plain = AESGCM(cek).decrypt(nonce, sealed, None)
        assert size == 4096 and plain.endswith(b"\x02")
        return plain[:-1]


class Service:
    """A stand-in push service: keeps what it was sent and answers with a chosen status."""

    def __init__(self, status=201):
        self.status, self.got = status, []

    def __call__(self, request):
        self.got.append(request)
        return httpx.Response(self.status)


@pytest.fixture
def service():
    push.TRANSPORT = httpx.MockTransport(found := Service())
    yield found
    push.TRANSPORT = None


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


@pytest.fixture
def phone(conn, alice):
    device = Phone()
    with transaction(conn):
        push.subscribe(conn, alice, ENDPOINT, device.p256dh, device.auth, 0)
    return device


def _pet(conn, uid, now, **fields):
    """Make sure the pet exists (as if its person just looked), then put it in a known condition."""
    with transaction(conn):
        lumipet.state(conn, uid, now)
    pet = json.loads(conn.execute("SELECT data FROM lumi_pets WHERE user_id = ?", (uid,)).fetchone()[0])
    pet.update(fields)
    conn.execute("UPDATE lumi_pets SET data = ? WHERE user_id = ?", (json.dumps(pet), uid))


def _sweep(conn, settings, now):
    return lumipet.sweep(conn, settings.data_dir, "https://example.test", now=now)


def _needs(service, phone):
    return [json.loads(phone.open(request.content))["need"] for request in service.got]


# --------------------------------------------------------------------------- the envelope
def test_only_the_phone_can_read_a_message_and_every_message_is_sealed_afresh():
    phone = Phone()
    first, second = (push.encrypt(b'{"kind":"lumi-pet"}', phone.p256dh, phone.auth) for _ in range(2))
    assert phone.open(first) == phone.open(second) == b'{"kind":"lumi-pet"}'
    assert first != second and b"lumi" not in first
    with pytest.raises(Exception):
        Phone().open(first)                                                     # another phone's keys open nothing


def test_the_server_signs_for_one_push_service_with_a_key_it_keeps(settings):
    key = push.public_key(settings.data_dir)
    assert key == push.public_key(settings.data_dir) and len(_unb64(key)) == 65  # made once, then kept
    header = push._vapid(push._key(settings.data_dir), ENDPOINT + "?x=1", "https://example.test", 1_000_000)
    token, sent_key = header.removeprefix("vapid t=").split(", k=")
    head, claims, signature = token.split(".")
    assert sent_key == key and json.loads(_unb64(head)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(_unb64(claims)) == {"aud": "https://fcm.googleapis.com", "exp": 1_000_000 + 12 * 3600, "sub": "https://example.test"}
    raw = _unb64(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    push._key(settings.data_dir).public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


@pytest.mark.parametrize("endpoint, ok", [
    (ENDPOINT, True), ("https://updates.push.services.mozilla.com/wpush/v2/x", True), ("https://web.push.apple.com/x", True),
    ("https://db5p.notify.windows.com/w/?token=x", True), ("http://fcm.googleapis.com/x", False), ("https://192.168.1.5/x", False),
    ("https://localhost:38200/x", False), ("https://fcm.googleapis.com.evil.test/x", False), ("https://evilpush.apple.com/x", False),
    ("https://fcm.googleapis.com:8443/x", False), ("https://user@fcm.googleapis.com@10.0.0.1/x", False), ("nonsense", False)])
def test_only_real_push_services_are_ever_contacted(endpoint, ok):
    assert push.allowed(endpoint) is ok


# --------------------------------------------------------------------------- subscriptions and sending
def test_subscribing_checks_the_address_and_keys_and_refreshes_in_place(conn, alice):
    phone = Phone()
    for endpoint, p256dh, auth in (("https://10.0.0.1/hook", phone.p256dh, phone.auth), (ENDPOINT, "short", phone.auth),
                                   (ENDPOINT, phone.p256dh, "short"), (ENDPOINT, _b64(b"\x04" + b"\x01" * 64), phone.auth), (ENDPOINT, "!!", "!!")):
        with pytest.raises(push.PushError):
            push.subscribe(conn, alice, endpoint, p256dh, auth, 0)
    push.subscribe(conn, alice, ENDPOINT, phone.p256dh, phone.auth, 0)
    push.subscribe(conn, alice, ENDPOINT, phone.p256dh, phone.auth, 600)        # the same browser again, in a new time zone
    assert [row["utc_offset"] for row in push.subscriptions(conn, alice)] == [600]
    for n in range(push.MAX_PER_USER - 1):
        push.subscribe(conn, alice, f"{ENDPOINT}{n}", phone.p256dh, phone.auth, 0)
    with pytest.raises(push.PushError, match="Too many"):
        push.subscribe(conn, alice, ENDPOINT + "more", phone.p256dh, phone.auth, 0)
    push.unsubscribe(conn, alice, ENDPOINT)
    assert len(push.subscriptions(conn, alice)) == push.MAX_PER_USER - 1


def test_sending_reaches_each_device_and_forgets_one_that_has_gone(conn, settings, alice, phone, service):
    assert push.send(conn, settings.data_dir, "https://example.test", alice, {"kind": "lumi-pet", "need": "joy"}) == 1
    request = service.got[0]
    assert str(request.url) == ENDPOINT and request.headers["content-encoding"] == "aes128gcm" and request.headers["ttl"] == str(push.TTL)
    assert request.headers["authorization"].startswith("vapid t=") and json.loads(phone.open(request.content)) == {"kind": "lumi-pet", "need": "joy"}
    service.status = 410                                                        # the browser unsubscribed
    assert push.send(conn, settings.data_dir, "https://example.test", alice, {"kind": "lumi-pet", "need": "joy"}) == 0
    assert push.subscriptions(conn, alice) == []
    assert push.send(conn, settings.data_dir, "https://example.test", alice, {}) == 0 and len(service.got) == 2


def test_a_push_service_that_is_down_never_raises(conn, settings, alice, phone):
    def down(request):
        raise httpx.ConnectError("no route")
    push.TRANSPORT = httpx.MockTransport(down)
    try:
        assert push.send(conn, settings.data_dir, "https://example.test", alice, {"kind": "lumi-pet", "need": "joy"}) == 0
    finally:
        push.TRANSPORT = None
    assert len(push.subscriptions(conn, alice)) == 1                            # kept for next time


# --------------------------------------------------------------------------- Lumi's reminders
def test_a_need_is_mentioned_once_however_often_the_worker_looks(conn, settings, alice, phone, service):
    _pet(conn, alice, NOON, hunger=26.0)
    assert _sweep(conn, settings, NOON) == 0 and service.got == []              # not low yet
    for minutes in (60, 61, 62, 240):
        _sweep(conn, settings, NOON + minutes * 60_000)
    assert _needs(service, phone) == ["hunger"]
    row = conn.execute("SELECT kind, sent_at FROM lumi_push_events").fetchone()
    assert (row["kind"], row["sent_at"]) == ("hunger", NOON + HOUR)


def test_nobody_without_a_subscription_is_swept_or_sent_anything(conn, settings, alice, service):
    _pet(conn, alice, NOON, hunger=1.0)
    _sweep(conn, settings, NOON + 5 * HOUR)
    assert service.got == [] and conn.execute("SELECT COUNT(*) FROM lumi_push_events").fetchone()[0] == 0


def test_no_reminders_at_night_by_the_phones_clock_and_the_news_waits_for_morning(conn, settings, alice, service):
    device = Phone()
    push.subscribe(conn, alice, ENDPOINT, device.p256dh, device.auth, 11 * 60)  # a phone eleven hours ahead of UTC
    _pet(conn, alice, NOON, hunger=20.0, low={}, seen=NOON)                     # low from the very next look: 23:00 there
    _sweep(conn, settings, NOON + 60_000)
    assert service.got == []
    _sweep(conn, settings, NOON + 7 * HOUR)                                     # 06:00 there: still night
    assert service.got == []
    _sweep(conn, settings, NOON + 8 * HOUR + 60_000)                            # 07:01
    assert _needs(service, device) == ["hunger"]


def test_several_needs_become_one_notification_and_then_three_quiet_hours(conn, settings, alice, phone, service):
    _pet(conn, alice, NOON, hunger=10.0, joy=10.0, energy=60.0, low={})
    _sweep(conn, settings, NOON + 60_000)
    assert _needs(service, phone) == ["hunger"]                                 # hunger and joy together: the most pressing, once
    _pet(conn, alice, NOON + 60_000, energy=24.0, seen=NOON)                    # a new need arrives soon after...
    _sweep(conn, settings, NOON + 2 * HOUR)
    assert len(service.got) == 1                                                # ...and waits out the three hours
    _sweep(conn, settings, NOON + 3 * HOUR + 120_000)
    assert _needs(service, phone) == ["hunger", "energy"]


def test_a_need_she_was_fed_out_of_or_that_was_seen_is_never_sent(conn, settings, alice, service):
    device = Phone()
    push.subscribe(conn, alice, ENDPOINT, device.p256dh, device.auth, 11 * 60)  # night there, so the news has to wait
    _pet(conn, alice, NOON, hunger=20.0, low={})
    _sweep(conn, settings, NOON + 60_000)
    assert conn.execute("SELECT COUNT(*) FROM lumi_push_events WHERE sent_at IS NULL").fetchone()[0] == 1
    with transaction(conn):
        lumipet.act(conn, alice, "feed", item="apple", now=NOON + 120_000)      # fed before morning
    _sweep(conn, settings, NOON + 9 * HOUR)
    assert service.got == [] and conn.execute("SELECT COUNT(*) FROM lumi_push_events").fetchone()[0] == 0


def test_looking_at_a_hungry_lumi_and_leaving_is_not_news(conn, settings, alice, phone, service):
    _pet(conn, alice, NOON, hunger=20.0, low={})
    with transaction(conn):
        lumipet.state(conn, alice, NOON + 60_000)                               # her person saw she was hungry
    _sweep(conn, settings, NOON + HOUR)
    assert service.got == []


def test_a_nap_that_ends_while_away_is_mentioned_but_not_an_old_one(conn, settings, alice, phone, service):
    _pet(conn, alice, NOON)
    with transaction(conn):
        lumipet.act(conn, alice, "nap", now=NOON)
    _sweep(conn, settings, NOON + 60_000)
    assert service.got == []                                                    # still asleep
    _sweep(conn, settings, NOON + 6 * 60_000)
    assert _needs(service, phone) == ["nap"]
    assert lumipet.episodes({"low": {}, "seen": 0, "done": {"kind": "nap", "at": NOON}}, NOON + 2 * HOUR) == {}
    assert lumipet.episodes({"low": {}, "seen": 0, "done": {"kind": "play", "at": NOON}}, NOON + 60_000) == {}


def test_a_failed_send_is_tried_again_later(conn, settings, alice, phone, service):
    _pet(conn, alice, NOON, joy=5.0, low={})
    service.status = 503
    _sweep(conn, settings, NOON + 60_000)
    assert conn.execute("SELECT sent_at FROM lumi_push_events").fetchone()[0] is None
    service.status = 201
    _sweep(conn, settings, NOON + 120_000)
    assert _needs(service, phone)[-1] == "joy" and conn.execute("SELECT sent_at FROM lumi_push_events").fetchone()[0] == NOON + 120_000


def test_the_sweep_works_through_everyone_in_batches(conn, settings, service):
    device = Phone()
    ids = [users.create_user(conn, f"user{n}", "password123") for n in range(3)]
    for n, uid in enumerate(ids):
        push.subscribe(conn, uid, f"{ENDPOINT}{n}", device.p256dh, device.auth, 0)
        _pet(conn, uid, NOON, hunger=5.0, low={})
    cursor = lumipet.sweep(conn, settings.data_dir, "https://example.test", 0, 2, NOON + 60_000)
    assert cursor == ids[1] and len(service.got) == 2
    assert lumipet.sweep(conn, settings.data_dir, "https://example.test", cursor, 2, NOON + 60_000) == 0 and len(service.got) == 3


# --------------------------------------------------------------------------- API
@pytest.fixture
def on(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    return client


def test_a_browser_subscribes_and_unsubscribes_through_the_page(on, conn, games_conn):
    device = Phone()
    key = on.get("/api/media/games/lumi-pet/push").json()["key"]
    assert len(_unb64(key)) == 65
    body = {"endpoint": ENDPOINT, "p256dh": device.p256dh, "auth": device.auth, "utc_offset": -300}
    assert on.post("/api/media/games/lumi-pet/push", json=body).json() == {"on": True}
    assert games_conn.execute("SELECT utc_offset FROM push_subscriptions").fetchone()[0] == -300
    assert on.post("/api/media/games/lumi-pet/push", json={**body, "endpoint": "https://192.168.1.5/internal"}).status_code == 400
    assert on.post("/api/media/games/lumi-pet/push", json={**body, "utc_offset": 99999}).status_code == 422
    assert on.post("/api/media/games/lumi-pet/push/off", json={"endpoint": ENDPOINT}).json() == {"on": False}
    assert games_conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0
    on.cookies.clear()
    assert on.post("/api/media/games/lumi-pet/push", json=body).status_code == 401


def test_the_service_worker_is_served_from_the_top_and_only_handles_pushes(on):
    worker = on.get("/sw.js")
    assert worker.status_code == 200 and worker.headers["content-type"].startswith("text/javascript")
    assert worker.headers["cache-control"] == "no-cache"
    assert '"push"' in worker.text and 'addEventListener("fetch"' not in worker.text
