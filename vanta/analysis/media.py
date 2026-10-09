"""FFmpeg/ffprobe wrappers: probing, analysis proxies, audio extraction, frame grabs.

Originals are only ever read. Every derived file lands under the analysis cache,
keyed by a content hash so repeated analysis is free.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from vanta.config import find_tool

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mxf", ".webm", ".mts", ".braw"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".aac", ".m4a", ".flac", ".ogg", ".aif", ".aiff", ".opus"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


class MediaError(RuntimeError):
    """Raised when a media tool is missing or a file cannot be decoded."""


def _tool(name: str) -> str:
    path = find_tool(name)
    if not path:
        raise MediaError(
            f"{name} was not found. Install FFmpeg (winget install Gyan.FFmpeg) and restart VANTA."
        )
    return path


def run_tool(args: list[str], *, timeout: float = 600.0) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{Path(args[0]).stem} timed out after {timeout:g}s") from exc
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise MediaError(f"{Path(args[0]).stem} failed: " + " | ".join(tail))
    return result


def content_key(path: str | Path) -> str:
    """Fast, stable content hash: size + first/middle/last 4 MiB (sha256)."""
    path = Path(path)
    size = path.stat().st_size
    chunk = 4 * 1024 * 1024
    digest = hashlib.sha256(str(size).encode())
    with path.open("rb") as stream:
        if size <= 3 * chunk:
            digest.update(stream.read())
        else:
            for offset in (0, size // 2 - chunk // 2, size - chunk):
                stream.seek(offset)
                digest.update(stream.read(chunk))
    return digest.hexdigest()[:32]


def media_kind(path: str | Path) -> str:
    suffix = Path(path).suffix.casefold()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in IMAGE_EXTENSIONS:
        return "still"
    return "unknown"


@dataclass
class MediaInfo:
    path: str
    duration_s: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    rotation: int = 0
    fps_num: int = 0
    fps_den: int = 1
    nb_frames: int = 0
    vcodec: str | None = None
    pix_fmt: str | None = None
    color_range: str | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    bit_depth: int | None = None
    audio_codec: str | None = None
    audio_sample_rate: int = 0
    audio_channels: int = 0
    format_name: str | None = None
    bit_rate: int = 0
    variable_frame_rate: bool = False

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den if self.fps_num else 0.0

    @property
    def fps_fraction(self) -> Fraction:
        return Fraction(self.fps_num, self.fps_den) if self.fps_num else Fraction(0)

    @property
    def display_size(self) -> tuple[int, int]:
        if self.rotation % 180 == 90:
            return self.height, self.width
        return self.width, self.height

    @property
    def frame_count(self) -> int:
        if self.nb_frames:
            return self.nb_frames
        return int(round(self.duration_s * self.fps)) if self.fps else 0

    @property
    def is_log(self) -> bool:
        transfer = (self.color_transfer or "").casefold()
        return any(token in transfer for token in ("log", "arib-std-b67", "smpte2084"))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fps"] = self.fps
        payload["display_size"] = list(self.display_size)
        payload["frame_count"] = self.frame_count
        return payload


def _rate(value: str | None) -> tuple[int, int]:
    if not value or value in {"0/0", "N/A"}:
        return 0, 1
    try:
        fraction = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return 0, 1
    return fraction.numerator, fraction.denominator


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"media file not found: {path}")
    result = run_tool(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=60,
    )
    data = json.loads(result.stdout.decode("utf-8", "replace"))
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or 0.0)
    info = MediaInfo(
        path=str(path.resolve()),
        duration_s=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        format_name=fmt.get("format_name"),
        bit_rate=int(fmt.get("bit_rate") or 0),
    )
    if video:
        info.width = int(video.get("width") or 0)
        info.height = int(video.get("height") or 0)
        info.vcodec = video.get("codec_name")
        info.pix_fmt = video.get("pix_fmt")
        info.color_range = video.get("color_range")
        info.color_space = video.get("color_space")
        info.color_transfer = video.get("color_transfer")
        info.color_primaries = video.get("color_primaries")
        raw_depth = video.get("bits_per_raw_sample")
        info.bit_depth = int(raw_depth) if str(raw_depth or "").isdigit() else None
        avg = _rate(video.get("avg_frame_rate"))
        real = _rate(video.get("r_frame_rate"))
        info.fps_num, info.fps_den = real if real[0] else avg
        if avg[0] and real[0]:
            info.variable_frame_rate = abs(avg[0] / avg[1] - real[0] / real[1]) > 0.05
        info.nb_frames = int(video.get("nb_frames") or 0) if str(
            video.get("nb_frames") or ""
        ).isdigit() else 0
        if not duration:
            info.duration_s = float(video.get("duration") or 0.0)
        rotation = 0
        for side in video.get("side_data_list") or []:
            if "rotation" in side:
                rotation = int(float(side["rotation"]))
        rotation = rotation or int(float((video.get("tags") or {}).get("rotate", 0) or 0))
        info.rotation = rotation % 360
    if audio:
        info.audio_codec = audio.get("codec_name")
        info.audio_sample_rate = int(audio.get("sample_rate") or 0)
        info.audio_channels = int(audio.get("channels") or 0)
        if not info.duration_s:
            info.duration_s = float(audio.get("duration") or 0.0)
    if not info.has_video and not info.has_audio:
        raise MediaError(f"no decodable audio or video stream in {path.name}")
    return info


def effective_matrix(info: MediaInfo | dict) -> str:
    """The Y'CbCr matrix a file really uses: its tag, else the HD/SD convention Resolve applies."""
    get = info.get if isinstance(info, dict) else lambda k, d=None: getattr(info, k, d)
    space = str(get("color_space") or "").casefold()
    if space == "bt709":
        return "bt709"
    if space in {"smpte170m", "bt470bg", "bt601", "fcc", "smpte240m"}:
        return "bt601"
    if space.startswith("bt2020"):
        return "bt2020"
    w, h = int(get("width") or 0), int(get("height") or 0)
    return "bt709" if max(w, h) >= 1280 or min(w, h) >= 720 else "bt601"


