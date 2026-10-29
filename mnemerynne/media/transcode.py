"""Playing what a browser can't play by itself: ffmpeg writes HLS (fragmented MP4 segments) into a
session folder under <media_cache_dir>/sessions, and the page plays it with hls.js.

A session is one ffmpeg process. For a file it is the person's own (one per person and player
slot, so a new film or a seek replaces the old one); for a live stream it is shared by everyone
watching that source. Sessions nobody has fetched from for IDLE_SECONDS are stopped and their
folder removed, and every session folder is wiped when the web app starts.

Modes (probe.decide): 'remux' copies the picture and converts the sound only if it must; 'audio'
copies the picture and converts the sound; 'transcode' re-encodes the picture to H.264, on a GPU
when there is one:
- vaapi: the AMD card (RX 580) through VAAPI, h264_vaapi; it decodes H.264, HEVC, MPEG-2 and VC-1
  itself, other codecs are decoded on the CPU and uploaded;
- nvenc: the NVIDIA card (GTX 1050 Ti), h264_nvenc, decoding with CUDA where it can;
- software: libx264 on the CPU.
'auto' tries them in that order once (a two-second test encode) and keeps the first that works.
Only re-encodes count against the admin's limit (Settings → Entertainment → Media → transcodes at once).
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.media")

IDLE_SECONDS = 60
READY_SECONDS = 25
SEGMENT_SECONDS = 4
LIVE_SEGMENT_SECONDS = 2
MAX_HEIGHT = 1080
NAME = re.compile(r"^(index\.m3u8|init\.mp4|seg\d{5,}\.m4s)$")
BACKENDS = ("vaapi", "nvenc", "software")
HW_DECODE = {"vaapi": {"h264", "hevc", "mpeg2video", "vc1"},
             "nvenc": {"h264", "hevc", "mpeg2video", "vc1", "vp8", "vp9", "mpeg4"}}
COPY_AUDIO = {"aac", "mp3"}
MEDIA_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".mp4": "video/mp4", ".m4s": "video/iso.segment"}


class TranscodeError(Exception):
    """A session couldn't start; the message is safe to show."""
    status = 503


@dataclass
class Session:
    id: str
    key: str                    # "user:<id>:<slot>" or "live:<source id>"
    user_id: int | None
    file_id: int | None
    source_id: int | None
    mode: str
    start_s: float
    dir: Path
    proc: subprocess.Popen
    backend: str | None
    last_seen: float

    def alive(self) -> bool:
        return self.proc.poll() is None

    def counts(self) -> bool:
        """Takes a transcoding slot."""
        return self.mode == "transcode"


def _scale(hw: bool) -> str:
    return (f"scale_vaapi=w=-2:h='min({MAX_HEIGHT},ih)':format=nv12" if hw
            else f"scale=-2:'min({MAX_HEIGHT},ih)'")


