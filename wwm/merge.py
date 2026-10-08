"""Patch an OMR score with a transcription of a recording of the same piece.

The score keeps its clean timeline. Wherever the two sources disagree (misread
pitches, missing or extra notes, garbled tuplets), the recording's notes replace the
score's, time-warped between the nearest agreeing chords on either side."""
from __future__ import annotations

from dataclasses import dataclass, field

from .align import Chord, best_matches, dtw_path, jaccard, to_chords
from .arrange import Note


@dataclass
class Patch:
    t0: float  # score time
    t1: float
    removed: int
    inserted: int


@dataclass
class MergeReport:
    anchors: int = 0
    score_notes: int = 0
    recording_notes: int = 0
    merged_notes: int = 0
    patches: list[Patch] = field(default_factory=list)

    def lines(self, measure_of=None) -> list[str]:
        out = [
            f"score {self.score_notes} notes, recording {self.recording_notes} notes, agreeing anchors {self.anchors}",
            f"merged {self.merged_notes} notes; {len(self.patches)} regions patched from the recording "
            f"({sum(p.removed for p in self.patches)} score notes replaced by {sum(p.inserted for p in self.patches)})",
        ]
        for p in self.patches:
            where = f"  {p.t0:6.1f}s - {p.t1:6.1f}s"
            if measure_of:
                where += f"  (m{measure_of(p.t0)}-m{measure_of(p.t1)})"
            out.append(f"{where}: {p.removed} -> {p.inserted} notes")
        return out


def anchor_pairs(S: list[Chord], R: list[Chord], min_jaccard: float = 0.5, band: int = 120) -> list[tuple[int, int]]:
    """(score chord index, recording chord index) pairs where both sources agree. A
    chord of two or more notes anchors on its own; a single note only with an agreeing
    neighbour, so runs cannot be pinned note by note to the wrong run."""
    path = dtw_path(R, S, band)
    best = best_matches(R, S, path)

    def strong(j: int) -> bool:
        i, jac = best.get(j, (-1, 0.0))
        if jac < min_jaccard:
            return False
        if len(S[j].pitches) >= 2 and len(R[i].pitches) >= 2:
            return True
        for dj in (-1, 1):
            k = j + dj
            if 0 <= k < len(S):
                ik, jk = best.get(k, (-1, 0.0))
                if ik == i + dj and jk >= min_jaccard:
                    return True
        return False

    anchors: list[tuple[int, int]] = []
    last_i = -1
    for j in range(len(S)):
        if strong(j) and best[j][0] > last_i:
            anchors.append((j, best[j][0]))
            last_i = best[j][0]
    return anchors


def retime(score: list[Note], recording: list[Note], min_jaccard: float = 0.5, window: float = 0.03,
           min_velocity: int = 30, band: int = 120) -> tuple[list[Note], int]:
    """Move notes from the score's timeline onto the recording's: every agreeing chord
    is a fixed point, times in between are interpolated, so the result follows the
    pianist's tempo and rubato while keeping the score's (or merged) notes."""
    import numpy as np

    recording = [n for n in recording if n.vel >= min_velocity]
    S, R = to_chords(score, window), to_chords(recording, window)
    anchors = anchor_pairs(S, R, min_jaccard, band)
    if len(anchors) < 2:
        return list(score), len(anchors)
    xs = np.array([S[j].t for j, _ in anchors])
    ys = np.array([R[i].t for _, i in anchors])
    keep = np.concatenate([[True], np.diff(xs) > 1e-6])  # strictly increasing for interp
    xs, ys = xs[keep], ys[keep]
    slope_head = (ys[1] - ys[0]) / (xs[1] - xs[0])
    slope_tail = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])

    def f(t: float) -> float:
        if t <= xs[0]:
            return ys[0] + (t - xs[0]) * slope_head
        if t >= xs[-1]:
            return ys[-1] + (t - xs[-1]) * slope_tail
        return float(np.interp(t, xs, ys))

    out = []
    for n in score:
        t = f(n.t)
        dur = max(f(n.t + n.dur) - t, 0.05)
        out.append(Note(t, n.pitch, n.vel, dur))
    out.sort(key=lambda n: (n.t, n.pitch))
    t0 = out[0].t if out else 0.0
    return [Note(n.t - min(t0, 0.0), n.pitch, n.vel, n.dur) for n in out], len(anchors)


