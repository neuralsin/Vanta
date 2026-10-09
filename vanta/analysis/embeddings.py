"""Visual, text-image and audio embeddings behind one interface (manual 5 model roles).

Providers load lazily inside the scheduler's GPU lease and are unloaded after
each batch. When a model is not installed a deterministic ``tiny`` embedding
(colour layout + edge orientation histogram) keeps retrieval working, and the
provenance records which model produced every vector.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

import cv2
import numpy as np

from vanta.config import get_settings
from vanta.models.scheduler import best_device, scheduler

_cache: dict[str, object] = {}
_lock = threading.Lock()

TINY_MODEL = "vanta-tiny-v1"


def tiny_embedding(rgb: np.ndarray) -> np.ndarray:
    """128-d deterministic descriptor: 4x4 Lab layout (48) + hue hist (32) + HOG-ish (48)."""
    small = cv2.resize(rgb, (64, 64), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32) / 255.0
    layout = cv2.resize(lab, (4, 4), interpolation=cv2.INTER_AREA).ravel()
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    hue = np.histogram(hsv[..., 0], bins=32, range=(0, 180), weights=hsv[..., 1].astype(np.float32))[0]
    hue = hue / max(hue.sum(), 1e-6)
    grey = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(grey, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(grey, cv2.CV_32F, 0, 1)
    mag = np.hypot(gx, gy)
    ang = (np.arctan2(gy, gx) % np.pi) / np.pi
    hog = []
    for qy in range(2):
        for qx in range(2):
            sl = (slice(qy * 32, qy * 32 + 32), slice(qx * 32, qx * 32 + 32))
            h = np.histogram(ang[sl], bins=12, range=(0, 1), weights=mag[sl])[0]
            hog.append(h / max(h.sum(), 1e-6))
    vec = np.concatenate([layout, hue, np.concatenate(hog)]).astype(np.float32)
    return vec / max(np.linalg.norm(vec), 1e-9)


def _hf_available(repo: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(repo, "config.json"), str)
    except Exception:
        return False


def _unload(key: str) -> None:
    with _lock:
        _cache.pop(key, None)


def _load(key: str, loader):
    with _lock:
        if key not in _cache:
            _cache[key] = loader()
            scheduler.register_unloader(key, lambda: _unload(key))
        return _cache[key]


def _to_pil(frames: Sequence[np.ndarray]):
    from PIL import Image

    return [Image.fromarray(np.ascontiguousarray(frame)) for frame in frames]


def image_embeddings(frames: Sequence[np.ndarray], *, model: str | None = None) -> tuple[str, np.ndarray]:
    """DINOv2 CLS embeddings (visual memory). Returns (model_name, N x D, L2-normalised)."""
    repo = model or get_settings().models.embedding_model
    if not frames:
        return repo, np.zeros((0, 0), dtype=np.float32)
    if not _hf_available(repo):
        return TINY_MODEL, np.stack([tiny_embedding(f) for f in frames])
    import torch
    from transformers import AutoImageProcessor, AutoModel

    device = best_device()

    def loader():
        processor = AutoImageProcessor.from_pretrained(repo)
        net = AutoModel.from_pretrained(repo, dtype=torch.float16 if device == "cuda" else torch.float32)
        return processor, net.to(device).eval()

    out: list[np.ndarray] = []
    with scheduler.lease(repo):
        processor, net = _load(repo, loader)
        with torch.inference_mode():
            for start in range(0, len(frames), 16):
                batch = processor(images=_to_pil(frames[start : start + 16]), return_tensors="pt").to(device)
                batch["pixel_values"] = batch["pixel_values"].to(net.dtype)
                hidden = net(**batch).last_hidden_state
                cls = torch.cat([hidden[:, 0], hidden[:, 1:].mean(dim=1)], dim=-1)
                out.append(torch.nn.functional.normalize(cls.float(), dim=-1).cpu().numpy())
    return repo, np.concatenate(out)


def _siglip():
    repo = get_settings().models.text_image_model
    import torch
    from transformers import AutoModel, AutoProcessor

    device = best_device()

    def loader():
        processor = AutoProcessor.from_pretrained(repo)
        net = AutoModel.from_pretrained(repo, dtype=torch.float16 if device == "cuda" else torch.float32)
        return processor, net.to(device).eval()

    return repo, device, loader


def semantic_image_embeddings(frames: Sequence[np.ndarray]) -> tuple[str, np.ndarray] | None:
    """SigLIP image embeddings for text-to-frame matching (None if not installed)."""
    repo = get_settings().models.text_image_model
    if not frames or not _hf_available(repo):
        return None
    import torch

    repo, device, loader = _siglip()
    out = []
    with scheduler.lease(repo):
        processor, net = _load(repo, loader)
        with torch.inference_mode():
            for start in range(0, len(frames), 16):
                batch = processor(images=_to_pil(frames[start : start + 16]), return_tensors="pt").to(device)
                feats = net.get_image_features(pixel_values=batch["pixel_values"].to(net.dtype))
                feats = getattr(feats, "pooler_output", feats)
                out.append(torch.nn.functional.normalize(feats.float(), dim=-1).cpu().numpy())
    return repo, np.concatenate(out)


def semantic_text_embeddings(texts: Sequence[str]) -> tuple[str, np.ndarray] | None:
    repo = get_settings().models.text_image_model
    if not texts or not _hf_available(repo):
        return None
    import torch

    repo, device, loader = _siglip()
    with scheduler.lease(repo):
        processor, net = _load(repo, loader)
        with torch.inference_mode():
            batch = processor(text=list(texts), padding="max_length", return_tensors="pt").to(device)
            feats = net.get_text_features(**batch)
            feats = getattr(feats, "pooler_output", feats)
            vec = torch.nn.functional.normalize(feats.float(), dim=-1).cpu().numpy()
    return repo, vec


def audio_embeddings(waves: Sequence[np.ndarray], sample_rate: int) -> tuple[str, np.ndarray] | None:
    """CLAP audio embeddings (48 kHz internally). None when CLAP is not installed."""
    repo = get_settings().models.audio_text_model
    if not waves or not _hf_available(repo):
        return None
    import torch
    from transformers import ClapModel, ClapProcessor

    device = best_device()

    def loader():
        return ClapProcessor.from_pretrained(repo), ClapModel.from_pretrained(repo).to(device).eval()

    resampled = []
    for wave in waves:
        if sample_rate != 48000:
            import soxr

            wave = soxr.resample(wave.astype(np.float32), sample_rate, 48000)
        resampled.append(wave.astype(np.float32))
    with scheduler.lease(repo):
        processor, net = _load(repo, loader)
        with torch.inference_mode():
            batch = processor(audio=resampled, sampling_rate=48000, return_tensors="pt").to(device)
            feats = net.get_audio_features(**batch)
            feats = getattr(feats, "pooler_output", feats)
            vec = torch.nn.functional.normalize(feats.float(), dim=-1).cpu().numpy()
    return repo, vec


def audio_text_embeddings(texts: Sequence[str]) -> tuple[str, np.ndarray] | None:
    repo = get_settings().models.audio_text_model
    if not texts or not _hf_available(repo):
        return None
    import torch
    from transformers import ClapModel, ClapProcessor

    device = best_device()

    def loader():
        return ClapProcessor.from_pretrained(repo), ClapModel.from_pretrained(repo).to(device).eval()

    with scheduler.lease(repo):
        processor, net = _load(repo, loader)
        with torch.inference_mode():
            batch = processor(text=list(texts), padding=True, return_tensors="pt").to(device)
            feats = net.get_text_features(**batch)
            feats = getattr(feats, "pooler_output", feats)
            vec = torch.nn.functional.normalize(feats.float(), dim=-1).cpu().numpy()
    return repo, vec
