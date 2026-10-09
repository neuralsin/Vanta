"""Provider abstraction for the creative-director VLM (manual 5, 10).

The director returns schema-constrained JSON (plans) or, for critique, may use
thinking mode. Providers are swappable by configuration; no code path depends
on a specific model. Every call is timed and its model/prompt version recorded.
"""

from __future__ import annotations

import base64
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from pydantic import BaseModel, ValidationError

from vanta.config import Settings, get_settings
from vanta.models.scheduler import scheduler


class ModelUnavailable(RuntimeError):
    """The configured provider/model cannot be reached (RETRYABLE or fallback)."""


class ModelOutputInvalid(RuntimeError):
    """The model answered but its output failed schema validation."""


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    seconds: float
    prompt_version: str
    raw: str = ""
    thinking: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


ImageLike = np.ndarray | str | Path


def encode_image(image: ImageLike, long_edge: int = 768) -> str:
    if isinstance(image, (str, Path)):
        rgb = cv2.cvtColor(cv2.imread(str(image), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    else:
        rgb = image
    h, w = rgb.shape[:2]
    if max(h, w) > long_edge:
        scale = long_edge / max(h, w)
        rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise ValueError("could not encode image for the model")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def contact_sheet(images: list[np.ndarray], labels: list[str] | None = None, cols: int = 4, cell: int = 256) -> np.ndarray:
    """Tile frames into one labelled image so the VLM sees many shots in one call."""
    if not images:
        raise ValueError("no images for contact sheet")
    rows = (len(images) + cols - 1) // cols
    tiles = []
    for idx, img in enumerate(images):
        h, w = img.shape[:2]
        scale = cell / max(h, w)
        small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        tile = np.full((cell, cell, 3), 18, np.uint8)
        y0, x0 = (cell - small.shape[0]) // 2, (cell - small.shape[1]) // 2
        tile[y0 : y0 + small.shape[0], x0 : x0 + small.shape[1]] = small
        label = labels[idx] if labels else str(idx)
        cv2.rectangle(tile, (0, 0), (min(cell, 12 + 11 * len(label)), 22), (0, 0, 0), -1)
        cv2.putText(tile, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 220, 80), 1, cv2.LINE_AA)
        tiles.append(tile)
    while len(tiles) < rows * cols:
        tiles.append(np.full((cell, cell, 3), 18, np.uint8))
    grid = [np.concatenate(tiles[r * cols : (r + 1) * cols], axis=1) for r in range(rows)]
    return np.concatenate(grid, axis=0)


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """Return (usable, reason)."""

    @abstractmethod
    def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        images: list[ImageLike] | None = None,
        prompt_version: str,
        think: bool = False,
        temperature: float = 0.2,
        seed: int = 7,
        num_ctx: int = 8192,
    ) -> LLMResult:
        """Return validated JSON for ``schema``."""


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, settings: Settings | None = None, model: str | None = None):
        self.settings = settings or get_settings()
        self.host = self.settings.models.ollama_host.rstrip("/")
        self.model = model or self.settings.models.director_model

    def available(self) -> tuple[bool, str]:
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=3)
            response.raise_for_status()
        except Exception as exc:
            return False, f"Ollama not reachable at {self.host} ({type(exc).__name__})"
        names = [m.get("name") for m in response.json().get("models", [])]
        if self.model in names or f"{self.model}:latest" in names:
            return True, f"{self.model} ready"
        fallback = self.settings.models.director_fallback_model
        if fallback in names:
            self.model = fallback
            return True, f"{fallback} ready (fallback)"
        return False, f"{self.model} is not pulled in Ollama"

    def unload(self) -> None:
        try:
            httpx.post(f"{self.host}/api/generate", json={"model": self.model, "keep_alive": 0}, timeout=10)
        except Exception:
            pass

    def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        images: list[ImageLike] | None = None,
        prompt_version: str,
        think: bool = False,
        temperature: float = 0.2,
        seed: int = 7,
        num_ctx: int = 8192,
    ) -> LLMResult:
        ok, reason = self.available()
        if not ok:
            raise ModelUnavailable(reason)
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            message["images"] = [encode_image(img) for img in images]
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, message],
            "stream": False,
            "format": schema.model_json_schema(),
            "think": think,
            "keep_alive": self.settings.models.keep_alive,
            "options": {"temperature": temperature, "seed": seed, "num_ctx": num_ctx},
        }
        scheduler.register_unloader(f"ollama:{self.model}", self.unload)
        started = time.monotonic()
        last_error: Exception | None = None
        with scheduler.lease(f"ollama:{self.model}", keep_resident=False):
            for attempt in range(3):  # schema-repair retries (and one think-less retry)
                try:
                    response = httpx.post(
                        f"{self.host}/api/chat", json=body, timeout=self.settings.models.request_timeout_s
                    )
                    response.raise_for_status()
                except httpx.TimeoutException as exc:
                    raise ModelUnavailable(f"{self.model} timed out after {self.settings.models.request_timeout_s:g}s") from exc
                except httpx.HTTPStatusError as exc:
                    # a non-thinking checkpoint rejects "think": answer without it rather than lose the model
                    if body["think"] and exc.response.status_code == 400 and "think" in exc.response.text.casefold():
                        body["think"] = False
                        continue
                    raise ModelUnavailable(f"Ollama request failed: {exc}") from exc
                except httpx.HTTPError as exc:
                    raise ModelUnavailable(f"Ollama request failed: {exc}") from exc
                payload = response.json()
                content = payload.get("message", {}).get("content", "")
                try:
                    data = schema.model_validate_json(content).model_dump(mode="json")
                except ValidationError as exc:
                    last_error = exc
                    body["options"]["seed"] = seed + attempt + 1
                    continue
                return LLMResult(
                    data=data,
                    model=self.model,
                    seconds=round(time.monotonic() - started, 2),
                    prompt_version=prompt_version,
                    raw=content,
                    thinking=payload.get("message", {}).get("thinking", "") or "",
                    usage={k: payload.get(k) for k in ("eval_count", "prompt_eval_count", "eval_duration")},
                )
        raise ModelOutputInvalid(f"{self.model} output failed schema validation: {last_error}")


class DisabledProvider(LLMProvider):
    name = "disabled"

    def available(self) -> tuple[bool, str]:
        return False, "model provider disabled in settings"

    def generate_json(self, **_: Any) -> LLMResult:
        raise ModelUnavailable("model provider disabled in settings")


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if settings.models.provider == "ollama":
        return OllamaProvider(settings)
    return DisabledProvider()


def dumps_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
