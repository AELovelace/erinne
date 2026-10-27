"""Posters, album covers, backdrops and picture thumbnails, stored as JPEGs under
<media_cache_dir>/art and recorded in media_artwork.

Where art comes from, best first: an image beside the file (Kodi's names: poster.jpg,
<film>-poster.jpg, folder.jpg, cover.jpg, fanart.jpg...), art embedded in a music file, then
TMDB or the Cover Art Archive (scrape.py), and for films with nothing else a frame from the film.
"""

from __future__ import annotations

import hashlib
import io
import logging
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.media")

# Longest side, per kind.
SIZES = {"poster": 680, "cover": 600, "fanart": 1920, "thumb": 480}
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
POSTER_NAMES = ("poster", "folder", "cover", "movie")
FANART_NAMES = ("fanart", "backdrop", "background")
COVER_NAMES = ("cover", "folder", "front", "album", "albumart", "albumartsmall")


def art_dir(settings: Settings) -> Path:
    return settings.media_dir / "art"


def path_of(settings: Settings, rel_path: str) -> Path:
    return art_dir(settings) / rel_path


@dataclass
class Prepared:
    """An image resized for its kind, not yet stored (prepare() is slow; store() is quick, under the lock)."""
    kind: str
    source: str
    body: bytes
    width: int
    height: int


def prepare(data: bytes | None, kind: str, source: str) -> Prepared | None:
    """Resize to a JPEG for `kind`; None if there is nothing or Pillow can't read it."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((SIZES[kind], SIZES[kind]))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, "JPEG", quality=84, optimize=True)
            width, height = img.size
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        log.info("Not usable as %s art: %s", kind, exc)
        return None
    return Prepared(kind, source, out.getvalue(), width, height)


def store(conn: sqlite3.Connection, settings: Settings, art: Prepared | None) -> int | None:
    """Its media_artwork id (in the caller's transaction). The same picture stored twice is one row,
    named by its content."""
    if art is None:
        return None
    body, kind, source, width, height = art.body, art.kind, art.source, art.width, art.height
    digest = hashlib.sha256(body).hexdigest()
    rel = f"{digest[:2]}/{digest[2:26]}-{kind}.jpg"
    found = conn.execute("SELECT id FROM media_artwork WHERE rel_path = ?", (rel,)).fetchone()
    if found:
        return found["id"]
    dest = path_of(settings, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(dest)
    return conn.execute("INSERT INTO media_artwork(kind, rel_path, source, width, height) VALUES (?, ?, ?, ?, ?)",
                        (kind, rel, source, width, height)).lastrowid


def source_of(conn: sqlite3.Connection, artwork_id: int | None) -> str | None:
    if artwork_id is None:
        return None
    row = conn.execute("SELECT source FROM media_artwork WHERE id = ?", (artwork_id,)).fetchone()
    return row["source"] if row else None


# ------------------------------------------------------------------ finding
def _beside(folder: Path, names: tuple[str, ...]) -> Path | None:
    """The first of `names` (any image suffix, any case) in the folder."""
    try:
        files = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return None
    for name in names:
        for suffix in IMAGE_SUFFIXES:
            if (name + suffix) in files:
                return files[name + suffix]
    return None


def movie_sidecars(path: Path) -> tuple[Path | None, Path | None]:
    """(poster, fanart) beside a film: <name>-poster.jpg / <name>.jpg first, then the folder's own."""
    stem = path.stem.lower()
    poster = _beside(path.parent, (f"{stem}-poster", stem, *POSTER_NAMES))
    fanart = _beside(path.parent, (f"{stem}-fanart", *FANART_NAMES))
    return poster, fanart


def album_sidecar(path: Path) -> Path | None:
    return _beside(path.parent, COVER_NAMES)


def frame(settings: Settings, path: Path, at_s: float) -> bytes | None:
    """One frame of a video as JPEG bytes, or None."""
    try:
        out = subprocess.run([settings.media_ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                              "-ss", f"{max(at_s, 0):.1f}", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale='min(1280,iw)':-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                             capture_output=True, timeout=settings.media_probe_timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("No frame from %s: %s", path, exc)
        return None
    return out.stdout if out.returncode == 0 and out.stdout else None


def read_file(path: Path, limit: int = 40 * 1024 * 1024) -> bytes | None:
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_bytes()
    except OSError:
        return None
