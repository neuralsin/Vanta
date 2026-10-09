"""Word-level transcription with faster-whisper (manual 12.3, text-based editing).

Resolve 21.1's own MediaPoolItem.GetTranscription is used as a cross-check by
the adapter when available; Whisper word timestamps are authoritative for cuts.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from vanta.config import get_settings
from vanta.models.scheduler import best_device, scheduler

_model = None
_model_key: str | None = None
_lock = threading.Lock()

FILLERS = {"um", "uh", "erm", "ah", "hmm", "like", "basically", "actually", "literally", "you know"}


@dataclass
class Word:
    text: str
    start_s: float
    end_s: float
    probability: float

    @property
    def is_filler(self) -> bool:
        return self.text.strip(" ,.!?").casefold() in FILLERS


@dataclass
class Segment:
    text: str
    start_s: float
    end_s: float
    words: list[Word] = field(default_factory=list)
    no_speech_prob: float = 0.0


@dataclass
class Transcript:
    language: str | None
    segments: list[Segment]
    model: str
    duration_s: float

    @property
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    @property
    def is_empty(self) -> bool:
        return not self.words

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> Transcript:
        segments = [
            Segment(
                text=s["text"],
                start_s=s["start_s"],
                end_s=s["end_s"],
                words=[Word(**w) for w in s.get("words", [])],
                no_speech_prob=s.get("no_speech_prob", 0.0),
            )
            for s in payload.get("segments", [])
        ]
        return cls(payload.get("language"), segments, payload.get("model", "?"), payload.get("duration_s", 0.0))


def _unload() -> None:
    global _model, _model_key
    with _lock:
        _model = None
        _model_key = None


def transcribe(audio_path: str | Path, *, language: str | None = None) -> Transcript:
    """Transcribe with word timestamps; VAD filters silence so empty speech yields no words."""
    global _model, _model_key
    settings = get_settings()
    settings.apply_model_cache_env()
    name = settings.models.whisper_model
    from faster_whisper import WhisperModel

    device = best_device()
    compute = settings.models.whisper_compute_type if device == "cuda" else "int8"
    key = f"whisper:{name}"
    with scheduler.lease(key):
        with _lock:
            if _model is None or _model_key != key:
                # resolves through the shared HF cache (HF_HOME on the data volume)
                _model = WhisperModel(name, device=device, compute_type=compute)
                _model_key = key
                scheduler.register_unloader(key, _unload)
            model = _model
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        segments: list[Segment] = []
        for seg in segments_iter:
            words = [
                Word(w.word.strip(), float(w.start), float(w.end), float(w.probability))
                for w in (seg.words or [])
                if w.word.strip()
            ]
            segments.append(
                Segment(seg.text.strip(), float(seg.start), float(seg.end), words, float(seg.no_speech_prob))
            )
    return Transcript(info.language, segments, name, float(info.duration))
