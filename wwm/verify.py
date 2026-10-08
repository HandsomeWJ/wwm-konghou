"""Check a recording of the game against the script that was played.

For every onset in the script, the audio shortly after it is inspected for the
expected pitches (fundamental plus second harmonic against the neighbouring
semitones). No musical ear needed: the report says which notes sounded, which
did not, and what sounded instead.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .keymap import NOTE_MAX, NOTE_MIN, PITCH_NAMES, pitch_name

SR = 22050
ANALYSIS_START = 0.03  # seconds after the onset: skip the attack transient
ANALYSIS_LEN = 0.18
PRESENT_DB = 6.0  # a pitch counts as sounded when it beats its neighbours by this much


def load_audio(path: str | Path, sr: int = SR) -> np.ndarray:
    """Mono float32 at `sr` from any audio or video file (ffmpeg), wav/flac via soundfile."""
    path = Path(path)
    if path.suffix.lower() in (".wav", ".flac"):
        import soundfile as sf

        data, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
        y = data.mean(axis=1)
        if file_sr != sr:
            import librosa

            y = librosa.resample(y, orig_sr=file_sr, target_sr=sr)
        return y.astype(np.float32)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is needed to decode this file (brew install ffmpeg)")
    cmd = [ffmpeg, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def note_to_midi(name: str) -> int:
    pc = name.rstrip("-0123456789")
    octave = int(name[len(pc):])
    return PITCH_NAMES.index(pc) + (octave + 1) * 12


def _freq(pitch: int) -> float:
    return 440.0 * 2 ** ((pitch - 69) / 12)


def _band_energy(spec: np.ndarray, freqs: np.ndarray, f: float, width: float = 0.025) -> float:
    lo, hi = f * (1 - width), f * (1 + width)
    sel = (freqs >= lo) & (freqs <= hi)
    return float(spec[sel].max()) if sel.any() else 0.0


def _spectrum(y: np.ndarray, sr: int, start: float, length: float):
    a = max(0, int(start * sr))
    b = min(len(y), a + int(length * sr))
    if b - a < int(0.02 * sr):
        return None, None
    seg = y[a:b] * np.hanning(b - a)
    spec = np.abs(np.fft.rfft(seg, n=8192))
    return spec, np.fft.rfftfreq(8192, 1 / sr)


def _strength(spec, freqs, p: int) -> float:
    return _band_energy(spec, freqs, _freq(p)) + 0.5 * _band_energy(spec, freqs, 2 * _freq(p))


def _window_len(times: list[float], i: int) -> float:
    gap = times[i + 1] - times[i] if i + 1 < len(times) else 1.0
    return float(min(ANALYSIS_LEN, max(0.08, gap - 0.02)))


def onset_times(y: np.ndarray, sr: int) -> np.ndarray:
    import librosa

    return librosa.onset.onset_detect(y=y, sr=sr, hop_length=256, units="time", backtrack=False, delta=0.05)


def strength_profiles(y: np.ndarray, sr: int, t: float, length: float, before_len: float):
    """Per-pitch band strength just before and just after the onset at t."""
    before, fb = _spectrum(y, sr, t - before_len - 0.01, before_len)
    after, fa = _spectrum(y, sr, t + ANALYSIS_START, length)
    if before is None or after is None:
        return None, None
    pitches = range(NOTE_MIN - 1, NOTE_MAX + 2)
    return ({p: max(_strength(before, fb, p), 1e-9) for p in pitches},
            {p: max(_strength(after, fa, p), 1e-9) for p in pitches})


def heard_pitch(y: np.ndarray, sr: int, t: float, length: float) -> int | None:
    """Fundamental of a single, well-separated note after t by pYIN, which resolves
    semitones even at C3 where a short FFT cannot. None when nothing voiced is found."""
    import librosa

    a = max(0, int((t + ANALYSIS_START) * sr))
    b = min(len(y), a + int(length * sr))
    if b - a < int(0.05 * sr):
        return None
    seg = y[a:b]
    if np.max(np.abs(seg)) < 1e-4:
        return None
    f0, voiced, _ = librosa.pyin(seg, fmin=110.0, fmax=1100.0, sr=sr, frame_length=1024, hop_length=128)
    f0 = f0[voiced & np.isfinite(f0)]
    if len(f0) == 0:
        return None
    return int(round(69 + 12 * np.log2(np.median(f0) / 440.0)))


def single_note_check(y: np.ndarray, sr: int, t: float, expected: int, length: float, before_len: float,
                      repeat: bool, onsets: np.ndarray) -> tuple[bool, int | None]:
    """(present, heard). Slow passages use the pitch tracker. Fast passages use the
    spectrum across the onset: the expected pitch must rise by PRESENT_DB (it was
    struck, not merely ringing) and must not be dwarfed by a semitone neighbour. A
    repeat of the note just played cannot rise, so an onset near t is the evidence."""
    onset_ok = bool(len(onsets)) and bool(np.min(np.abs(onsets - t)) <= 0.035)
    if length >= 0.15 and before_len >= 0.11 and not repeat:
        heard = heard_pitch(y, sr, t, length)
        return heard == expected, heard
    before, after = strength_profiles(y, sr, t, length, before_len)
    if before is None:
        return False, None

    def rise(p: int) -> float:
        return 20 * np.log10(after[p] / before[p])

    own_rise = rise(expected)
    loud_neighbour = max(after[expected - 1], after[expected + 1])
    present = own_rise >= PRESENT_DB and 20 * np.log10(after[expected] / loud_neighbour) >= -3.0
    if repeat:
        # the pitch was struck < 0.3 s ago and still rings, so its spectrum cannot
        # rise cleanly (the new pluck may even cancel the ring); an onset near t is
        # the evidence, and a missed hit leaves no onset
        present = onset_ok
    struck = [p for p in range(NOTE_MIN, NOTE_MAX + 1) if rise(p) >= PRESENT_DB]
    heard = max(struck, key=lambda p: after[p]) if struck else None
    return present, heard


def chord_presence(y: np.ndarray, sr: int, t: float, length: float, pitches: list[int]) -> dict[int, bool]:
    """Each expected pitch must beat the quieter semitone neighbour by PRESENT_DB and
    not be dwarfed by the louder one (spectral leakage from a neighbour is not a hit)."""
    spec, freqs = _spectrum(y, sr, t + ANALYSIS_START, length)
    if spec is None:
        return {p: False for p in pitches}
    out = {}
    for p in pitches:
        own = max(_strength(spec, freqs, p), 1e-9)
        lo, hi = _strength(spec, freqs, p - 1), _strength(spec, freqs, p + 1)
        quiet, loud = max(min(lo, hi), 1e-9), max(max(lo, hi), 1e-9)
        out[p] = 20 * np.log10(own / quiet) >= PRESENT_DB and 20 * np.log10(own / loud) >= -3.0
    return out


def pitch_rise(y: np.ndarray, sr: int, t: float, p: int, length: float = ANALYSIS_LEN) -> float:
    """dB change of a pitch's energy from just before the onset to just after it."""
    before, fb = _spectrum(y, sr, t - 0.12, 0.11)
    after, fa = _spectrum(y, sr, t + ANALYSIS_START, length)
    if before is None or after is None:
        return 0.0
    return 20 * np.log10(max(_strength(after, fa, p), 1e-9) / max(_strength(before, fb, p), 1e-9))


