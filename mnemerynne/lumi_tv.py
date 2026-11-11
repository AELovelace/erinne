"""Who's playing? Each person's own Lumi on a shared TV.

Every household TV is paired to one account (media/screens.py), so a game on a TV loads that owner's data. In the
Lumi apps a remote's OK press says who is holding it instead: the TV page hands the press's signed proof
(casino_remote.issue, made by the server when the remote pressed) to web/games.py, which checks the remote may still
drive this TV (its owner's own, or a TV guest's) and answers with a play pass. The Lumi page sends that pass with each
request (the X-Lumi-Player header), and every Lumi route then loads the pass holder's pet, coins, garden and trips.

A pass names the screen, the remote, the remote's pairing and the person. It is checked again on every request:
re-pairing the remote, removing the guest, or using the pass from another screen ends it at once. It lasts
PASS_SECONDS at most; the page also forgets it after IDLE_SECONDS without a key press and asks again.
"""

from __future__ import annotations

from itsdangerous import BadData, URLSafeTimedSerializer

from mnemerynne import casino_remote

SALT = "lumi-tv-player-v1"       # its own salt: a casino press proof can never be read as a pass, nor the other way round
PASS_SECONDS = 12 * 3600         # the longest a pass lasts, even while it's in use
HEADER = "x-lumi-player"         # the request header the Lumi page sends it in
IDLE_SECONDS = 30 * 60           # the page forgets the pass after this long without a key press (game.js)

__all__ = ["BadData", "HEADER", "issue", "read"]


def _signer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=SALT)   # signs and timestamps the pass with the app's secret key


def issue(secret: str, screen, remote, user_id: int) -> str:
    """A pass for user_id, who holds `remote`, to play their own Lumi on `screen`."""
    return _signer(secret).dumps({
        "screen": screen["id"], "display": casino_remote.actor("screen", screen),   # the TV, and its pairing
        "remote": remote["id"], "pairing": casino_remote.pairing(remote),           # the remote, and its pairing
        "user": user_id,                                                            # whose Lumi is played
    })


def read(secret: str, token: str) -> dict:
    """The pass's contents once its signature and age check out; raises BadData otherwise. The caller still checks the
    remote, the guest link and the screen are live (web/games.py pet_user)."""
    return _signer(secret).loads(token, max_age=PASS_SECONDS)