def is_full_range(info: MediaInfo | dict) -> bool:
    get = info.get if isinstance(info, dict) else lambda k, d=None: getattr(info, k, d)
    return str(get("color_range") or "").casefold() in {"pc", "jpeg", "full"} or str(get("pix_fmt") or "").startswith("yuvj")


_MATRIX_CACHE: dict[tuple[str, float], tuple[str, bool]] = {}


def file_matrix(path: str | Path) -> tuple[str, bool]:
    """(matrix, full_range) of a video file, cached by path + mtime."""
    p = Path(path)
    key = (str(p), p.stat().st_mtime if p.exists() else 0.0)
    if key not in _MATRIX_CACHE:
        try:
            info = probe(p)
            _MATRIX_CACHE[key] = (effective_matrix(info), is_full_range(info))
        except Exception:
            _MATRIX_CACHE[key] = ("bt709", False)
    return _MATRIX_CACHE[key]


def read_rgb_fixed(rgb: np.ndarray, path: str | Path) -> np.ndarray:
    """An OpenCV-decoded RGB frame corrected to the file's real Y'CbCr matrix."""
    from vanta.core.colormath import correct_decoded

    matrix, _ = file_matrix(path)
    return correct_decoded(rgb, matrix)


# tags for every video VANTA encodes from display-referred RGB (Rec.709, limited range)
REC709_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def _scale_name(matrix: str) -> str:
    return {"bt601": "bt601", "bt709": "bt709", "bt2020": "bt2020"}[matrix]


