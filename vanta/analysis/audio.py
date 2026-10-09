"""Music and audio analysis (manual 8.1, 14): tempo, beat grid, downbeats, sections,
drops, energy, brightness, key, loudness and true peak.

Beat logic follows the manual: beats are classified (soft/strong/downbeat) and
sections annotated (intro/build/drop/outro) so the cut solver can decide, per
learned style, whether a beat becomes a cut, a hold, or a major visual change.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

HOP = 512

_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


@dataclass
class Beat:
    time_s: float
    strength: float  # 0..1 normalised onset strength
    kind: str  # downbeat | strong | soft
    bar: int
    beat_in_bar: int


@dataclass
class Section:
    start_s: float
    end_s: float
    label: str  # intro | build | drop | breakdown | outro | verse
    energy: float


@dataclass
class AudioAnalysis:
    duration_s: float
    sample_rate: int
    bpm: float
    bpm_confidence: float
    beats: list[Beat]
    downbeat_phase: int
    downbeat_confidence: float
    sections: list[Section]
    drops_s: list[float]
    energy_curve: list[float]  # RMS (dB normalised 0..1) at 2 Hz
    brightness_curve: list[float]  # spectral centroid (normalised) at 2 Hz
    onset_curve: list[float]  # onset strength at 2 Hz
    key: str
    key_confidence: float
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    clipping_fraction: float
    mean_energy: float
    mean_brightness: float
    silence_fraction: float
    curve_rate_hz: float = 2.0
    extra: dict = field(default_factory=dict)

    @property
    def beat_times(self) -> np.ndarray:
        return np.asarray([b.time_s for b in self.beats])

    @property
    def downbeat_times(self) -> np.ndarray:
        return np.asarray([b.time_s for b in self.beats if b.kind == "downbeat"])

    def to_dict(self) -> dict:
        payload = asdict(self)
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> AudioAnalysis:
        data = dict(payload)
        data["beats"] = [Beat(**b) for b in data.get("beats", [])]
        data["sections"] = [Section(**s) for s in data.get("sections", [])]
        return cls(**data)


def load_audio(path: str | Path, sr: int = 22050) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != sr:
        import soxr

        mono = soxr.resample(mono, rate, sr)
        rate = sr
    return mono.astype(np.float32), rate


def _resample_curve(values: np.ndarray, src_rate: float, dst_rate: float, duration: float) -> np.ndarray:
    n = max(1, int(duration * dst_rate))
    src_t = np.arange(len(values)) / src_rate
    dst_t = np.arange(n) / dst_rate
    return np.interp(dst_t, src_t, values) if len(values) else np.zeros(n)


def _key(chroma: np.ndarray) -> tuple[str, float]:
    profile = chroma.mean(axis=1)
    if profile.sum() <= 0:
        return "unknown", 0.0
    scores = []
    for shift in range(12):
        rolled = np.roll(profile, -shift)
        scores.append((np.corrcoef(rolled, _MAJOR)[0, 1], f"{_NOTES[shift]} major"))
        scores.append((np.corrcoef(rolled, _MINOR)[0, 1], f"{_NOTES[shift]} minor"))
    scores.sort(reverse=True)
    best, second = scores[0][0], scores[1][0]
    return scores[0][1], float(np.clip((best - second) * 4 + 0.3, 0, 1))


def _loudness(path: str | Path) -> tuple[float | None, float | None, float]:
    import soundfile as sf

    try:
        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception:
        return None, None, 0.0
    clipping = float(np.mean(np.abs(data) >= 0.999))
    lufs = None
    try:
        import pyloudnorm

        if len(data) / rate >= 0.5:
            lufs = float(pyloudnorm.Meter(rate).integrated_loudness(data))
            if not np.isfinite(lufs):
                lufs = None
    except Exception:
        lufs = None
    peak = None
    try:
        import soxr

        oversampled = soxr.resample(data, rate, rate * 4)
        value = float(np.max(np.abs(oversampled)))
        peak = 20 * np.log10(max(value, 1e-9))
    except Exception:
        peak = None
    return lufs, peak, clipping


def analyse_audio(path: str | Path, *, beats_per_bar: int = 4, loudness_path: str | Path | None = None) -> AudioAnalysis:
    """Analyse an audio file (mono 22.05 kHz analysis copy is fine)."""
    import librosa

    y, sr = load_audio(path)
    duration = len(y) / sr
    if duration < 1.0:
        raise ValueError("audio is shorter than one second")
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    frame_rate = sr / HOP
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, hop_length=HOP, tightness=100, units="frames"
    )
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP)
    # refine tempo from median inter-beat interval
    if len(beat_times) > 3:
        ibi = np.diff(beat_times)
        # least-squares period over the whole grid; the median IBI is quantised to hops
        slope = float(np.polyfit(np.arange(len(beat_times)), beat_times, 1)[0])
        tempo = float(60.0 / slope) if slope > 0 else float(60.0 / np.median(ibi))
        bpm_conf = float(np.clip(1 - np.std(ibi) / max(np.median(ibi), 1e-6) * 4, 0, 1))
    else:
        bpm_conf = 0.0
    env_norm = onset_env / max(onset_env.max(), 1e-9)
    strengths = env_norm[np.clip(beat_frames, 0, len(env_norm) - 1)] if len(beat_frames) else np.array([])
    # low-frequency accent for downbeat estimation
    spec = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low = spec[freqs < 150].sum(axis=0)
    low = low / max(low.max(), 1e-9)
    low_at = low[np.clip(beat_frames, 0, len(low) - 1)] if len(beat_frames) else np.array([])
    accent = 0.6 * low_at + 0.4 * strengths if len(beat_frames) else np.array([])
    phase_scores = [
        float(np.mean(accent[p::beats_per_bar])) if len(accent[p::beats_per_bar]) else 0.0
        for p in range(beats_per_bar)
    ]
    phase = int(np.argmax(phase_scores)) if phase_scores else 0
    ordered = sorted(phase_scores, reverse=True)
    down_conf = float(np.clip((ordered[0] - ordered[1]) / max(ordered[0], 1e-6) * 2.5, 0, 1)) if len(ordered) > 1 else 0.0
    strong_cut = float(np.percentile(strengths, 65)) if len(strengths) else 1.0
    beats: list[Beat] = []
    for i, t in enumerate(beat_times):
        in_bar = (i - phase) % beats_per_bar
        bar = (i - phase) // beats_per_bar
        kind = "downbeat" if in_bar == 0 else ("strong" if strengths[i] >= strong_cut else "soft")
        beats.append(Beat(float(t), float(strengths[i]), kind, int(bar), int(in_bar)))
    # curves at 2 Hz
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    rms_db = librosa.amplitude_to_db(rms, ref=np.max)
    energy = np.clip((rms_db + 60) / 60, 0, 1)
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=HOP)[0]
    brightness = np.clip(centroid / (sr / 2), 0, 1)
    energy_2 = _resample_curve(energy, frame_rate, 2.0, duration)
    bright_2 = _resample_curve(brightness, frame_rate, 2.0, duration)
    onset_2 = _resample_curve(env_norm, frame_rate, 2.0, duration)
    sections, drops = _sections(energy, rms, frame_rate, duration, beats, tempo)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP * 4)
    key, key_conf = _key(chroma)
    lufs, peak, clipping = _loudness(loudness_path or path)
    return AudioAnalysis(
        duration_s=float(duration),
        sample_rate=int(sr),
        bpm=round(tempo, 2),
        bpm_confidence=round(bpm_conf, 3),
        beats=beats,
        downbeat_phase=phase,
        downbeat_confidence=round(down_conf, 3),
        sections=sections,
        drops_s=drops,
        energy_curve=[round(float(v), 4) for v in energy_2],
        brightness_curve=[round(float(v), 4) for v in bright_2],
        onset_curve=[round(float(v), 4) for v in onset_2],
        key=key,
        key_confidence=round(key_conf, 3),
        integrated_lufs=None if lufs is None else round(lufs, 2),
        true_peak_dbtp=None if peak is None else round(peak, 2),
        clipping_fraction=round(clipping, 6),
        mean_energy=round(float(energy.mean()), 4),
        mean_brightness=round(float(brightness.mean()), 4),
        silence_fraction=round(float(np.mean(rms_db < -45)), 4),
    )


def _window_db(rms: np.ndarray, frame_rate: float, start_s: float, end_s: float) -> float:
    a = max(0, int(start_s * frame_rate))
    b = min(len(rms), max(a + 1, int(end_s * frame_rate)))
    return float(20 * np.log10(max(float(np.sqrt(np.mean(rms[a:b] ** 2))), 1e-9)))


def _sections(
    energy: np.ndarray,
    rms: np.ndarray,
    frame_rate: float,
    duration: float,
    beats: list[Beat],
    bpm: float,
) -> tuple[list[Section], list[float]]:
    """Energy-based structural segmentation snapped to downbeats.

    A drop is a downbeat where the RMS level of the following bar exceeds the
    previous two bars by >= 3 dB and sits in the louder part of the track.
    """
    win = max(1, int(frame_rate * 1.0))
    smooth = np.convolve(energy, np.ones(win) / win, mode="same")
    times = np.arange(len(smooth)) / frame_rate
    downbeats = [b.time_s for b in beats if b.kind == "downbeat"] or [b.time_s for b in beats]
    drops: list[float] = []
    bar_s = 4 * 60.0 / max(bpm, 1e-3)
    if len(downbeats) >= 3:
        overall_db = _window_db(rms, frame_rate, 0, duration)
        for d in downbeats[1:]:
            if d - 2 * bar_s < -0.01 or d + bar_s > duration + 0.01:
                continue
            before = _window_db(rms, frame_rate, d - 2 * bar_s, d)
            after = _window_db(rms, frame_rate, d, d + bar_s)
            if after - before >= 3.0 and after >= overall_db - 1.0:
                if not drops or d - drops[-1] > 2 * bar_s:
                    drops.append(float(d))
    # boundaries: drops plus large novelty points every >= 4 s
    diff = np.abs(np.diff(smooth, prepend=smooth[0]))
    novelty = np.convolve(diff, np.ones(win) / win, mode="same")
    candidates = sorted(
        set(drops)
        | {
            float(times[i])
            for i in np.argsort(-novelty)[: max(1, int(duration // 6))]
            if novelty[i] > np.percentile(novelty, 90)
        }
    )
    bounds = [0.0]
    for c in candidates:
        snapped = min(downbeats, key=lambda d: abs(d - c)) if downbeats else c
        if snapped - bounds[-1] >= 4.0 and duration - snapped >= 2.0:
            bounds.append(float(snapped))
    bounds.append(float(duration))
    sections: list[Section] = []
    overall = float(smooth.mean())
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        mask = (times >= a) & (times < b)
        level = float(smooth[mask].mean()) if mask.any() else overall
        slope = 0.0
        if mask.sum() > 4:
            slope = float(np.polyfit(times[mask], smooth[mask], 1)[0]) * (b - a)
        if any(abs(a - d) < 0.05 for d in drops):
            label = "drop"
        elif i == 0 and level < overall:
            label = "intro"
        elif i == len(bounds) - 2 and (level < overall or slope < -0.05):
            label = "outro"
        elif slope > 0.08:
            label = "build"
        elif level < overall * 0.8:
            label = "breakdown"
        else:
            label = "verse"
        sections.append(Section(round(a, 3), round(b, 3), label, round(level, 4)))
    return sections, drops


def beat_alignment_error(cut_times_s: list[float], beat_times_s: np.ndarray) -> dict[str, float]:
    """Cut-to-beat error statistics (L3 QC and the +/-40 ms acceptance check)."""
    if not cut_times_s or len(beat_times_s) == 0:
        return {"mean_ms": 0.0, "max_ms": 0.0, "within_40ms": 1.0}
    errors = np.asarray([np.min(np.abs(beat_times_s - t)) for t in cut_times_s]) * 1000
    return {
        "mean_ms": float(errors.mean()),
        "max_ms": float(errors.max()),
        "within_40ms": float(np.mean(errors <= 40.0)),
    }