def count_onsets(y: np.ndarray, sr: int, t0: float, t1: float) -> int:
    import librosa

    a, b = max(0, int(t0 * sr)), min(len(y), int(t1 * sr))
    if b <= a:
        return 0
    seg = y[a:b]
    times = librosa.onset.onset_detect(y=seg, sr=sr, hop_length=256, units="time", backtrack=False, delta=0.05)
    return int(len(times))


def onset_envelope(y: np.ndarray, sr: int, hop: int = 256) -> np.ndarray:
    import librosa

    return librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)


def find_offset(y: np.ndarray, sr: int, event_times: list[float], hop: int = 256) -> float:
    """Where in the recording the script starts: the shift that best lines up the
    recording's onset strength with the script's onset pattern."""
    env = onset_envelope(y, sr, hop)
    env = env / (env.max() + 1e-9)
    frames = len(env)
    pattern = np.zeros(frames)
    for t in event_times:
        k = int(round(t * sr / hop))
        if 0 <= k < frames:
            pattern[k] = 1.0
    kernel = np.exp(-0.5 * (np.arange(-3, 4) / 1.5) ** 2)
    pattern = np.convolve(pattern, kernel, mode="same")
    corr = np.correlate(env, pattern, mode="full")[frames - 1 :]  # non-negative shifts only
    span = int(event_times[-1] * sr / hop)
    corr = corr[: max(1, frames - span)]
    return float(np.argmax(corr) * hop / sr)


@dataclass
class EventResult:
    index: int
    t: float
    expected: list[int]
    missing: list[int]
    leaked: list[int]


@dataclass
class VerifyReport:
    offset: float = 0.0
    events: list[EventResult] = field(default_factory=list)
    onset_counts: dict[str, tuple[int, int]] = field(default_factory=dict)  # section -> (heard, expected)

    @property
    def expected_notes(self) -> int:
        return sum(len(e.expected) for e in self.events)

    @property
    def sounded_notes(self) -> int:
        return sum(len(e.expected) - len(e.missing) for e in self.events)


