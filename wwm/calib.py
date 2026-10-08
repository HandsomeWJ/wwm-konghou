"""Calibration piece: proves the key map, modifier handling, chords and speed in game."""
from __future__ import annotations

from .arrange import Note
from .keymap import NOTE_MAX, NOTE_MIN


def calibration_notes() -> list[Note]:
    notes: list[Note] = []
    t = 0.0
    # 1. chromatic C3 -> B5, one note per half second: every key and every Shift combo
    for p in range(NOTE_MIN, NOTE_MAX + 1):
        notes.append(Note(t, p, 96, 0.3))
        t += 0.5
    t += 1.0
    # 2. C major chord twice: four keys at once
    for _ in range(2):
        for p in (60, 64, 67, 72):
            notes.append(Note(t, p, 96, 0.4))
        t += 1.0
    # 3. chord mixing naturals and a sharp (C4 E4 G#4): Shift must not leak onto C and E
    for _ in range(2):
        for p in (60, 64, 68):
            notes.append(Note(t, p, 96, 0.4))
        t += 1.0
    t += 0.5
    # 4. eight repeats of G4 at 100 ms: per-key re-trigger speed
    for _ in range(8):
        notes.append(Note(t, 67, 96, 0.05))
        t += 0.1
    t += 1.0
    # 5. scale run in 16ths at 120 BPM (125 ms): sequential speed across keys
    for p in (60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62, 60):
        notes.append(Note(t, p, 96, 0.1))
        t += 0.125
    return notes
