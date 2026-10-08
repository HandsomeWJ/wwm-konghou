"""Render notes to a WAV with a Karplus-Strong pluck so a reduction can be
heard on the Mac before it goes anywhere near the game. No external deps."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .arrange import Note

SR = 44100


def pluck(pitch: int, sr: int = SR, dur: float = 1.6, decay: float = 0.996) -> np.ndarray:
    freq = 440.0 * 2 ** ((pitch - 69) / 12)
    period = max(2, int(round(sr / freq)))
    n = int(sr * dur)
    rng = np.random.default_rng(pitch)
    buf = np.zeros(n + 1)  # buf[0] is a zero pad so i-period-1 is never negative
    # excitation: one period of a 1/k harmonic series plus a little noise, so the
    # fundamental dominates (a single-period noise burst has random harmonic weights)
    n_idx = np.arange(period)
    burst = np.zeros(period)
    for k in range(1, min(12, int(sr / 2 / freq)) + 1):
        burst += np.sin(2 * np.pi * k * n_idx / period + rng.uniform(0, 2 * np.pi)) / k
    burst += 0.05 * rng.uniform(-1.0, 1.0, period)
    buf[1 : period + 1] = burst
    i = period + 1
    while i < n + 1:
        j = min(i + period, n + 1)
        buf[i:j] = decay * 0.5 * (buf[i - period : j - period] + buf[i - period - 1 : j - period - 1])
        i = j
    y = buf[1:]
    y *= np.exp(-np.arange(n) / (sr * 0.5))
    peak = float(np.max(np.abs(y))) or 1.0
    return (y / peak).astype(np.float32)


def render(notes: list[Note], path: str | Path, sr: int = SR, tail: float = 2.0) -> float:
    """Write a 16-bit mono WAV; returns its length in seconds."""
    if not notes:
        raise ValueError("nothing to render")
    end = max(n.t for n in notes) + tail
    mix = np.zeros(int(end * sr) + 1, dtype=np.float32)
    cache: dict[int, np.ndarray] = {}
    for n in notes:
        if n.pitch not in cache:
            cache[n.pitch] = pluck(n.pitch, sr)
        sample = cache[n.pitch] * (0.25 + 0.75 * n.vel / 127)
        start = int(n.t * sr)
        stop = min(start + len(sample), len(mix))
        mix[start:stop] += sample[: stop - start]
    peak = float(np.max(np.abs(mix))) or 1.0
    pcm = (mix / peak * 0.85 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return len(pcm) / sr
