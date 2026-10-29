"""Speakers: a remote panel with a speaker (the FNK0104B build of Mnemerynne-tab) that is also one of its
owner's screens, so the phone casts music and radio to it as it does to a TV.

Pairing: the panel asks for its code as a speaker (POST /media/remote/pair {"speaker": true}). Claiming that
code makes the remote and a screen of the same name marked `speaker`, linked by media_remotes.screen_id.
The screen's own key is random and never handed out, so a speaker is only ever known by its remote's key,
and that key never opens Media.

Casting: commands for a speaker are rewritten before they reach its stream (web/media.py _for_speaker).
A track or an album becomes a queue of tickets, one per track. A ticket is random, bound to the speaker,
the file and the person who cast it, and good for TICKET_SECONDS. The panel fetches
/media/speaker/audio/<ticket>.<ext> with the ticket alone, because its audio library can't send a header.
At every fetch the server checks again that the speaker still exists and the person may still read the
file. Removing the remote (or the speaker) ends its tickets. Stations go out as their own address, since
they are public. Films, live TV, YouTube and screen-size calibration are refused with a message.

Formats: the panel decodes MP3, AAC (.m4a or .aac), FLAC, 16-bit WAV and Ogg (Vorbis, Opus), picking the
decoder by extension. Anything else is converted to MP3 once by ffmpeg and kept in a small cache, so every
ticket serves a whole file with Range (the panel seeks with it).
"""

from __future__ import annotations

import os
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

TICKET_SECONDS = 6 * 3600        # long enough for an album, short enough that a copied link soon dies
MAX_TICKETS = 5000               # server-wide; the oldest go first
MAX_QUEUE = 200                  # tracks in one cast
CACHE_FILES = 40                 # converted MP3s kept
CONVERT_SECONDS = 300            # one track's conversion at most

# (container, codec) the panel plays as it is (ffprobe's codec names; the container is the file's suffix),
# and the extension its audio library picks the decoder by.
DIRECT = {("mp3", "mp3"): "mp3", ("m4a", "aac"): "m4a", ("mp4", "aac"): "m4a", ("aac", "aac"): "aac",
          ("flac", "flac"): "flac", ("ogg", "vorbis"): "ogg", ("oga", "vorbis"): "ogg", ("ogg", "opus"): "opus",
          ("opus", "opus"): "opus", ("wav", "pcm_s16le"): "wav"}
MIME = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac", "flac": "audio/flac", "ogg": "audio/ogg",
        "opus": "audio/ogg", "wav": "audio/wav"}


class SpeakerError(ValueError):
    """A refusal; the message is safe to show."""
    status = 422


def plan(container: str | None, codec: str | None) -> tuple[str, bool]:
    """(extension, convert?) for a track: as it is when the panel decodes it, else converted to MP3."""
    ext = DIRECT.get(((container or "").lower(), (codec or "").lower()))
    return (ext, False) if ext else ("mp3", True)


@dataclass(frozen=True)
class Ticket:
    screen_id: int
    user_id: int                 # who cast it: their access is checked again at every fetch
    file_id: int
    ext: str
    convert: bool
    until: float


class Tickets:
    """Tickets handed to speakers, in memory: a restart ends them (the panel just skips the track).
    Endpoints run on worker threads too, so a lock guards the table."""

    def __init__(self, clock=time.monotonic) -> None:
        self._tickets: dict[str, Ticket] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def issue(self, screen_id: int, user_id: int, file_id: int, ext: str, convert: bool) -> str:
        """A new ticket's name as the panel fetches it: '<token>.<ext>'."""
        now = self._clock()
        token = secrets.token_urlsafe(24)
        with self._lock:
            for key in [k for k, t in self._tickets.items() if t.until <= now]:
                del self._tickets[key]
            while len(self._tickets) >= MAX_TICKETS:
                del self._tickets[next(iter(self._tickets))]          # dicts keep insertion order: the oldest
            self._tickets[token] = Ticket(screen_id, user_id, file_id, ext, convert, now + TICKET_SECONDS)
        return f"{token}.{ext}"

    def get(self, name: str) -> Ticket | None:
        """The ticket a fetched name stands for, while it lasts and only with its own extension."""
        token, _, ext = name.partition(".")
        with self._lock:
            ticket = self._tickets.get(token)
        if ticket is None or ticket.ext != ext or ticket.until <= self._clock():
            return None
        return ticket

    def forget(self, screen_id: int) -> None:
        """The speaker is gone: its tickets stop working at once."""
        with self._lock:
            for key in [k for k, t in self._tickets.items() if t.screen_id == screen_id]:
                del self._tickets[key]


_converting = threading.Lock()


def converted(ffmpeg: str, cache: Path, file_id: int, source: Path) -> Path:
    """The track as MP3 (192 kbit/s), converted once and cached by file and modification time. Blocking:
    call it from a worker thread. One conversion runs at a time, so a queue of casts can't swamp the NAS."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{file_id}-{int(source.stat().st_mtime)}.mp3"
    with _converting:
        if target.is_file():
            os.utime(target)                                            # recently used: kept longest
            return target
        partial = target.with_suffix(".part")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source), "-vn",
               "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-f", "mp3", str(partial)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=CONVERT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            partial.unlink(missing_ok=True)
            raise SpeakerError("This track couldn't be converted for the speaker.") from exc
        partial.replace(target)
        old = sorted(cache.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for stale in old[:-CACHE_FILES]:
            stale.unlink(missing_ok=True)
    return target