def _consistent(score_part: list[Chord], rec_part: list[Chord], min_jaccard: float) -> bool:
    if len(score_part) != len(rec_part):
        return False
    return all(jaccard(s.pitches, r.pitches) >= min_jaccard for s, r in zip(score_part, rec_part))


def _warp(rec_part: list[Chord], ra: float, rb: float, ta: float, tb: float) -> list[Note]:
    scale = (tb - ta) / (rb - ra) if rb > ra else 1.0
    out: list[Note] = []
    for c in rec_part:
        t = ta + (c.t - ra) * scale
        out.extend(Note(t, n.pitch, n.vel, max(n.dur * scale, 0.05)) for n in c.notes)
    return out


def merge(score: list[Note], recording: list[Note], min_jaccard: float = 0.5, window: float = 0.03,
          min_velocity: int = 30, band: int = 120) -> tuple[list[Note], MergeReport]:
    recording = [n for n in recording if n.vel >= min_velocity]
    S, R = to_chords(score, window), to_chords(recording, window)
    report = MergeReport(score_notes=len(score), recording_notes=len(recording))
    if not S or not R:
        return list(score), report

    anchors = anchor_pairs(S, R, min_jaccard, band)
    report.anchors = len(anchors)
    if not anchors:
        return list(score), report

    merged: list[Note] = []

    def patch(score_part, rec_part, ta, tb, ra, rb):
        if not score_part and not rec_part:
            return
        if _consistent(score_part, rec_part, min_jaccard):
            for c in score_part:
                merged.extend(c.notes)
            return
        new = _warp(rec_part, ra, rb, ta, tb)
        merged.extend(new)
        removed = sum(len(c.notes) for c in score_part)
        if removed or new:
            report.patches.append(Patch(ta, tb, removed, len(new)))

    # head: before the first anchor, warped with the first segment's tempo ratio
    j0, i0 = anchors[0]
    if j0 > 0 or i0 > 0:
        if len(anchors) > 1:
            j1, i1 = anchors[1]
            ratio = (S[j1].t - S[j0].t) / (R[i1].t - R[i0].t) if R[i1].t > R[i0].t else 1.0
        else:
            ratio = 1.0
        head_start_r = R[0].t if i0 > 0 else R[i0].t
        ta = S[j0].t - (R[i0].t - head_start_r) * ratio
        patch(S[:j0], R[:i0], ta, S[j0].t, head_start_r, R[i0].t)

    for k, (j, i) in enumerate(anchors):
        merged.extend(S[j].notes)
        if k + 1 < len(anchors):
            jn, inn = anchors[k + 1]
            patch(S[j + 1 : jn], R[i + 1 : inn], S[j].t, S[jn].t, R[i].t, R[inn].t)

    # tail
    jl, il = anchors[-1]
    if jl + 1 < len(S) or il + 1 < len(R):
        if len(anchors) > 1:
            jp, ip = anchors[-2]
            ratio = (S[jl].t - S[jp].t) / (R[il].t - R[ip].t) if R[il].t > R[ip].t else 1.0
        else:
            ratio = 1.0
        rb = R[-1].t + 0.01 if il + 1 < len(R) else R[il].t + 0.01
        tb = S[jl].t + (rb - R[il].t) * ratio
        patch(S[jl + 1 :], R[il + 1 :], S[jl].t, tb, R[il].t, rb)

    merged.sort(key=lambda n: (n.t, n.pitch))
    report.merged_notes = len(merged)
    return merged, report
