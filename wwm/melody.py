"""Melody-only arrangements: the top line of the score's treble staff, optionally
moved onto the recording's timeline and checked note by note against the
transcription."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .arrange import Note, cluster_onsets
from .merge import retime


def treble_skyline(xml_path: str | Path, tempo_map: dict[int, float] | None, part_index: int = 0,
                   window: float = 0.03) -> list[Note]:
    """Highest note of every onset in one staff (the treble staff by default)."""
    from music21 import converter

    from .omr import score_notes

    score = converter.parse(str(xml_path))
    parts = list(score.parts)
    if not parts:
        raise ValueError("score has no parts")
    part = parts[min(part_index, len(parts) - 1)]
    notes = score_notes(part, tempo_map)
    out = []
    for o in cluster_onsets(notes, window):
        top = max(o.notes, key=lambda n: n.pitch)
        out.append(Note(o.t, top.pitch, top.vel, top.dur))
    return out


@dataclass
class SnapReport:
    confirmed: int = 0
    octave_fixed: int = 0
    replaced: int = 0
    unconfirmed: int = 0
    dropped_extra: int = 0

    def lines(self) -> list[str]:
        return [
            f"melody notes confirmed by the recording {self.confirmed}, octave corrected {self.octave_fixed}, "
            f"replaced by the recording's top note {self.replaced}, kept unconfirmed {self.unconfirmed}",
        ]


def snap_to_recording(melody: list[Note], recording: list[Note], tol: float = 0.12,
                      replace_within: int = 4, min_velocity: int = 30) -> tuple[list[Note], SnapReport]:
    """Check every melody note (recording timeline) against the transcription: keep
    exact matches, fix octave misreads, and replace a note that the recording
    contradicts by its top note nearby when that is within `replace_within` semitones."""
    rec = sorted((n for n in recording if n.vel >= min_velocity), key=lambda n: n.t)
    times = [n.t for n in rec]
    import bisect

    report = SnapReport()
    out: list[Note] = []
    for m in melody:
        lo = bisect.bisect_left(times, m.t - tol)
        hi = bisect.bisect_right(times, m.t + tol)
        near = rec[lo:hi]
        if any(n.pitch == m.pitch for n in near):
            report.confirmed += 1
            out.append(m)
            continue
        same_pc = [n for n in near if n.pitch % 12 == m.pitch % 12]
        if same_pc:
            best = min(same_pc, key=lambda n: abs(n.pitch - m.pitch))
            report.octave_fixed += 1
            out.append(Note(m.t, best.pitch, m.vel, m.dur))
            continue
        if near:
            top = max(near, key=lambda n: n.pitch)
            if abs(top.pitch - m.pitch) <= replace_within:
                report.replaced += 1
                out.append(Note(m.t, top.pitch, m.vel, m.dur))
                continue
        report.unconfirmed += 1
        out.append(m)
    return out, report


def melody_from_score(xml_path: str | Path, tempo_map: dict[int, float] | None,
                      align_notes: list[Note] | None = None, recording: list[Note] | None = None,
                      part_index: int = 0) -> tuple[list[Note], SnapReport | None, int]:
    """Treble-staff skyline; with `align_notes` (the full score or merged MIDI on the
    same timeline) and `recording`, moved onto the recording's timeline and snapped
    to it. Returns (notes, snap report or None, anchors used)."""
    melody = treble_skyline(xml_path, tempo_map, part_index)
    anchors = 0
    report = None
    if align_notes is not None and recording is not None:
        melody, anchors = retime(align_notes, recording, apply_to=melody)
        melody, report = snap_to_recording(melody, recording)
    return melody, report, anchors
