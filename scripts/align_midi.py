"""Align two MIDIs of the same piece (e.g. an MP3 transcription and an OMR result)
with dynamic time warping over onset chords, ignoring tempo and rubato.

Reports how many of the reference's notes the other file reproduces (pitch-exact and
octave-insensitive) and lists the regions where they disagree, so you know where to
look in the score.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

sys.path.insert(0, ".")
from wwm.arrange import cluster_onsets, load_midi  # noqa: E402


def chords(path: str, window: float = 0.03):
    onsets = cluster_onsets(load_midi(path), window)
    return [(o.t, frozenset(n.pitch for n in o.notes)) for o in onsets]


def cost(a: frozenset, b: frozenset, octave_free: bool) -> float:
    if octave_free:
        a, b = frozenset(p % 12 for p in a), frozenset(p % 12 for p in b)
    return 1.0 - len(a & b) / len(a | b)


def dtw(ref, other, octave_free: bool, band: int):
    n, m = len(ref), len(other)
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    slope = m / n if n else 1.0
    for i in range(1, n + 1):
        j_lo = max(1, int(i * slope) - band)
        j_hi = min(m, int(i * slope) + band)
        for j in range(j_lo, j_hi + 1):
            c = cost(ref[i - 1][1], other[j - 1][1], octave_free)
            D[i, j] = c + min(D[i - 1, j - 1], D[i - 1, j] + 0.5, D[i, j - 1] + 0.5)
    # backtrack
    i, j, path = n, m, []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        steps = [(D[i - 1, j - 1], i - 1, j - 1), (D[i - 1, j], i - 1, j), (D[i, j - 1], i, j - 1)]
        _, i, j = min(steps)
    return path[::-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference", help="e.g. the MP3 transcription")
    ap.add_argument("other", help="e.g. the OMR MIDI")
    ap.add_argument("--band", type=int, default=120, help="DTW band in onsets")
    ap.add_argument("--window", type=float, default=0.03)
    ap.add_argument("--window-secs", type=float, default=20.0, help="size of the per-window coverage report")
    args = ap.parse_args()
    ref, oth = chords(args.reference, args.window), chords(args.other, args.window)
    path = dtw(ref, oth, octave_free=False, band=args.band)

    matched_exact = matched_pc = 0
    total = sum(len(c) for _, c in ref)
    hits_per_ref = {}
    for i, j in path:
        a, b = ref[i][1], oth[j][1]
        hits_per_ref.setdefault(i, [0, 0])
        hits_per_ref[i][0] = max(hits_per_ref[i][0], len(a & b))
        pcs_b = {p % 12 for p in b}
        hits_per_ref[i][1] = max(hits_per_ref[i][1], sum(1 for p in a if p % 12 in pcs_b))
    matched_exact = sum(v[0] for v in hits_per_ref.values())
    matched_pc = sum(v[1] for v in hits_per_ref.values())
    print(f"reference {total} notes in {len(ref)} onsets; other {sum(len(c) for _, c in oth)} notes in {len(oth)} onsets")
    print(f"notes reproduced: exact pitch {matched_exact / total:.0%}, pitch class only {matched_pc / total:.0%}")

    # divergent regions: runs of reference onsets with < half their notes matched
    bad = [i for i, (t, c) in enumerate(ref) if hits_per_ref.get(i, [0, 0])[0] < 0.5 * len(c)]
    regions, start = [], None
    for k, i in enumerate(bad):
        if start is None:
            start = i
        if k + 1 == len(bad) or bad[k + 1] != i + 1:
            if i - start + 1 >= 4:
                regions.append((ref[start][0], ref[i][0], i - start + 1))
            start = None
    print(f"divergent regions (reference time, >=4 onsets): {len(regions)}")
    for t0, t1, cnt in regions[:25]:
        print(f"  {t0:6.1f}s - {t1:6.1f}s  ({cnt} onsets)")

    # whole-track coverage: match rate per window of reference time
    win = args.window_secs
    end = ref[-1][0] if ref else 0.0
    print(f"match rate per {win:.0f}s window of the reference (exact / pitch-class):")
    k = 0
    while k * win <= end:
        idx = [i for i, (t, _) in enumerate(ref) if k * win <= t < (k + 1) * win]
        n_notes = sum(len(ref[i][1]) for i in idx)
        if n_notes:
            ex = sum(hits_per_ref.get(i, [0, 0])[0] for i in idx) / n_notes
            pc = sum(hits_per_ref.get(i, [0, 0])[1] for i in idx) / n_notes
            bar = "#" * int(round(ex * 20))
            print(f"  {k * win:5.0f}-{(k + 1) * win:4.0f}s  {ex:4.0%} / {pc:4.0%}  {bar:<20} ({n_notes} notes)")
        k += 1


if __name__ == "__main__":
    main()
