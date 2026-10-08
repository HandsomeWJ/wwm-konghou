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
    dropped_figuration: int = 0
    added_support: int = 0
    per_window: list[tuple[float, int, int]] = field(default_factory=list)  # (start, acc notes before, after)

    def lines(self) -> list[str]:
        out = [
            f"melody notes {self.melody}, accompaniment {self.accompaniment}: "
            f"dropped {self.dropped_repeats} re-strikes and {self.dropped_busy} notes under a running melody; "
            f"figuration thinned by {self.dropped_figuration} notes, {self.added_support} supporting chord strikes added",
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


BASS_SPLIT = 55  # below G3 counts as bass register for the figuration rules


def figuration_cells(upper: list[Note], rate: float, max_distinct: int, cell: float = 2.0) -> set[int]:
    """Cells (index = floor(t / cell)) where the upper register is a pianistic
    figuration: fast (>= rate notes/s) yet circling over few pitches (<= max_distinct)."""
    cells: set[int] = set()
    by_cell: dict[int, list[Note]] = {}
    for n in upper:
        by_cell.setdefault(int(n.t // cell), []).append(n)
    for c, ns in by_cell.items():
        if len(ns) / cell >= rate and len({n.pitch for n in ns}) <= max_distinct:
            cells.add(c)
    return cells


def thin(notes: list[Note], melody: list[Note], repeat_window: float = 0.3, busy_gap: float = 0.25,
         busy_rate: float = 5.0, window: float = 0.03,
         protect: list[tuple[float, float]] | None = None,
         figuration_rate: float = 4.0, figuration_distinct: int = 6, figuration_keep: int = 2,
         support_gap: float = 2.2) -> tuple[list[Note], ThinReport]:
    """`protect` lists (start, end) ranges in seconds that are left exactly as they are.

    Rule 3 (first): where the upper register is a figuration (fast, few pitches, e.g.
    sextuplet shimmers), keep every `figuration_keep`-th upper-register onset so the
    figure stays regular at a fraction of the density.
    Rule 4: in those stretches, re-strike the last bass-register chord whenever the
    bass has been silent for `support_gap` seconds, so the harmony keeps ringing.
    Rules 1-2 then prune the accompaniment: no pitch re-struck within
    `repeat_window`; onsets at least `busy_gap` apart while the melody runs fast."""
    report = ThinReport()

    def protected(t: float) -> bool:
        return bool(protect) and any(a <= t < b for a, b in protect)

    upper = [n for n in notes if n.pitch >= BASS_SPLIT]
    lower = [n for n in notes if n.pitch < BASS_SPLIT]
    fig = figuration_cells(upper, figuration_rate, figuration_distinct)
    if fig:
        mel0, _ = split_melody(upper, melody)
        mel_ids = {id(n) for n in mel0}
        # a slow melody riding on a fast figuration keeps every note; when the melody
        # itself is the figure (>= 3 melody notes/s in the cell) everything is thinned
        mel_rate_cell: dict[int, float] = {}
        for n in mel0:
            c = int(n.t // 2.0)
            mel_rate_cell[c] = mel_rate_cell.get(c, 0.0) + 0.5
        kept_upper: list[Note] = []
        counter = 0
        fig_onset_times: list[float] = []
        for o in cluster_onsets(upper, window):
            c = int(o.t // 2.0)
            if c in fig and not protected(o.t):
                fig_onset_times.append(o.t)
                has_melody = any(id(n) in mel_ids for n in o.notes)
                if has_melody and mel_rate_cell.get(c, 0.0) < 3.0:
                    kept_upper.extend(o.notes)  # slow melody: untouched, not counted
                    continue
                if counter % figuration_keep == 0:
                    kept_upper.extend(o.notes)
                else:
                    report.dropped_figuration += len(o.notes)
                counter += 1
            else:
                counter = 0
                kept_upper.extend(o.notes)
        upper = kept_upper
        if support_gap > 0:
            low_onsets = cluster_onsets(lower, window)
            extra: list[Note] = []
            last_chord: list[Note] | None = None
            last_t = -1e9
            li = 0
            for t in fig_onset_times:
                while li < len(low_onsets) and low_onsets[li].t <= t:
                    last_chord, last_t = low_onsets[li].notes, low_onsets[li].t
                    li += 1
                if last_chord and t - last_t >= support_gap:
                    extra.extend(Note(t, n.pitch, n.vel, n.dur) for n in last_chord)
                    last_t = t
                    report.added_support += len(last_chord)
            lower = lower + extra
        notes = sorted(upper + lower, key=lambda n: (n.t, n.pitch))

    mel, acc = split_melody(notes, melody)
    report.melody, report.accompaniment = len(mel), len(acc)
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
