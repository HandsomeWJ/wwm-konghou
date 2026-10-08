"""Reduce any MIDI to what a Where Winds Meet instrument can play.

Pipeline (each step is a pure function so it can be tested alone):
  load_midi -> cluster_onsets -> choose_transposition -> fold/snap/dedupe
  -> limit_polyphony -> enforce_retrigger
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import mido

from .keymap import NOTE_MAX, NOTE_MIN, SCALE_PCS, KeyMap

DRUM_CHANNEL = 9


@dataclass
class Note:
    t: float  # onset, seconds
    pitch: int
    vel: int = 90
    dur: float = 0.1


@dataclass
class Onset:
    t: float
    notes: list[Note] = field(default_factory=list)


@dataclass
class ArrangeOptions:
    mode: str = "36"  # "36": sharps via modifier; "21": naturals only
    max_voices: int = 4
    transpose: int | None = None  # None = search
    search_range: tuple[int, int] = (-24, 24)
    cluster_window: float = 0.030
    min_retrigger: float = 0.040  # 2 frames at 60 FPS: key up must be seen between hits
    min_velocity: int = 1
    snap: str = "down"  # 21-key only: down | up | drop
    key_change_penalty: float = 0.05  # 36-key: only leave the original key for a big gain
    lo: int = NOTE_MIN
    hi: int = NOTE_MAX
    segment_transpose: bool = True  # 36-key: pick the shift per key region, not once for the whole piece
    segment_window: float = 10.0    # seconds of music each shift decision looks at
    switch_penalty: float = 40.0    # cost of changing shift between regions, in note units: only a region
                                    # full of rolled chords is worth re-keying
    segment_range: tuple[int, int] = (-6, 5)  # one octave of candidates, so a key never recurs an octave apart
    off_key_cost: float = 0.03      # per note, for any shift other than 0: the original key wins ties
    max_groups: int = 2             # 36-key: chords needing more key groups lose inner notes (3 groups = a 100 ms roll)


@dataclass
class ArrangeReport:
    transpose: int = 0
    segments: list[tuple[float, float, int]] = field(default_factory=list)  # (start, end, shift)
    total_notes: int = 0
    in_range: int = 0
    folded: int = 0
    accidentals: int = 0
    snapped: int = 0
    dropped_snap: int = 0
    dropped_duplicate: int = 0
    dropped_polyphony: int = 0
    dropped_groups: int = 0
    dropped_retrigger: int = 0
    kept: int = 0
    duration: float = 0.0

    def lines(self) -> list[str]:
        if self.segments and len(self.segments) > 1:
            first = "transpose        " + " | ".join(f"{a:.0f}-{b:.0f}s {sh:+d}" for a, b, sh in self.segments)
        else:
            first = f"transpose        {self.transpose:+d} semitones"
        return [
            first,
            f"notes in         {self.total_notes}",
            f"  in range       {self.in_range}",
            f"  octave-folded  {self.folded}",
            f"  accidentals    {self.accidentals}" + (f" (snapped {self.snapped}, dropped {self.dropped_snap})" if self.snapped or self.dropped_snap else ""),
            f"  dropped dup    {self.dropped_duplicate}",
            f"  dropped voices {self.dropped_polyphony}" + (f" (+{self.dropped_groups} to keep chords to {2} key groups)" if self.dropped_groups else ""),
            f"  dropped fast   {self.dropped_retrigger}",
            f"notes out        {self.kept}",
            f"duration         {self.duration:.1f}s",
        ]


def load_midi(path: str) -> list[Note]:
    """All non-drum notes of a MIDI file with onsets in seconds, tempo map applied."""
    mid = mido.MidiFile(path)
    notes: list[Note] = []
    open_notes: dict[tuple[int, int], tuple[float, int]] = {}
    t = 0.0
    for msg in mid:  # merged tracks, msg.time is a delta in seconds
        t += msg.time
        if msg.type not in ("note_on", "note_off") or msg.channel == DRUM_CHANNEL:
            continue
        key = (msg.channel, msg.note)
        if msg.type == "note_on" and msg.velocity > 0:
            if key in open_notes:
                start, vel = open_notes.pop(key)
                notes.append(Note(start, msg.note, vel, max(t - start, 0.01)))
            open_notes[key] = (t, msg.velocity)
        elif key in open_notes:
            start, vel = open_notes.pop(key)
            notes.append(Note(start, msg.note, vel, max(t - start, 0.01)))
    for (_, pitch), (start, vel) in open_notes.items():
        notes.append(Note(start, pitch, vel, max(t - start, 0.01)))
    notes.sort(key=lambda n: (n.t, n.pitch))
    return notes


def cluster_onsets(notes: list[Note], window: float) -> list[Onset]:
    """Group notes whose onsets fall within `window` seconds of a group's first note."""
    onsets: list[Onset] = []
    for n in sorted(notes, key=lambda n: (n.t, n.pitch)):
        if onsets and n.t - onsets[-1].t <= window:
            onsets[-1].notes.append(n)
        else:
            onsets.append(Onset(n.t, [n]))
    return onsets