def verify(script: dict, y: np.ndarray, sr: int = SR, offset: float | None = None) -> VerifyReport:
    events = script["events"]
    times = [e["t_ms"] / 1000.0 for e in events]
    off = find_offset(y, sr, times) if offset is None else offset
    report = VerifyReport(offset=off)
    onsets = onset_times(y, sr)
    last_hit: dict[int, float] = {}  # pitch -> script time of its last single-note onset
    for i, ev in enumerate(events):
        expected = sorted(note_to_midi(n) for n in ev["notes"])
        t = off + times[i]
        length = _window_len(times, i)
        gap_prev = times[i] - times[i - 1] if i > 0 else 1.0
        before_len = float(min(0.11, max(0.04, gap_prev - 0.02)))
        leaked: list[int] = []
        if len(expected) == 1:
            repeat = times[i] - last_hit.get(expected[0], -9.0) < 0.3
            present, heard = single_note_check(y, sr, t, expected[0], length, before_len, repeat, onsets)
            missing = [] if present else list(expected)
            if missing and heard is not None and abs(heard - expected[0]) == 1:
                leaked = [heard]
            last_hit[expected[0]] = times[i]
        else:
            for p in expected:
                last_hit[p] = times[i]
            present_map = chord_presence(y, sr, t, length, expected)
            missing = [p for p in expected if not present_map[p]]
            # a loud, newly struck pitch a semitone from a missing note: Shift leaked onto it
            for q in missing:
                for p in (q - 1, q + 1):
                    if NOTE_MIN <= p <= NOTE_MAX and p not in expected:
                        if chord_presence(y, sr, t, length, [p])[p] and pitch_rise(y, sr, t, p, length) >= 3.0:
                            leaked.append(p)
        report.events.append(EventResult(i, times[i], expected, missing, sorted(set(leaked))))
    return report


CALIBRATION_SECTIONS = {"repeats": (40, 48), "run": (48, 63)}


def calibration_summary(report: VerifyReport, y: np.ndarray, sr: int = SR) -> list[str]:
    """Group the calibration piece's sections and turn misses into settings advice."""
    ev = report.events
    if len(ev) != 63:
        return []
    chrom, chords, mixed = ev[:36], ev[36:38], ev[38:40]
    lines = []
    sharps = [e for e in chrom if e.expected[0] % 12 in (1, 3, 6, 8, 10)]
    naturals = [e for e in chrom if e not in sharps]
    nat_ok = sum(not e.missing for e in naturals)
    sharp_ok = sum(not e.missing for e in sharps)
    lines.append(f"chromatic scale: naturals {nat_ok}/{len(naturals)}, sharps {sharp_ok}/{len(sharps)}")
    if nat_ok < len(naturals):
        bad = " ".join(pitch_name(e.expected[0]) for e in naturals if e.missing)
        lines.append(f"  naturals not heard: {bad} -> check the in-game key binds for those rows")
    if sharp_ok < len(sharps):
        bad = " ".join(pitch_name(e.expected[0]) for e in sharps if e.missing)
        lines.append(f"  sharps not heard: {bad} -> 36-key mode off, or try --modifier-settle-ms 40")
    chord_ok = sum(not e.missing for e in chords)
    lines.append(f"C major chord (4 keys at once): {chord_ok}/2 complete" + ("" if chord_ok == 2 else " -> lower --voices in wwm arrange"))
    mixed_ok = sum(not e.missing and not e.leaked for e in mixed)
    lines.append(f"C-E-G# chord (Shift must not leak): {mixed_ok}/2 clean" + ("" if mixed_ok == 2 else " -> raise --modifier-settle-ms"))
    for name, (a, b) in CALIBRATION_SECTIONS.items():
        t0 = report.offset + ev[a].t - 0.05
        t1 = report.offset + ev[b - 1].t + 0.12
        heard = count_onsets(y, sr, t0, t1)
        report.onset_counts[name] = (heard, b - a)
    rep, rep_n = report.onset_counts["repeats"]
    run, run_n = report.onset_counts["run"]
    lines.append(f"fast G4 repeats (100 ms apart): {rep}/{rep_n} onsets heard" + ("" if rep >= rep_n else " -> raise --retrigger-ms in wwm arrange"))
    lines.append(f"16th-note run (125 ms apart): {run}/{run_n} onsets heard" + ("" if run >= run_n else " -> try --speed 0.8"))
    return lines


def report_lines(report: VerifyReport, verbose: bool = False) -> list[str]:
    lines = [
        f"script starts {report.offset:.2f}s into the recording",
        f"notes heard: {report.sounded_notes}/{report.expected_notes} ({100 * report.sounded_notes / max(1, report.expected_notes):.0f}%)",
    ]
    for e in report.events:
        if e.missing or e.leaked or verbose:
            exp = " ".join(pitch_name(p) for p in e.expected)
            miss = " ".join(pitch_name(p) for p in e.missing) or "-"
            leak = " ".join(pitch_name(p) for p in e.leaked) or "-"
            lines.append(f"  {e.t:7.2f}s  expected {exp:<16} missing {miss:<12} leaked {leak}")
    return lines


def load_script(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
