"""Thin a piano accompaniment for a sustaining instrument.

Two rules, applied to accompaniment notes only (the melody is left alone):
  1. a pitch struck again within `repeat_window` seconds adds nothing but mud while
     the previous strike still rings, so the re-strike is dropped (repeated chords,
     tremolo octaves, triplet chord pulses);
  2. while the melody itself is running fast, accompaniment onsets are kept at least
     `busy_gap` seconds apart, so two 16th-note lines never run at once.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from .arrange import Note, cluster_onsets


@dataclass
class ThinReport:
    melody: int = 0
    accompaniment: int = 0
    dropped_repeats: int = 0
    dropped_busy: int = 0
    per_window: list[tuple[float, int, int]] = field(default_factory=list)  # (start, acc notes before, after)

    def lines(self) -> list[str]:
        out = [
            f"melody notes {self.melody} (untouched), accompaniment {self.accompaniment}: "
            f"dropped {self.dropped_repeats} re-strikes and {self.dropped_busy} notes under a running melody",
        ]
        for start, before, after in self.per_window:
            if before:
                out.append(f"  {start:4.0f}-{start + 20:4.0f}s  accompaniment {before:4d} -> {after:4d}")
        return out


def split_melody(notes: list[Note], melody: list[Note], tol: float = 0.1) -> tuple[list[Note], list[Note]]:
    """Notes matching a melody note (same pitch, or the same pitch class an octave
    away, within `tol` seconds) vs the rest."""
    mt = [m.t for m in melody]
    mel, acc = [], []
    for n in notes:
        i = bisect.bisect_left(mt, n.t - tol)
        hit = False
        while i < len(mt) and mt[i] <= n.t + tol:
            if melody[i].pitch == n.pitch or (melody[i].pitch % 12 == n.pitch % 12 and abs(melody[i].pitch - n.pitch) == 12):
                hit = True
                break
            i += 1
        (mel if hit else acc).append(n)
    return mel, acc


def thin(notes: list[Note], melody: list[Note], repeat_window: float = 0.3, busy_gap: float = 0.25,
         busy_rate: float = 5.0, window: float = 0.03,
         protect: list[tuple[float, float]] | None = None) -> tuple[list[Note], ThinReport]:
    """`protect` lists (start, end) ranges in seconds that are left exactly as they are."""
    mel, acc = split_melody(notes, melody)
    report = ThinReport(melody=len(mel), accompaniment=len(acc))
    mel_times = sorted(m.t for m in mel)

    def melody_rate(t: float) -> float:
        if not mel_times:
            return 0.0
        lo, hi = max(t - 1.0, mel_times[0]), min(t + 1.0, mel_times[-1])
        a = bisect.bisect_left(mel_times, lo)
        b = bisect.bisect_right(mel_times, hi)
        return (b - a) / max(hi - lo, 0.5)  # notes per second over the span the melody covers

    kept: list[Note] = []
    last_struck: dict[int, float] = {}
    last_acc_onset = -1e9
    before_by_win: dict[int, int] = {}
    after_by_win: dict[int, int] = {}
    for o in cluster_onsets(acc, window):
        w = int(o.t // 20)
        before_by_win[w] = before_by_win.get(w, 0) + len(o.notes)
        if protect and any(a <= o.t < b for a, b in protect):
            kept.extend(o.notes)
            after_by_win[w] = after_by_win.get(w, 0) + len(o.notes)
            last_acc_onset = o.t
            for n in o.notes:
                last_struck[n.pitch] = o.t
            continue
        fresh = [n for n in o.notes if o.t - last_struck.get(n.pitch, -1e9) >= repeat_window]
        report.dropped_repeats += len(o.notes) - len(fresh)
        if fresh and melody_rate(o.t) >= busy_rate and o.t - last_acc_onset < busy_gap:
            report.dropped_busy += len(fresh)
            fresh = []
        if fresh:
            last_acc_onset = o.t
            for n in fresh:
                last_struck[n.pitch] = o.t
            kept.extend(fresh)
            after_by_win[w] = after_by_win.get(w, 0) + len(fresh)
    for w in sorted(before_by_win):
        report.per_window.append((w * 20.0, before_by_win[w], after_by_win.get(w, 0)))
    out = sorted(mel + kept, key=lambda n: (n.t, n.pitch))
    return out, report
