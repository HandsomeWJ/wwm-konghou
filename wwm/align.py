"""Align two note sequences of the same piece from different sources (an OMR read of
the score and a transcription of a recording) with dynamic time warping over onset
chords. Tempo, rubato and silence are irrelevant; only pitch content drives it."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .arrange import Note, cluster_onsets


@dataclass
class Chord:
    t: float
    pitches: frozenset[int]
    notes: list[Note]


def to_chords(notes: list[Note], window: float = 0.03) -> list[Chord]:
    return [Chord(o.t, frozenset(n.pitch for n in o.notes), o.notes) for o in cluster_onsets(notes, window)]


def jaccard(a: frozenset[int], b: frozenset[int]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def dtw_path(ref: list[Chord], other: list[Chord], band: int = 120) -> list[tuple[int, int]]:
    """Monotonic alignment path of (ref index, other index) pairs. Cost is 1 - Jaccard
    of the pitch sets; skipping a chord on either side costs 0.5."""
    n, m = len(ref), len(other)
    if n == 0 or m == 0:
        return []
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    slope = m / n
    for i in range(1, n + 1):
        j_lo = max(1, int(i * slope) - band)
        j_hi = min(m, int(i * slope) + band)
        a = ref[i - 1].pitches
        for j in range(j_lo, j_hi + 1):
            c = 1.0 - jaccard(a, other[j - 1].pitches)
            D[i, j] = c + min(D[i - 1, j - 1], D[i - 1, j] + 0.5, D[i, j - 1] + 0.5)
    i, j, path = n, m, []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        _, i, j = min((D[i - 1, j - 1], i - 1, j - 1), (D[i - 1, j], i - 1, j), (D[i, j - 1], i, j - 1))
    return path[::-1]


def best_matches(ref: list[Chord], other: list[Chord], path: list[tuple[int, int]]) -> dict[int, tuple[int, float]]:
    """For every `other` chord: (ref index, Jaccard) of its best partner on the path."""
    best: dict[int, tuple[int, float]] = {}
    for i, j in path:
        jac = jaccard(ref[i].pitches, other[j].pitches)
        if jac > best.get(j, (-1, -1.0))[1]:
            best[j] = (i, jac)
    return best
