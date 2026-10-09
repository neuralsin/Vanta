"""Bring media into the VANTA library: references (links or files), footage, music.

Originals are never moved or modified. Footage and music are referenced in
place (read-only); downloaded references live in the library download folder.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from vanta.analysis.media import MediaError, content_key, media_kind, probe
from vanta.config import Settings, get_settings
from vanta.ingest.downloader import download_reel
from vanta.storage.library import Library

KIND_EXPECTED = {"reference": "video", "footage": "video", "music": "audio", "sfx": "audio"}


class ImportErrorVanta(RuntimeError):
    pass


def import_file(
    library: Library,
    path: str | Path,
    kind: str,
    *,
    title: str | None = None,
    source_url: str | None = None,
    uploader: str | None = None,
    rights: dict | None = None,
    tags: list[str] | None = None,
    profile_id: str | None = None,
) -> dict:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ImportErrorVanta(f"file not found: {path}")
    detected = media_kind(path)
    expected = KIND_EXPECTED[kind]
    if kind in {"music", "sfx"} and detected not in {"audio", "video"}:
        raise ImportErrorVanta(f"{path.name} is not an audio file")
    if kind in {"reference", "footage"} and detected not in {"video", "still"}:
        raise ImportErrorVanta(f"{path.name} is not a video file")
    try:
        info = probe(path)
    except MediaError as exc:
        raise ImportErrorVanta(str(exc)) from exc
    if expected == "video" and not info.has_video:
        raise ImportErrorVanta(f"{path.name} has no video stream")
    if expected == "audio" and not info.has_audio:
        raise ImportErrorVanta(f"{path.name} has no audio stream")
    key = content_key(path)
    width, height = info.display_size
    default_rights = (
        {"source": "user library", "licence": "unknown", "platform_cleared": False}
        if kind in {"music", "sfx"}
        else {"source": "user" if kind == "footage" else "reference (analysis only)"}
    )
    record = library.add_media(
        kind=kind,
        path=str(path),
        content_key=key,
        title=title or path.stem,
        source_url=source_url,
        uploader=uploader,
        duration_s=info.duration_s,
        fps=info.fps or None,
        width=width,
        height=height,
        has_audio=int(info.has_audio),
        info_json=info.to_dict(),
        rights_json=rights or default_rights,
        tags_json=tags or [],
        profile_id=profile_id,
    )
    return record


def import_links(
    library: Library,
    urls: list[str],
    *,
    settings: Settings | None = None,
    profile_id: str | None = None,
    progress: Callable[[str, float, str], None] | None = None,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Download and register reference reels. Returns (records, failures)."""
    settings = settings or get_settings()
    records: list[dict] = []
    failures: list[tuple[str, str]] = []
    for url in urls:
        try:
            reel = download_reel(
                url,
                settings=settings,
                progress=(lambda p, s, u=url: progress(u, p, s)) if progress else None,
            )
            record = import_file(
                library,
                reel.path,
                "reference",
                title=reel.title,
                source_url=url,
                uploader=reel.uploader,
                tags=[reel.platform],
                profile_id=profile_id,
            )
            library.update_media(
                record["id"],
                info_json={**(record.get("info_json") or {}), "platform": reel.platform, "description": reel.description, "remote": reel.metadata},
            )
            records.append(library.get_media(record["id"]))
        except Exception as exc:
            failures.append((url, str(exc)))
    return records, failures


def import_folder(library: Library, folder: str | Path, kind: str) -> tuple[list[dict], list[tuple[str, str]]]:
    folder = Path(folder)
    records, failures = [], []
    wanted = {"video", "still"} if kind in {"reference", "footage"} else {"audio", "video"}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and media_kind(path) in wanted:
            try:
                records.append(import_file(library, path, kind))
            except ImportErrorVanta as exc:
                failures.append((str(path), str(exc)))
    return records, failures
