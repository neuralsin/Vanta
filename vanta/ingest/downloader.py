"""Built-in reel downloader (yt-dlp) for Instagram / TikTok / YouTube Shorts links.

Downloads are for private style analysis only. Reference music is an analysis
signal, never reused; after analysis the source file is deleted unless the user
opts to keep reference media (manual 21: keep derived features and thumbnails).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from vanta.config import Settings, find_tool, get_settings

SUPPORTED_HOSTS = (
    "instagram.com",
    "instagr.am",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "fb.watch",
)

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class DownloadError(RuntimeError):
    def __init__(self, message: str, *, needs_login: bool = False):
        super().__init__(message)
        self.needs_login = needs_login


@dataclass
class DownloadedReel:
    url: str
    path: Path
    title: str
    uploader: str | None
    duration_s: float | None
    description: str
    platform: str
    video_id: str
    metadata: dict = field(default_factory=dict)


def extract_urls(text: str) -> list[str]:
    """Pull every supported link out of pasted text (one per line, or mixed with prose)."""
    seen: list[str] = []
    for match in URL_PATTERN.findall(text or ""):
        url = match.rstrip(").,;]")
        if any(host in url.casefold() for host in SUPPORTED_HOSTS) and url not in seen:
            seen.append(url)
    return seen


def _platform(url: str) -> str:
    lower = url.casefold()
    for name in ("instagram", "tiktok", "youtube", "vimeo", "facebook"):
        if name in lower:
            return name
    if "youtu.be" in lower:
        return "youtube"
    if "x.com" in lower or "twitter" in lower:
        return "x"
    return "web"


def download_reel(
    url: str,
    *,
    settings: Settings | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> DownloadedReel:
    settings = settings or get_settings()
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise DownloadError("yt-dlp is not installed") from exc
    target = settings.references_dir / "downloads"
    target.mkdir(parents=True, exist_ok=True)

    def hook(status: dict) -> None:
        if not progress:
            return
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            done = status.get("downloaded_bytes") or 0
            progress(done / total if total else 0.0, "downloading")
        elif status.get("status") == "finished":
            progress(1.0, "merging")

    options: dict = {
        "outtmpl": str(target / "%(extractor)s_%(id)s.%(ext)s"),
        "format": "bv*[height<=1920]+ba/b[height<=1920]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "restrictfilenames": True,
        "writethumbnail": False,
        "retries": 3,
        "socket_timeout": 30,
        "match_filter": yt_dlp.utils.match_filter_func(
            f"duration < {int(settings.ingest.max_reference_duration_s)} | !duration"
        ),
    }
    ffmpeg = find_tool("ffmpeg")
    if ffmpeg:
        options["ffmpeg_location"] = str(Path(ffmpeg).parent)
    if settings.ingest.cookies_file:
        options["cookiefile"] = settings.ingest.cookies_file
    elif settings.ingest.cookies_from_browser:
        options["cookiesfrombrowser"] = (settings.ingest.cookies_from_browser,)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError("the link was filtered (too long) or returned nothing")
            if info.get("_type") == "playlist":
                entries = [e for e in info.get("entries") or [] if e]
                if not entries:
                    raise DownloadError("the link is a playlist with no downloadable videos")
                info = entries[0]
            path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
            if not path.is_file():
                candidates = sorted(target.glob(f"*_{info.get('id')}.*"))
                path = next((c for c in candidates if c.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}), path)
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc)
        login = any(token in message.casefold() for token in ("login", "logged", "cookies", "private", "rate-limit", "401", "403"))
        hint = (
            " Instagram often requires a signed-in session: enable 'Use browser cookies' in Settings ▸ Ingest."
            if login
            else ""
        )
        raise DownloadError(re.sub(r"\x1b\[[0-9;]*m", "", message) + hint, needs_login=login) from exc
    if not path.is_file():
        raise DownloadError("download finished but the media file was not found")
    return DownloadedReel(
        url=url,
        path=path,
        title=(info.get("title") or info.get("description") or info.get("id") or "reel")[:160],
        uploader=info.get("uploader") or info.get("channel"),
        duration_s=info.get("duration"),
        description=(info.get("description") or "")[:2000],
        platform=_platform(url),
        video_id=str(info.get("id")),
        metadata={
            k: info.get(k)
            for k in ("like_count", "view_count", "upload_date", "width", "height", "fps", "webpage_url", "track", "artist")
            if info.get(k) is not None
        },
    )