class Manager:
    """Every running session of this web app (one uvicorn process, so in memory)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.media_dir / "sessions"
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._resolved: str | None = None
        self._vaapi: str | None | bool = False   # False: not looked for yet
        self._reaper: threading.Thread | None = None
        self._stopping = threading.Event()

    # ---------------------------------------------------------- lifecycle
    def startup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    def shutdown(self) -> None:
        self._stopping.set()
        self.stop_all()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or not self._reaper.is_alive():
            self._reaper = threading.Thread(target=self._reap_loop, name="media-reaper", daemon=True)
            self._reaper.start()

    def _reap_loop(self) -> None:
        while not self._stopping.wait(10):
            self.reap()
            with self._lock:
                if not self._sessions:
                    self._reaper = None
                    return

    # ----------------------------------------------------------- backends
    def _test(self, args: list[str]) -> tuple[bool, str]:
        cmd = [self.settings.media_ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *args]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        err = out.stderr.decode(errors="replace").strip().splitlines()
        return out.returncode == 0, (err[-1] if err else "ok")

    def _test_encode(self, backend: str, device: str | None = None) -> tuple[bool, str]:
        src = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24", "-t", "2"]
        if backend == "vaapi":
            return self._test(["-vaapi_device", device or "", *src, "-vf", "format=nv12,hwupload",
                               "-c:v", "h264_vaapi", "-f", "null", "-"])
        if backend == "nvenc":
            return self._test([*src, "-c:v", "h264_nvenc", "-f", "null", "-"])
        return self._test([*src, "-c:v", "libx264", "-preset", "veryfast", "-f", "null", "-"])

    def vaapi_device(self) -> str | None:
        """The render node that encodes H.264: MNEMERYNNE_MEDIA_VAAPI_DEVICE, else the first that works."""
        if self._vaapi is False:
            if self.settings.media_vaapi_device:
                self._vaapi = self.settings.media_vaapi_device
            else:
                self._vaapi = next((d for d in sorted(glob.glob("/dev/dri/renderD*"))
                                    if self._test_encode("vaapi", d)[0]), None)
        return self._vaapi or None

    def test_all(self) -> dict:
        """Each backend: does a short test encode work here? (Settings → Entertainment → Media → Test.)"""
        self._vaapi = False
        self._resolved = None
        device = self.vaapi_device()
        out = {"vaapi": {"ok": False, "detail": "no VAAPI render node can encode H.264"} if device is None
               else dict(zip(("ok", "detail"), self._test_encode("vaapi", device))) | {"device": device}}
        for backend in ("nvenc", "software"):
            out[backend] = dict(zip(("ok", "detail"), self._test_encode(backend)))
        return out

    def backend(self, choice: str) -> str:
        """The encoder for the admin's choice; 'auto' is resolved once."""
        if choice in BACKENDS:
            return choice
        if self._resolved is None:
            self._resolved = "software"
            for backend in ("vaapi", "nvenc"):
                device = self.vaapi_device() if backend == "vaapi" else None
                if backend == "vaapi" and device is None:
                    continue
                if self._test_encode(backend, device)[0]:
                    self._resolved = backend
                    break
            log.info("Media: transcoding with %s.", self._resolved)
        return self._resolved

    # ----------------------------------------------------------- commands
    @staticmethod
    def _hls(live: bool) -> list[str]:
        """HLS output, by names relative to the session folder ffmpeg runs in (it writes the fMP4 init
        segment to its working directory whatever the playlist's path)."""
        common = ["-f", "hls", "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
                  "-hls_segment_filename", "seg%05d.m4s", "-start_number", "0"]
        if live:
            return [*common, "-hls_time", str(LIVE_SEGMENT_SECONDS), "-hls_list_size", "8",
                    "-hls_flags", "delete_segments+independent_segments", "index.m3u8"]
        return [*common, "-hls_time", str(SEGMENT_SECONDS), "-hls_list_size", "0", "-hls_playlist_type", "event",
                "-hls_flags", "independent_segments", "index.m3u8"]

    def video_args(self, backend: str, codec: str | None) -> tuple[list[str], list[str]]:
        """(before -i, after -i) for re-encoding the picture to H.264 with `backend`."""
        keys = ["-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})"]
        rate = ["-b:v", "8M", "-maxrate", "10M", "-bufsize", "16M"]
        if backend == "vaapi":
            device = self.vaapi_device() or "/dev/dri/renderD128"
            if codec in HW_DECODE["vaapi"]:
                return (["-hwaccel", "vaapi", "-hwaccel_device", device, "-hwaccel_output_format", "vaapi"],
                        ["-vf", _scale(True), "-c:v", "h264_vaapi", *rate, *keys])
            return (["-vaapi_device", device],
                    ["-vf", f"{_scale(False)},format=nv12,hwupload", "-c:v", "h264_vaapi", *rate, *keys])
        if backend == "nvenc":
            pre = ["-hwaccel", "cuda"] if codec in HW_DECODE["nvenc"] else []
            return pre, ["-vf", f"{_scale(False)},format=yuv420p", "-c:v", "h264_nvenc", "-preset", "p4",
                         *rate, *keys]
        return [], ["-vf", f"{_scale(False)},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "21", "-maxrate", "10M", "-bufsize", "16M", *keys]

    def file_command(self, path: Path, kind: str, video_codec: str | None, audio_codec: str | None, mode: str,
                     start: float, backend: str | None) -> list[str]:
        """The ffmpeg command for a file; it runs in the session folder."""
        cmd = [self.settings.media_ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        audio = (["-c:a", "copy"] if audio_codec in COPY_AUDIO and mode == "remux"
                 else ["-c:a", "aac", "-b:a", "256k" if kind == "audio" else "192k", "-ac", "2"])
        if kind == "audio" or not video_codec:
            return [*cmd, "-ss", f"{start:.3f}", "-i", str(path), "-map", "0:a:0", "-vn", *audio,
                    *self._hls(live=False)]
        pre, post = ([], ["-c:v", "copy", *(["-tag:v", "hvc1"] if video_codec == "hevc" else [])])
        if mode == "transcode":
            pre, post = self.video_args(backend or "software", video_codec)
        return [*cmd, *pre, "-ss", f"{start:.3f}", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
                "-sn", "-dn", *post, *audio, *self._hls(live=False)]

    def live_command(self, url: str) -> list[str]:
        """Pull a live stream (rtmp://, HLS...) and repackage it; the picture is copied (H.264 expected)."""
        return [self.settings.media_ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-rw_timeout", "15000000", "-i", url, "-map", "0:v:0?", "-map", "0:a:0?", "-sn", "-dn",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ac", "2", *self._hls(live=True)]

    # ----------------------------------------------------------- sessions
    def _spawn(self, key: str, cmd: list[str], **fields) -> Session:
        """Under the lock: run ffmpeg in a new session folder."""
        sid = uuid.uuid4().hex
        out = self.root / sid
        out.mkdir(parents=True, exist_ok=True)
        log.info("Media: session %s: %s", sid[:8], " ".join(cmd))
        with open(out / "ffmpeg.log", "wb") as errlog:
            try:
                proc = subprocess.Popen(cmd, cwd=out, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=errlog)
            except OSError as exc:
                shutil.rmtree(out, ignore_errors=True)
                raise TranscodeError(f"ffmpeg couldn't start on the server: {exc}") from None
        session = Session(sid, key, dir=out, proc=proc, last_seen=time.monotonic(), **fields)
        self._sessions[sid] = session
        self._ensure_reaper()
        return session

    def start_file(self, *, user_id: int, slot: str, file_id: int, path: Path, kind: str, video_codec: str | None,
                   audio_codec: str | None, mode: str, start: float, hwaccel: str, max_transcodes: int) -> Session:
        key = f"user:{user_id}:{slot}"
        with self._lock:
            for old in [s for s in self._sessions.values() if s.key == key]:
                self._stop(old)
            if mode == "transcode" and kind == "video":
                if max_transcodes <= 0:
                    raise TranscodeError("Transcoding is turned off on this server (Settings → Entertainment → Media).")
                if sum(1 for s in self._sessions.values() if s.counts() and s.alive()) >= max_transcodes:
                    raise TranscodeError("Every transcoding slot is busy right now; try again in a minute.")
        backend = self.backend(hwaccel) if mode == "transcode" and kind == "video" else None
        with self._lock:
            return self._spawn(key, self.file_command(path, kind, video_codec, audio_codec, mode, start, backend),
                               user_id=user_id, file_id=file_id, source_id=None,
                               mode=mode if kind == "video" else "audio", start_s=start, backend=backend)

    def watch_live(self, source_id: int, url: str) -> Session:
        key = f"live:{source_id}"
        with self._lock:
            for s in self._sessions.values():
                if s.key == key and s.alive():
                    s.last_seen = time.monotonic()
                    return s
            for old in [s for s in self._sessions.values() if s.key == key]:
                self._stop(old)
            return self._spawn(key, self.live_command(url), user_id=None, file_id=None,
                               source_id=source_id, mode="live", start_s=0.0, backend=None)

    def get(self, sid: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is not None:
                session.last_seen = time.monotonic()
            return session

    def wait_ready(self, session: Session, timeout: float = READY_SECONDS) -> None:
        """Block until the playlist lists a segment; TranscodeError with ffmpeg's complaint if it dies."""
        playlist = session.dir / "index.m3u8"
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if "#EXTINF" in playlist.read_text(encoding="utf-8", errors="replace"):
                    return
            except OSError:
                pass
            if not session.alive():
                break
            time.sleep(0.2)
        problem = self.log_tail(session)
        with self._lock:
            self._stop(session)
        raise TranscodeError("The server couldn't prepare this for playback"
                             + (f": {problem}" if problem else " in time."))

    @staticmethod
    def log_tail(session: Session) -> str:
        try:
            lines = (session.dir / "ffmpeg.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            return ""
        return lines[-1][:300] if lines else ""

    def stop(self, sid: str, user_id: int | None = None) -> bool:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None or (user_id is not None and session.user_id != user_id):
                return False
            self._stop(session)
            return True

    def _stop(self, session: Session) -> None:
        """Under the lock."""
        self._sessions.pop(session.id, None)
        if session.alive():
            session.proc.terminate()
            try:
                session.proc.wait(5)
            except subprocess.TimeoutExpired:
                session.proc.kill()
        shutil.rmtree(session.dir, ignore_errors=True)

    def stop_live(self, source_id: int) -> None:
        with self._lock:
            for session in [s for s in self._sessions.values() if s.source_id == source_id]:
                self._stop(session)

    def stop_all(self) -> None:
        with self._lock:
            for session in list(self._sessions.values()):
                self._stop(session)

    def reap(self) -> int:
        cutoff = time.monotonic() - IDLE_SECONDS
        with self._lock:
            idle = [s for s in self._sessions.values() if s.last_seen < cutoff]
            for session in idle:
                self._stop(session)
        return len(idle)

    def running(self) -> list[dict]:
        with self._lock:
            return [{"id": s.id[:8], "mode": s.mode, "backend": s.backend, "file_id": s.file_id,
                     "source_id": s.source_id, "alive": s.alive(),
                     "idle_s": round(time.monotonic() - s.last_seen)} for s in self._sessions.values()]

    def file(self, session: Session, name: str) -> Path | None:
        """A playlist or segment of the session, or None."""
        if not NAME.match(name):
            return None
        path = session.dir / name
        return path if path.is_file() else None


def media_type_of(name: str) -> str:
    return MEDIA_TYPES.get(os.path.splitext(name)[1], "application/octet-stream")