def make_proxy(
    path: str | Path,
    output: str | Path,
    *,
    long_edge: int = 720,
    fps: float | None = None,
    timeout: float = 1800.0,
    matrix: str = "bt709",
    full_range: bool = False,
) -> Path:
    """Analysis proxy: constant fps, long edge <= ``long_edge``, 8-bit, no audio.

    Aspect ratio and orientation are preserved (autorotate). The proxy is
    re-encoded to the BT.601 matrix (tagged) on purpose: OpenCV decodes every
    stream as BT.601, so every frame VANTA analyses then carries the source's
    true R'G'B' values. The source matrix/range are converted through RGB
    explicitly (``matrix``/``full_range`` come from :func:`effective_matrix`).
    """
    output = Path(output)
    if output.is_file() and output.stat().st_size > 0:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    scale = (
        f"scale='if(gt(iw,ih),min({long_edge},iw),-2)':'if(gt(iw,ih),-2,min({long_edge},ih))'"
    )
    in_range = "pc" if full_range else "tv"
    filters = [
        f"{scale}:in_color_matrix={_scale_name(matrix)}:in_range={in_range}",
        "format=gbrp16le",
        "scale=out_color_matrix=bt601:out_range=tv",
        "format=yuv420p",
    ]
    if fps:
        filters.append(f"fps={fps:g}")
    tmp = output.with_suffix(".tmp.mp4")
    run_tool(
        [
            _tool("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            ",".join(filters),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "smpte170m",
            "-color_range",
            "tv",
            "-an",
            str(tmp),
        ],
        timeout=timeout,
    )
    os.replace(tmp, output)
    return output


def extract_audio(
    path: str | Path, output: str | Path, *, sample_rate: int = 22050, mono: bool = True
) -> Path:
    output = Path(output)
    if output.is_file() and output.stat().st_size > 0:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".tmp.wav")
    args = [_tool("ffmpeg"), "-y", "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0"]
    if mono:
        args += ["-ac", "1"]
    args += ["-ar", str(sample_rate), "-c:a", "pcm_s16le", str(tmp)]
    run_tool(args, timeout=900)
    os.replace(tmp, output)
    return output


def grab_frame(path: str | Path, time_s: float, *, long_edge: int = 0) -> np.ndarray:
    """Decode one frame (RGB uint8) at ``time_s`` with accurate seeking."""
    args = [_tool("ffmpeg"), "-v", "error", "-ss", f"{max(0.0, time_s):.4f}", "-i", str(path)]
    matrix, full = file_matrix(path)
    vf = [f"scale=in_color_matrix={_scale_name(matrix)}:in_range={'pc' if full else 'tv'}", "format=rgb24"]
    if long_edge:
        vf.append(
            f"scale='if(gt(iw,ih),min({long_edge},iw),-2)':'if(gt(iw,ih),-2,min({long_edge},ih))'"
        )
    if vf:
        args += ["-vf", ",".join(vf)]
    args += ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    result = run_tool(args, timeout=60)
    import cv2

    buffer = np.frombuffer(result.stdout, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise MediaError(f"could not decode a frame at {time_s:.3f}s from {Path(path).name}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


class FrameReader:
    """Sequential/random frame access on an analysis proxy via OpenCV."""

    def __init__(self, path: str | Path):
        import cv2

        self._cv2 = cv2
        self.path = str(path)
        self.capture = cv2.VideoCapture(self.path)
        if not self.capture.isOpened():
            raise MediaError(f"OpenCV could not open {path}")
        self.fps = float(self.capture.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        self.frame_count = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    def read_at(self, time_s: float) -> np.ndarray | None:
        index = int(round(time_s * self.fps))
        return self.read_index(index)

    def read_index(self, index: int) -> np.ndarray | None:
        cv2 = self._cv2
        index = max(0, min(index, max(0, self.frame_count - 1)))
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        if not ok or frame is None:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def iterate(self, step: int = 1, max_long_edge: int = 0):
        """Yield ``(index, rgb_frame)`` sequentially, keeping every ``step``-th frame."""
        cv2 = self._cv2
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        index = 0
        while True:
            ok, frame = self.capture.read()
            if not ok or frame is None:
                break
            if index % step == 0:
                if max_long_edge and max(frame.shape[:2]) > max_long_edge:
                    scale = max_long_edge / max(frame.shape[:2])
                    frame = cv2.resize(
                        frame,
                        (int(frame.shape[1] * scale), int(frame.shape[0] * scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                yield index, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            index += 1

    def close(self) -> None:
        self.capture.release()

    def __enter__(self) -> FrameReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def save_jpeg(image_rgb: np.ndarray, path: str | Path, *, quality: int = 88, long_edge: int = 0) -> Path:
    import cv2

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = image_rgb
    if long_edge and max(image.shape[:2]) > long_edge:
        scale = long_edge / max(image.shape[:2])
        image = cv2.resize(
            image, (int(image.shape[1] * scale), int(image.shape[0] * scale)), cv2.INTER_AREA
        )
    ok, encoded = cv2.imencode(
        ".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    if not ok:
        raise MediaError(f"could not encode {path.name}")
    path.write_bytes(encoded.tobytes())
    return path
