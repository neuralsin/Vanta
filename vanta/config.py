"""Application settings: paths, external tools, model choices and hardware budget.

Settings are layered: built-in defaults < ``data/settings.json`` < ``VANTA_*``
environment variables. The desktop app edits the JSON layer; nothing here
downloads or installs anything.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _default_data_dir() -> Path:
    return Path(os.environ.get("VANTA_DATA_DIR", ROOT / "data")).resolve()


@dataclass
class ModelSettings:
    """Model roles from manual section 5. Every entry is swappable by configuration."""

    provider: str = "ollama"  # ollama | disabled
    ollama_host: str = "http://127.0.0.1:11434"
    director_model: str = "qwen3-vl:8b"  # visual reasoner / creative director
    director_fallback_model: str = "qwen3-vl:4b"
    critic_think: bool = True
    director_think: bool = True  # reason before planning/grading intent (quality over speed; retried without if unsupported)
    request_timeout_s: float = 600.0
    keep_alive: str = "0"  # unload after each stage (manual: keep_alive 0)
    whisper_model: str = "large-v3-turbo"
    whisper_compute_type: str = "int8_float16"
    embedding_model: str = "facebook/dinov2-small"
    text_image_model: str = "google/siglip-base-patch16-224"
    audio_text_model: str = "laion/clap-htsat-unfused"
    segmentation_model: str = "facebook/sam2.1-hiera-small"
    gated_segmentation_model: str = "facebook/sam3"
    ocr_engine: str = "rapidocr"


@dataclass
class HardwareBudget:
    """Guaranteed operating budget (user-stated), independent of momentary free memory."""

    ram_gb: float = 12.0
    vram_gb: float = 6.0
    disk_gb: float = 190.0
    tier: str = "low_vram"  # low_vram | medium_vram | high_vram | multi_gpu
    one_heavy_model: bool = True


@dataclass
class EditDefaults:
    platform: str = "instagram_reel"
    aspect: str = "9:16"
    fps: float = 30.0
    resolution: tuple[int, int] = (1080, 1920)
    duration_s: float = 38.0  # target reel length: 30-45 s
    beat_snap_ms: float = 40.0
    watch_delay_s: float = 0.7
    max_repair_iterations: int = 3
    loudness_target_lufs: float = -14.0
    true_peak_dbtp: float = -1.0


@dataclass
class IngestSettings:
    """Reference download behaviour (manual 21: keep derived features, not others' media)."""

    cookies_from_browser: str | None = None  # chrome | edge | firefox | brave | None
    cookies_file: str | None = None
    keep_reference_media: bool = False
    max_reference_duration_s: float = 240.0
    copy_footage_into_library: bool = False  # footage is referenced in place, read-only


@dataclass
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    resolve_root: Path = Path("C:/Program Files/Blackmagic Design/DaVinci Resolve")
    resolve_scripting_dir: Path = Path(
        "C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting"
    )
    hud_port: int = 8765
    models: ModelSettings = field(default_factory=ModelSettings)
    hardware: HardwareBudget = field(default_factory=HardwareBudget)
    edit: EditDefaults = field(default_factory=EditDefaults)
    ingest: IngestSettings = field(default_factory=IngestSettings)
    allow_network_models: bool = False
    hf_token_env: str = "HF_TOKEN"

    # ---- derived paths -------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "vanta.sqlite3"

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def references_dir(self) -> Path:
        return self.library_dir / "references"

    @property
    def footage_dir(self) -> Path:
        return self.library_dir / "footage"

    @property
    def music_dir(self) -> Path:
        return self.library_dir / "music"

    @property
    def analysis_dir(self) -> Path:
        return self.data_dir / "analysis"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def kb_dir(self) -> Path:
        return ROOT / "kb"

    @property
    def templates_dir(self) -> Path:
        return ROOT / "vanta" / "resolve" / "templates"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.library_dir,
            self.references_dir,
            self.footage_dir,
            self.music_dir,
            self.analysis_dir,
            self.runs_dir,
            self.models_dir,
            self.cache_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def apply_model_cache_env(self) -> None:
        """Keep every model cache on the data volume, never the small system drive."""
        hf = self.models_dir / "huggingface"
        os.environ.setdefault("HF_HOME", str(hf))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("TORCH_HOME", str(self.models_dir / "torch"))
        os.environ.setdefault("OLLAMA_NUM_PARALLEL", "1")

    # ---- persistence ---------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("data_dir", "resolve_root", "resolve_scripting_dir"):
            payload[key] = str(payload[key])
        return payload

    def save(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")
        os.replace(tmp, self.settings_path)

    @classmethod
    def load(cls, data_dir: Path | None = None) -> Settings:
        settings = cls(data_dir=data_dir or _default_data_dir())
        if settings.settings_path.is_file():
            try:
                stored = json.loads(settings.settings_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                stored = {}
            _merge(settings, stored)
        _apply_env(settings)
        return settings


def _merge(target: Any, values: dict[str, Any]) -> None:
    for item in fields(target):
        if item.name not in values:
            continue
        current = getattr(target, item.name)
        value = values[item.name]
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _merge(current, value)
        elif isinstance(current, Path):
            setattr(target, item.name, Path(value))
        elif isinstance(current, tuple) and isinstance(value, list):
            setattr(target, item.name, tuple(value))
        else:
            setattr(target, item.name, value)


def _apply_env(settings: Settings) -> None:
    env = os.environ
    if "VANTA_RESOLVE_ROOT" in env:
        settings.resolve_root = Path(env["VANTA_RESOLVE_ROOT"])
    if "VANTA_MODEL_PROVIDER" in env:
        settings.models.provider = env["VANTA_MODEL_PROVIDER"]
    if "VANTA_DIRECTOR_MODEL" in env:
        settings.models.director_model = env["VANTA_DIRECTOR_MODEL"]
    if "OLLAMA_HOST" in env:
        host = env["OLLAMA_HOST"]
        settings.models.ollama_host = host if host.startswith("http") else f"http://{host}"


_settings: Settings | None = None
_settings_lock = threading.Lock()


def get_settings() -> Settings:
    global _settings
    with _settings_lock:
        if _settings is None:
            _settings = Settings.load()
            _settings.ensure_dirs()
            _settings.apply_model_cache_env()
        return _settings


def set_settings(settings: Settings) -> None:
    """Replace the process-wide settings (tests and the settings screen)."""
    global _settings
    with _settings_lock:
        _settings = settings
        settings.ensure_dirs()


def find_tool(name: str) -> str | None:
    """Locate an external executable without assuming it is on PATH."""
    found = shutil.which(name)
    if found:
        return found
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe")
        candidates.append(Path(local) / "Programs" / "Ollama" / f"{name}.exe")
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            candidates.extend(packages.glob(f"*/*/bin/{name}.exe"))
    candidates.append(ROOT / "vendor" / "bin" / f"{name}.exe")
    for candidate in candidates:
        if Path(candidate).is_file():
            return str(candidate)
    return None
