"""What a media file is: ffprobe for streams and duration, mutagen for music tags and embedded cover
art, Pillow for a picture's size and EXIF, and the file name for a film's title and year.

Also decides how a file plays in a browser (`playback`): straight from the share ('direct'), its
streams copied into HLS ('remux'), the video copied and the sound converted ('audio'), or the
video re-encoded ('transcode'). The catalog stores the answer for a typical browser; the player
asks again with what its browser says it can decode (decide()).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.media")

# What browsers play without help, by container and codec (ffprobe's names).
DIRECT_CONTAINERS = {"mp4", "m4v", "mov", "webm", "mp3", "flac", "ogg", "oga", "opus", "m4a", "wav", "aac"}
BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac"}
PCM = re.compile(r"^pcm_(s16le|s24le|u8|f32le)$")
# A film's file name: the title, then the year; release tags after it are dropped.
_YEAR = re.compile(r"^(?P<title>.+?)[\s._\-\[(]+(?P<year>(?:19|20)\d{2})(?:[\s._\-\])]|$)")
_JUNK = re.compile(r"[\s._\-](?:2160p|1080p|720p|480p|4k|uhd|bluray|blu-ray|brrip|bdrip|web-?dl|webrip|hdrip|dvdrip|"
                   r"x264|x265|h\.?264|h\.?265|hevc|aac|ac3|dts|hdr|remux|proper|repack|extended|unrated)\b.*$",
                   re.IGNORECASE)
# An episode's file name: season and episode as 'S01E02' (also 's1e2', 'S01E01E02') or '1x02'; or the episode
# alone as 'E05' / 'Ep 5' / 'Episode 5', or a leading '05', the season then coming from its folder.
_SXE = re.compile(r"(?:^|[\s._\-\[(])s(?P<s>\d{1,3})[\s._\-]?e(?P<e>\d{1,4})(?:[\s._\-]?e\d{1,4})*"
                  r"(?=$|[^a-z0-9])", re.IGNORECASE)
_NXN = re.compile(r"(?:^|[\s._\-\[(])(?P<s>\d{1,2})x(?P<e>\d{1,3})(?=$|[\s._\-\])])", re.IGNORECASE)
_EPISODE = re.compile(r"(?:^|[\s._\-\[(])(?:e|ep|episode)[\s._\-]?(?P<e>\d{1,4})(?=$|[\s._\-\])])", re.IGNORECASE)
_LEADING = re.compile(r"^(?P<e>\d{1,3})(?=$|[\s._\-])")
# A season's folder: 'Season 2', 'Series 2', 'S02'; 'Specials' is season 0.
_SEASON_FOLDER = re.compile(r"^(?:season|series|s)[\s._\-]*(?P<s>\d{1,3})$", re.IGNORECASE)


@dataclass
class Info:
    """Everything the catalog keeps about one file; empty fields are unknown."""
    title: str = ""
    year: int | None = None
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bitrate: int | None = None
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    track_no: int | None = None
    disc_no: int | None = None
    season_no: int | None = None                             # TV (0: Specials)
    episode_no: int | None = None                            # TV
    genre: str = ""
    taken_at: str | None = None
    camera: str = ""
    pages: int | None = None                                 # documents (PDF)
    cover: bytes | None = field(default=None, repr=False)   # embedded art


# ------------------------------------------------------------------- names
def movie_name(stem: str) -> tuple[str, int | None]:
    """'Blade Runner (1982)', 'Blade.Runner.1982.1080p.BluRay' -> ('Blade Runner', 1982)."""
    match = _YEAR.match(stem)
    if match:
        title, year = match.group("title"), int(match.group("year"))
    else:
        title, year = _JUNK.sub("", stem), None
    title = re.sub(r"[._]+", " ", title).strip(" -[(")
    return (title or stem), year


def season_of_folder(name: str) -> int | None:
    """'Season 2', 'Series 2', 'S02' -> 2; 'Specials' -> 0; any other folder name -> None."""
    name = name.strip()
    if name.lower() in ("specials", "special"):
        return 0
    match = _SEASON_FOLDER.match(name)
    return int(match.group("s")) if match else None


def _episode_title(text: str) -> str:
    """What's left of an episode's name once its numbers are gone, release tags and separators removed."""
    text = _JUNK.sub("", " " + text)                       # the space lets a tag right at the start match
    return re.sub(r"[._]+", " ", text).strip(" -[]()")


def episode_name(stem: str, folder: str = "") -> tuple[str, int | None, int | None]:
    """An episode's (title, season, episode) from its file name and its folder's name:
    'Show S01E02 The Title' -> ('The Title', 1, 2); 'show.1x02' -> ('Episode 2', 1, 2);
    '05 - The Title' in 'Season 3' -> ('The Title', 3, 5). The name's own season beats the folder's;
    with no number at all, the title is the tidied name and the episode None."""
    season = season_of_folder(folder)
    for pattern in (_SXE, _NXN):
        match = pattern.search(stem)
        if match:
            episode = int(match.group("e"))
            return _episode_title(stem[match.end():]) or f"Episode {episode}", int(match.group("s")), episode
    match = _EPISODE.search(stem) or _LEADING.match(stem)
    if match:
        episode = int(match.group("e"))
        return _episode_title(stem[match.end():]) or f"Episode {episode}", season, episode
    return _episode_title(stem) or stem, season, None


def sort_title(title: str) -> str:
    low = title.strip().lower()
    for article in ("the ", "a ", "an "):
        if low.startswith(article) and len(low) > len(article):
            return low[len(article):]
    return low


def _int(value) -> int | None:
    """'3/12' -> 3; junk -> None."""
    if value is None:
        return None
    match = re.match(r"\s*(\d+)", str(value))
    return int(match.group(1)) if match else None


# ----------------------------------------------------------------- ffprobe
def ffprobe(settings: Settings, path: Path) -> dict | None:
    """ffprobe's JSON for the file, or None when there is no ffprobe or it can't read the file."""
    try:
        out = subprocess.run([settings.media_ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                              "-show_streams", str(path)], capture_output=True, timeout=settings.media_probe_timeout,
                             check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("ffprobe failed on %s: %s", path, exc)
        return None
    if out.returncode != 0:
        log.info("ffprobe couldn't read %s: %s", path, out.stderr.decode(errors="replace").strip()[:300])
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def _apply_ffprobe(info: Info, data: dict, path: Path) -> None:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    try:
        info.duration_s = float(fmt["duration"]) if fmt.get("duration") else None
    except ValueError:
        pass
    info.bitrate = _int(fmt.get("bit_rate"))
    info.container = path.suffix.lower().lstrip(".") or (fmt.get("format_name") or "").split(",")[0]
    if video:
        info.video_codec = video.get("codec_name")
        info.width, info.height = video.get("width"), video.get("height")
    if audio:
        info.audio_codec = audio.get("codec_name")
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    info.title = info.title or tags.get("title", "")
    info.artist = info.artist or tags.get("artist", "")
    info.album_artist = info.album_artist or tags.get("album_artist", "")
    info.album = info.album or tags.get("album", "")
    info.genre = info.genre or tags.get("genre", "")
    info.track_no = info.track_no or _int(tags.get("track"))
    info.disc_no = info.disc_no or _int(tags.get("disc"))
    info.year = info.year or _int(tags.get("date") or tags.get("year"))


# ------------------------------------------------------------------ mutagen
def _music_tags(info: Info, path: Path) -> None:
    try:
        import mutagen
        easy = mutagen.File(path, easy=True)
        full = mutagen.File(path)
    except Exception as exc:  # mutagen raises many kinds on odd files
        log.info("No tags read from %s: %s", path, exc)
        return
    if easy is not None and easy.tags:
        def first(key: str) -> str:
            value = easy.tags.get(key)
            return str(value[0]).strip() if value else ""
        info.title = first("title")
        info.artist = first("artist")
        info.album_artist = first("albumartist")
        info.album = first("album")
        info.genre = first("genre")
        info.track_no = _int(first("tracknumber"))
        info.disc_no = _int(first("discnumber"))
        info.year = _int(first("date") or first("originaldate"))
    if full is not None:
        info.cover = _embedded_cover(full)
        if info.duration_s is None and getattr(full, "info", None) is not None:
            info.duration_s = getattr(full.info, "length", None)


def _embedded_cover(audio) -> bytes | None:
    pictures = getattr(audio, "pictures", None)   # FLAC
    if pictures:
        front = next((p for p in pictures if p.type == 3), pictures[0])
        return front.data
    tags = getattr(audio, "tags", None)
    if tags is None:
        return None
    try:
        apic = [tags[k] for k in tags.keys() if str(k).startswith("APIC")]   # MP3 (ID3)
    except Exception:
        apic = []
    if apic:
        front = next((p for p in apic if getattr(p, "type", None) == 3), apic[0])
        return front.data
    covr = tags.get("covr") if hasattr(tags, "get") else None   # MP4
    if covr:
        return bytes(covr[0])
    for key in ("metadata_block_picture",):   # Ogg/Opus
        value = tags.get(key) if hasattr(tags, "get") else None
        if value:
            import base64
            from mutagen.flac import Picture
            try:
                return Picture(base64.b64decode(value[0])).data
            except Exception:
                return None
    return None


# --------------------------------------------------------------------- EXIF
def _picture(info: Info, path: Path) -> None:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as img:
            info.width, info.height = img.size
            info.container = (img.format or path.suffix.lstrip(".")).lower()
            exif = img.getexif()
            taken = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)   # DateTimeOriginal, DateTime
            make, model = str(exif.get(0x010F) or "").strip(), str(exif.get(0x0110) or "").strip()
            if taken:
                match = re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", str(taken))
                if match:
                    y, mo, d, h, mi, s = match.groups()
                    info.taken_at = f"{y}-{mo}-{d}T{h}:{mi}:{s}"
            info.camera = model if model.lower().startswith(make.lower()) else f"{make} {model}".strip()
            orientation = exif.get(0x0112)
            if orientation in (5, 6, 7, 8) and info.width and info.height:
                info.width, info.height = info.height, info.width
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        log.info("Couldn't read the picture %s: %s", path, exc)


# --------------------------------------------------------------------- read
def read(settings: Settings, path: Path, section: str, kind: str) -> Info:
    """Probe one file of a section; kind is 'audio', 'video' or 'image' (media.media_type)."""
    info = Info()
    if kind == "image":
        _picture(info, path)
        info.title = path.stem
        return info
    if kind == "document":
        from mnemerynne.ingest import pdf
        info.title = path.stem
        info.container = "pdf"
        info.pages = pdf.page_count(path)     # PdfError (a ValueError) marks the file as failed
        return info
    if kind == "audio":
        _music_tags(info, path)
    data = ffprobe(settings, path)
    if data is not None:
        _apply_ffprobe(info, data, path)
    else:
        info.container = path.suffix.lower().lstrip(".")
    if section == "movies":
        title, year = movie_name(path.stem)
        # A film's own title tag is often the release group's; the folder or file name is what people chose.
        info.title, info.year = title, year or info.year
    elif section == "tv":
        # Like a film's, an episode's title tag is often the release's; its name says season and episode.
        info.title, info.season_no, info.episode_no = episode_name(path.stem, path.parent.name)
    elif not info.title:
        info.title = re.sub(r"^\d+[\s._\-]+", "", path.stem) or path.stem
    return info


# ----------------------------------------------------------------- playback
def decide(kind: str, container: str | None, video: str | None, audio: str | None, caps: dict | None = None) -> str:
    """'direct', 'remux', 'audio' or 'transcode' for a browser with `caps` (what MediaSource says it
    plays beyond the baseline: hevc, ac3, eac3, alac)."""
    caps = caps or {}
    video_ok = set(BROWSER_VIDEO) | ({"hevc"} if caps.get("hevc") else set())
    audio_ok = set(BROWSER_AUDIO) | {c for c in ("ac3", "eac3", "alac") if caps.get(c)}
    container = (container or "").lower()
    audio_fine = audio is None or audio in audio_ok or bool(PCM.match(audio or ""))
    if kind in ("image", "document"):
        return "direct"
    if kind == "audio" or video is None:
        if container in DIRECT_CONTAINERS and audio_fine:
            return "direct"
        return "transcode"   # sound only: always cheap
    if video not in video_ok:
        return "transcode"
    if container in DIRECT_CONTAINERS and container not in ("mp3", "flac", "wav", "aac") and audio_fine:
        return "direct"
    # MKV, AVI, TS...: the picture can be copied; the sound too if the browser decodes it.
    return "remux" if audio_fine else "audio"
