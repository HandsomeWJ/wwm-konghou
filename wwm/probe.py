"""Accidental probe: press every natural key with each modifier and record what the
game answers, to learn its black-key layout empirically."""
from __future__ import annotations

import numpy as np

from .keymap import KeyMap, pitch_name
from .verify import SR, analyse_onset, find_offset, onset_times, reference_level

NATURAL_PCS = (0, 2, 4, 5, 7, 9, 11)


def probe_script(keymap: KeyMap, repeats: int = 2, spacing: float = 0.7) -> dict:
    """Middle octave: each natural key alone, with Shift, with Ctrl; then the low and
    high octaves' Shift combos; `repeats` passes of everything."""
    events = []
    t = 0.0
    rows = [("mid", 60), ("low", 48), ("high", 72)]
    for _ in range(repeats):
        for row, base in rows:
            mods = [None, keymap.sharp_modifier, keymap.flat_modifier] if row == "mid" else [None, keymap.sharp_modifier]
            for degree, pc in enumerate(NATURAL_PCS):
                key = keymap.rows[row][degree]
                for mod in mods:
                    events.append({
                        "t_ms": int(round(t * 1000)),
                        "groups": [{"mod": mod, "keys": [key]}],
                        "notes": [pitch_name(base + pc)],  # the natural; the probe reports the offset from it
                        "probe": {"key": key, "mod": mod, "natural": base + pc},
                    })
                    t += spacing
            t += 0.5
    return {
        "version": 1,
        "game": "Where Winds Meet",
        "instrument": "konghou",
        "mode": "probe",
        "hold_ms": 20,
        "keymap": keymap.to_json(),
        "events": events,
    }


def probe_report(script: dict, y: np.ndarray, sr: int = SR, offset: float | None = None,
                 near: float | None = None) -> list[str]:
    events = script["events"]
    times = [e["t_ms"] / 1000.0 for e in events]
    off = offset if offset is not None else find_offset(y, sr, times, near=near)
    onsets = onset_times(y, sr)
    ref = reference_level(y, sr, [off + t for t in times])
    results: dict[tuple[str, str | None], list[str]] = {}
    for i, ev in enumerate(events):
        pr = ev["probe"]
        t = off + times[i]
        gap_next = times[i + 1] - times[i] if i + 1 < len(events) else 1.0
        length = float(min(0.35, max(0.08, gap_next - 0.02)))
        _, heard = analyse_onset(y, sr, t, length, 0.25, [pr["natural"]], ref_level=ref)
        onset_ok = bool(len(onsets)) and bool(np.min(np.abs(onsets - t)) <= 0.05)
        if heard is None:
            verdict = "silent" if not onset_ok else "onset, pitch unclear"
        else:
            delta = heard - pr["natural"]
            verdict = pitch_name(heard) + (f" ({delta:+d} semitone{'s' if abs(delta) != 1 else ''})" if delta else " (natural)")
        results.setdefault((pr["key"], pr["mod"]), []).append(verdict)
    lines = [f"script starts {off:.2f}s into the recording", f"{'key':<6}{'modifier':<10}answers"]
    for (key, mod), answers in results.items():
        lines.append(f"{key:<6}{(mod or '-'):<10}{' | '.join(answers)}")
    return lines