def fold(pitch: int, lo: int = NOTE_MIN, hi: int = NOTE_MAX) -> int:
    while pitch < lo:
        pitch += 12
    while pitch > hi:
        pitch -= 12
    return pitch


GROUP_PENALTY = {0: 0.0, 1: 0.0, 2: 0.6, 3: 1.5, 4: 3.0}  # per onset, in note units


def key_groups(pitches: list[int], keymap: KeyMap | None) -> int:
    """How many key groups the player must send for this chord: naturals count as one,
    each modifier (Shift, Ctrl) as another. Each extra group is sent ~50 ms later, so
    chords with two or three groups roll audibly."""
    if keymap is None:
        return 1
    naturals, mods = False, set()
    for p in pitches:
        kp = keymap.press_for(p)
        if kp.modifier is None:
            naturals = True
        else:
            mods.add(kp.modifier)
    return int(naturals) + len(mods)


def transposition_penalty(onsets: list[Onset], shift: int, opts: ArrangeOptions, keymap: KeyMap | None = None) -> tuple[float, float]:
    """(penalty, weight): lower penalty is better. Penalises folding (melody badly, bass
    mildly), accidentals (heavily in 21-key mode, mildly in 36-key mode) and, in 36-key
    mode, chords whose notes need more than one key group."""
    accidental_penalty = 0.9 if opts.mode == "21" else 0.15
    penalty = 0.0
    weight = 0.0
    for on in onsets:
        pitches = [n.pitch + shift for n in on.notes]
        top = max(pitches)
        folded = []
        for p in pitches:
            w = 1.5 if p == top else 1.0
            weight += w
            if not (opts.lo <= p <= opts.hi):
                penalty += w * (0.65 if p == top else 0.2)
            q = fold(p, opts.lo, opts.hi)
            folded.append(q)
            if q % 12 not in SCALE_PCS:
                penalty += w * accidental_penalty
        if opts.mode == "36" and keymap is not None:
            penalty += GROUP_PENALTY.get(min(key_groups(sorted(set(folded)), keymap), 4), 3.0)
    return penalty, weight


def transposition_score(onsets: list[Onset], shift: int, opts: ArrangeOptions, keymap: KeyMap | None = None) -> float:
    """Higher is better; kept for callers that compare shifts on a whole piece."""
    penalty, weight = transposition_penalty(onsets, shift, opts, keymap)
    score = 1.0 - penalty / weight if weight else 0.0
    score -= 0.002 * abs(shift)
    if opts.mode == "36" and shift % 12 != 0:
        score -= opts.key_change_penalty
    return score


def choose_transposition(onsets: list[Onset], opts: ArrangeOptions, keymap: KeyMap | None = None) -> int:
    lo, hi = opts.search_range
    best_shift, best_score = 0, -math.inf
    for shift in sorted(range(lo, hi + 1), key=lambda s: (abs(s), s)):
        score = transposition_score(onsets, shift, opts, keymap)
        if score > best_score + 1e-9:
            best_shift, best_score = shift, score
    return best_shift


def choose_transposition_segments(onsets: list[Onset], opts: ArrangeOptions, keymap: KeyMap) -> list[tuple[float, float, int]]:
    """Shift per region: the piece is cut into cells of half a window; each cell's cost
    for every candidate shift is measured over the window around it, and a dynamic
    programme picks the cheapest path with a penalty for every change of shift.
    Returns (start, end, shift) segments covering the whole piece."""
    if not onsets:
        return []
    lo, hi = opts.segment_range
    shifts = list(range(lo, hi + 1))
    t0, t1 = onsets[0].t, onsets[-1].t
    hop = opts.segment_window / 2
    n_cells = max(1, int(math.ceil((t1 - t0) / hop)))
    cost = [[0.0] * len(shifts) for _ in range(n_cells)]
    for c in range(n_cells):
        a = t0 + c * hop - hop / 2
        b = a + opts.segment_window
        window = [o for o in onsets if a <= o.t < b]
        for k, sh in enumerate(shifts):
            pen, weight = transposition_penalty(window, sh, opts, keymap)
            if sh != 0:
                pen += opts.off_key_cost * weight
            cost[c][k] = pen
    # Viterbi over cells
    P = opts.switch_penalty
    best = [cost[0][:]]
    back = [[0] * len(shifts)]
    for c in range(1, n_cells):
        row, arg = [], []
        prev = best[-1]
        stay_min = min(prev)
        stay_arg = prev.index(stay_min)
        for k in range(len(shifts)):
            # cheapest predecessor: itself (no switch) or the global best plus the switch penalty
            if prev[k] <= stay_min + P:
                row.append(prev[k] + cost[c][k]); arg.append(k)
            else:
                row.append(stay_min + P + cost[c][k]); arg.append(stay_arg)
        best.append(row); back.append(arg)
    k = best[-1].index(min(best[-1]))
    chosen = [0] * n_cells
    for c in range(n_cells - 1, -1, -1):
        chosen[c] = shifts[k]
        k = back[c][k]
    segments: list[tuple[float, float, int]] = []
    for c, sh in enumerate(chosen):
        start = t0 + c * hop if c else t0
        end = t0 + (c + 1) * hop if c + 1 < n_cells else t1 + 1.0
        if segments and segments[-1][2] == sh:
            segments[-1] = (segments[-1][0], end, sh)
        else:
            segments.append((start, end, sh))
    return segments


