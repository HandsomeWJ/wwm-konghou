"""Key layout for Where Winds Meet free-play instruments (Konghou profile).

Free play exposes three octaves, C3-B5 (MIDI 48-83), as three rows of seven
natural-note keys. In 36-key mode the sharps sit on Shift + the natural key
below them; Ctrl gives the enharmonic flats, which the arranger never needs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

NOTE_MIN = 48  # C3
NOTE_MAX = 83  # B5
ROW_NAMES = ("low", "mid", "high")  # C3-B3, C4-B4, C5-B5

NATURAL_DEGREE = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}  # C D E F G A B
SHARP_DEGREE = {1: 0, 3: 1, 6: 3, 8: 4, 10: 5}  # C# D# F# G# A# -> natural below
FLAT_DEGREE = {1: 1, 3: 2, 6: 4, 8: 5, 10: 6}  # Db Eb Gb Ab Bb -> natural above
SCALE_PCS = frozenset(NATURAL_DEGREE)
PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# DirectInput scan codes (set 1). Games that read raw input ignore virtual-key
# codes, so the player sends these.
SCANCODES = {
    "esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B, "-": 0x0C,
    "=": 0x0D, "backspace": 0x0E, "tab": 0x0F,
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15,
    "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19, "[": 0x1A, "]": 0x1B,
    "enter": 0x1C, "lctrl": 0x1D,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "j": 0x24, "k": 0x25, "l": 0x26, ";": 0x27, "'": 0x28, "`": 0x29,
    "lshift": 0x2A, "\\": 0x2B,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31,
    "m": 0x32, ",": 0x33, ".": 0x34, "/": 0x35, "rshift": 0x36,
    "lalt": 0x38, "space": 0x39, "capslock": 0x3A,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F, "f6": 0x40,
    "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44, "f11": 0x57, "f12": 0x58,
}

DEFAULT_KEYMAP = {
    "name": "konghou_default",
    "rows": {
        "low": ["z", "x", "c", "v", "b", "n", "m"],
        "mid": ["a", "s", "d", "f", "g", "h", "j"],
        "high": ["q", "w", "e", "r", "t", "y", "u"],
    },
    "sharp_modifier": "lshift",
    "flat_modifier": "lctrl",
    "accidental_style": "sharp",
}


def pitch_name(pitch: int) -> str:
    return f"{PITCH_NAMES[pitch % 12]}{pitch // 12 - 1}"


def in_range(pitch: int) -> bool:
    return NOTE_MIN <= pitch <= NOTE_MAX


@dataclass(frozen=True)
class KeyPress:
    key: str
    modifier: str | None = None


class KeyMap:
    def __init__(self, spec: dict):
        self.name: str = spec.get("name", "custom")
        self.rows: dict[str, list[str]] = {}
        for row in ROW_NAMES:
            keys = list(spec["rows"][row])
            if len(keys) != 7:
                raise ValueError(f"row {row!r} needs 7 keys (C D E F G A B), got {len(keys)}")
            self.rows[row] = keys
        self.sharp_modifier: str = spec.get("sharp_modifier", "lshift")
        self.flat_modifier: str = spec.get("flat_modifier", "lctrl")
        self.accidental_style: str = spec.get("accidental_style", "sharp")
        if self.accidental_style not in ("sharp", "flat"):
            raise ValueError("accidental_style must be 'sharp' or 'flat'")
        self.scancodes: dict[str, int] = dict(SCANCODES)
        self.scancodes.update(spec.get("scancodes", {}))
        missing = [k for k in self.used_keys() if k not in self.scancodes]
        if missing:
            raise ValueError(f"no scan code for keys: {missing}; add them under 'scancodes'")

    @classmethod
    def load(cls, path: str | Path | None = None) -> "KeyMap":
        if path is None:
            return cls(DEFAULT_KEYMAP)
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def used_keys(self) -> list[str]:
        keys = [k for row in ROW_NAMES for k in self.rows[row]]
        keys.append(self.sharp_modifier if self.accidental_style == "sharp" else self.flat_modifier)
        return keys

    def press_for(self, pitch: int) -> KeyPress:
        if not in_range(pitch):
            raise ValueError(f"pitch {pitch} ({pitch_name(pitch)}) outside playable C3-B5")
        row = self.rows[ROW_NAMES[(pitch - NOTE_MIN) // 12]]
        pc = pitch % 12
        if pc in NATURAL_DEGREE:
            return KeyPress(row[NATURAL_DEGREE[pc]])
        if self.accidental_style == "sharp":
            return KeyPress(row[SHARP_DEGREE[pc]], self.sharp_modifier)
        return KeyPress(row[FLAT_DEGREE[pc]], self.flat_modifier)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "rows": self.rows,
            "sharp_modifier": self.sharp_modifier,
            "flat_modifier": self.flat_modifier,
            "accidental_style": self.accidental_style,
            "scancodes": {k: self.scancodes[k] for k in self.used_keys()},
        }
