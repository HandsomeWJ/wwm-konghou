"""Write arranged onsets as a game-ready MIDI (notes 48-83) and a .wwm.json key script."""
from __future__ import annotations

import json
from pathlib import Path

import mido

from .arrange import Note, Onset
from .keymap import KeyMap, pitch_name

TICKS_PER_BEAT = 480
TEMPO = 500_000  # 120 BPM; onsets are already absolute seconds, so tempo is cosmetic
MIN_DUR = 0.05
MAX_DUR = 0.25


def _sec_to_tick(seconds: float) -> int:
    return int(round(mido.second2tick(seconds, TICKS_PER_BEAT, TEMPO)))


def flatten(onsets: list[Onset]) -> list[Note]:
    return [n for on in onsets for n in on.notes]


def write_midi(notes: list[Note], path: str | Path, clamp: bool = True) -> None:
    """Single-track type-0 MIDI. Durations are clamped short so repeated notes never
    overlap; the Konghou is plucked, so release timing is irrelevant in game."""
    notes = sorted(notes, key=lambda n: (n.t, n.pitch))
    next_same: dict[int, float] = {}
    ends: list[float] = [0.0] * len(notes)
    for i in range(len(notes) - 1, -1, -1):
        n = notes[i]
        dur = min(max(n.dur, MIN_DUR), MAX_DUR) if clamp else n.dur
        limit = next_same.get(n.pitch)
        if limit is not None:
            dur = min(dur, max(limit - n.t - 0.01, 0.01))
        ends[i] = n.t + dur
        next_same[n.pitch] = n.t

    events: list[tuple[int, int, mido.Message]] = []
    for n, end in zip(notes, ends):
        start_tick = _sec_to_tick(n.t)
        end_tick = max(_sec_to_tick(end), start_tick + 1)
        events.append((start_tick, 1, mido.Message("note_on", note=n.pitch, velocity=max(1, min(127, n.vel)))))
        events.append((end_tick, 0, mido.Message("note_off", note=n.pitch, velocity=0)))
    events.sort(key=lambda e: (e[0], e[1]))

    mid = mido.MidiFile(type=0, ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="WWM Konghou", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=TEMPO, time=0))
    track.append(mido.Message("program_change", program=46, time=0))  # GM harp, for previews elsewhere
    last = 0
    for tick, _, msg in events:
        msg.time = tick - last
        last = tick
        track.append(msg)
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.save(str(path))


def build_script(onsets: list[Onset], keymap: KeyMap, mode: str, hold_ms: int = 20) -> dict:
    """Timed key groups. Naturals come first within an onset, then each modifier
    group, so a held Shift can never leak onto a natural note."""
    events = []
    for on in onsets:
        groups: dict[str | None, list[str]] = {}
        for n in on.notes:
            kp = keymap.press_for(n.pitch)
            groups.setdefault(kp.modifier, []).append(kp.key)
        ordered = []
        if None in groups:
            ordered.append({"mod": None, "keys": groups.pop(None)})
        ordered.extend({"mod": m, "keys": k} for m, k in groups.items())
        events.append({
            "t_ms": int(round(on.t * 1000)),
            "groups": ordered,
            "notes": [pitch_name(n.pitch) for n in on.notes],
        })
    return {
        "version": 1,
        "game": "Where Winds Meet",
        "instrument": "konghou",
        "mode": mode,
        "hold_ms": hold_ms,
        "keymap": keymap.to_json(),
        "events": events,
    }


def write_script(script: dict, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(script, fh, indent=1)
