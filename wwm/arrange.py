"""Reduce any MIDI to what a Where Winds Meet instrument can play.

Pipeline (each step is a pure function so it can be tested alone):
  load_midi -> cluster_onsets -> choose_transposition -> fold/snap/dedupe
  -> limit_polyphony -> enforce_retrigger
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import mido

from .keymap import NOTE_MAX, NOTE_MIN, SCALE_PCS

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


@dataclass
class ArrangeReport:
    transpose: int = 0
    total_notes: int = 0
    in_range: int = 0
    folded: int = 0
    accidentals: int = 0
    snapped: int = 0
    dropped_snap: int = 0
    dropped_duplicate: int = 0
    dropped_polyphony: int = 0
    dropped_retrigger: int = 0
    kept: int = 0
    duration: float = 0.0

    def lines(self) -> list[str]:
        return [
            f"transpose        {self.transpose:+d} semitones",
            f"notes in         {self.total_notes}",
            f"  in range       {self.in_range}",
            f"  octave-folded  {self.folded}",
            f"  accidentals    {self.accidentals}" + (f" (snapped {self.snapped}, dropped {self.dropped_snap})" if self.snapped or self.dropped_snap else ""),
            f"  dropped dup    {self.dropped_duplicate}",
            f"  dropped voices {self.dropped_polyphony}",
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


def transposition_score(onsets: list[Onset], shift: int, opts: ArrangeOptions) -> float:
    """Higher is better. Rewards notes that land in range without folding and,
    in 21-key mode, without accidentals. Melody (top note of a chord) counts 1.5x."""
    accidental_penalty = 0.9 if opts.mode == "21" else 0.15
    total = 0.0
    weight = 0.0
    for on in onsets:
        pitches = [n.pitch + shift for n in on.notes]
        top = max(pitches)
        for p in pitches:
            w = 1.5 if p == top else 1.0
            if opts.lo <= p <= opts.hi:
                s = 1.0
            else:  # folding the melody wrecks its contour; folding a bass note is mild
                s = 0.35 if p == top else 0.8
            if p % 12 not in SCALE_PCS:
                s -= accidental_penalty
            total += w * s
            weight += w
    score = total / weight if weight else 0.0
    score -= 0.002 * abs(shift)
    if opts.mode == "36" and shift % 12 != 0:
        score -= opts.key_change_penalty
    return score


def choose_transposition(onsets: list[Onset], opts: ArrangeOptions) -> int:
    lo, hi = opts.search_range
    best_shift, best_score = 0, -math.inf
    for shift in sorted(range(lo, hi + 1), key=lambda s: (abs(s), s)):
        score = transposition_score(onsets, shift, opts)
        if score > best_score + 1e-9:
            best_shift, best_score = shift, score
    return best_shift


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


def arrange(notes: list[Note], opts: ArrangeOptions) -> tuple[list[Onset], ArrangeReport]:
    report = ArrangeReport()
    notes = [n for n in notes if n.vel >= opts.min_velocity]
    report.total_notes = len(notes)
    if not notes:
        return [], report
    onsets = cluster_onsets(notes, opts.cluster_window)
    shift = opts.transpose if opts.transpose is not None else choose_transposition(onsets, opts)
    report.transpose = shift

    t0 = onsets[0].t
    out: list[Onset] = []
    last_hit: dict[int, float] = {}
    for on in onsets:
        chord = reduce_chord(on, shift, opts, report)
        kept = limit_polyphony(chord, opts.max_voices)
        report.dropped_polyphony += len(chord) - len(kept)
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