def reduce_chord(onset: Onset, shift: int, opts: ArrangeOptions, report: ArrangeReport) -> list[Note]:
    """Transpose, fold into range, resolve accidentals, dedupe. Returns notes highest first."""
    chord: list[Note] = []
    seen: set[int] = set()
    for n in sorted(onset.notes, key=lambda n: -n.pitch):
        p = n.pitch + shift
        if opts.lo <= p <= opts.hi:
            report.in_range += 1
        else:
            report.folded += 1
            p = fold(p, opts.lo, opts.hi)
        if p % 12 not in SCALE_PCS:
            report.accidentals += 1
            if opts.mode == "21":
                if opts.snap == "drop":
                    report.dropped_snap += 1
                    continue
                p = fold(p - 1 if opts.snap == "down" else p + 1, opts.lo, opts.hi)
                report.snapped += 1
        if p in seen:
            report.dropped_duplicate += 1
            continue
        seen.add(p)
        chord.append(replace(n, t=onset.t, pitch=p))
    chord.sort(key=lambda n: -n.pitch)
    return chord


def limit_polyphony(chord: list[Note], max_voices: int) -> list[Note]:
    """Keep the melody (top voices) and the bass; drop inner filler. Input highest first."""
    if max_voices < 1 or len(chord) <= max_voices:
        return chord
    if max_voices == 1:
        return chord[:1]
    return chord[: max_voices - 1] + chord[-1:]


def limit_groups(chord: list[Note], keymap: KeyMap, max_groups: int) -> tuple[list[Note], int]:
    """Drop inner notes until the chord needs at most `max_groups` key groups. The top
    note (melody) and the bass are kept; the smallest modifier group goes first."""
    if max_groups < 1 or len(chord) <= 1:
        return chord, 0
    dropped = 0
    while key_groups([n.pitch for n in chord], keymap) > max_groups and len(chord) > 1:
        by_group: dict[str | None, list[Note]] = {}
        for n in chord:
            by_group.setdefault(keymap.press_for(n.pitch).modifier, []).append(n)
        top, bass = chord[0], chord[-1]
        candidates = [n for n in chord if n is not top and n is not bass]
        if not candidates:
            candidates = [n for n in chord if n is not top]
        if not candidates:
            break
        # drop a note from the group with the fewest notes, inner voices first
        victim = min(candidates, key=lambda n: (len(by_group[keymap.press_for(n.pitch).modifier]), -abs(n.pitch - top.pitch)))
        chord = [n for n in chord if n is not victim]
        dropped += 1
    return chord, dropped


def arrange(notes: list[Note], opts: ArrangeOptions, keymap: KeyMap | None = None) -> tuple[list[Onset], ArrangeReport]:
    report = ArrangeReport()
    notes = [n for n in notes if n.vel >= opts.min_velocity]
    report.total_notes = len(notes)
    if not notes:
        return [], report
    onsets = cluster_onsets(notes, opts.cluster_window)
    if opts.transpose is not None:
        segments = [(onsets[0].t, onsets[-1].t + 1.0, opts.transpose)]
    elif opts.mode == "36" and opts.segment_transpose and keymap is not None:
        segments = choose_transposition_segments(onsets, opts, keymap)
    else:
        segments = [(onsets[0].t, onsets[-1].t + 1.0, choose_transposition(onsets, opts, keymap))]
    report.segments = [(a - onsets[0].t, b - onsets[0].t, sh) for a, b, sh in segments]
    report.transpose = segments[0][2]

    def shift_at(t: float) -> int:
        for a, b, sh in segments:
            if a <= t < b:
                return sh
        return segments[-1][2]

    t0 = onsets[0].t
    out: list[Onset] = []
    last_hit: dict[int, float] = {}
    for on in onsets:
        chord = reduce_chord(on, shift_at(on.t), opts, report)
        kept = limit_polyphony(chord, opts.max_voices)
        report.dropped_polyphony += len(chord) - len(kept)
        if opts.mode == "36" and keymap is not None:
            kept, dropped = limit_groups(kept, keymap, opts.max_groups)
            report.dropped_groups += dropped
        t = on.t - t0
        playable: list[Note] = []
        for n in kept:
            if t - last_hit.get(n.pitch, -math.inf) < opts.min_retrigger:
                report.dropped_retrigger += 1
                continue
            last_hit[n.pitch] = t
            playable.append(replace(n, t=t))
        if playable:
            out.append(Onset(t, playable))
    report.kept = sum(len(o.notes) for o in out)
    report.duration = out[-1].t if out else 0.0
    return out, report
