"""Check a recording of the game against the script that was played.

For every onset in the script, the spectrum shortly after it is searched for a peak
at each expected pitch; the peak must be loud, must stand above the noise and must
have risen across the onset (struck, not merely still ringing). Peaks, not band
energy, so the skirt of a loud neighbouring note never passes as the note itself.
No musical ear needed: the report says which notes sounded and what sounded instead.
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
ANALYSIS_START = 0.03   # seconds after the onset: skip the attack transient
NFFT = 32768
PEAK_TOL_CENTS = 45.0   # a note is "there" when a spectral peak sits within this of its pitch
REL_DB = 18.0           # ... and is within this of the loudest peak in the window (G#4's fundamental sits ~16 dB under its octave)
RISE_DB = 6.0           # ... and rose this much across the onset
BIG_RISE_DB = 15.0      # a rise this large is proof of a strike even for a quiet note
NOISE_DB = 12.0         # ... and stands this far above the window's median spectrum


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


def _cents(f: float, p: int) -> float:
    return 1200 * np.log2(f / _freq(p))


def _spectrum(y: np.ndarray, sr: int, start: float, length: float):
    a = max(0, int(start * sr))
    b = min(len(y), a + int(length * sr))
    if b - a < int(0.02 * sr):
        return None, None
    seg = y[a:b] * np.hanning(b - a)
    spec = 20 * np.log10(np.abs(np.fft.rfft(seg, n=NFFT)) + 1e-9)
    return spec, np.fft.rfftfreq(NFFT, 1 / sr)


def _peaks(spec: np.ndarray, freqs: np.ndarray, fmin: float = 100.0, fmax: float = 2200.0):
    """Local maxima of the dB spectrum between fmin and fmax as (freq, dB)."""
    lo = int(np.searchsorted(freqs, fmin))
    hi = int(np.searchsorted(freqs, fmax))
    if hi - lo < 3:
        return []
    s = spec[lo - 1 : hi + 1]
    mid = s[1:-1]
    mask = (mid > s[:-2]) & (mid >= s[2:])
    idx = np.where(mask)[0] + lo
    return [(float(freqs[i]), float(spec[i])) for i in idx]


def _band_max(spec, freqs, p: int, cents: float = PEAK_TOL_CENTS) -> float:
    if spec is None:
        return -200.0
    f = _freq(p)
    sel = (freqs >= f * 2 ** (-cents / 1200)) & (freqs <= f * 2 ** (cents / 1200))
    return float(spec[sel].max()) if sel.any() else -200.0


def reference_level(y: np.ndarray, sr: int, times: list[float]) -> float:
    """Loudest spectral peak found right after any onset of the script: what a note
    of this instrument sounds like at this recording level."""
    best = -200.0
    for t in times:
        spec, freqs = _spectrum(y, sr, t + ANALYSIS_START, 0.2)
        if spec is not None:
            peaks = _peaks(spec, freqs)
            if peaks:
                best = max(best, max(db for _, db in peaks))
    return best


ABS_FLOOR_DB = 28.0  # a real note is never this far below the loudest note of the take


def analyse_onset(y: np.ndarray, sr: int, t: float, length: float, before_len: float, pitches: list[int],
                  min_rise: dict[int, float] | None = None, ref_level: float | None = None):
    """(present per pitch, loudest newly struck playable pitch or None).

    `min_rise` lowers the required rise for pitches that were struck recently and
    still ring (a re-strike of a long-sustain note only holds its level). `ref_level`
    is the take's loudest note; peaks far below it are residue, not notes."""
    # the after-window may run longer than the before-window: some notes of this
    # instrument swell for half a second after the strike instead of jumping
    after, fa = _spectrum(y, sr, t + ANALYSIS_START, length)
    before, fb = _spectrum(y, sr, t - before_len - 0.01, before_len)
    if after is None:
        return {p: False for p in pitches}, None
    peaks = _peaks(after, fa)
    if not peaks:
        return {p: False for p in pitches}, None
    loudest = max(db for _, db in peaks)
    floor = float(np.median(after[(fa >= 100) & (fa <= 2200)])) + NOISE_DB
    if ref_level is not None:
        floor = max(floor, ref_level - ABS_FLOOR_DB)
    present = {}
    for p in pitches:
        cands = [db for f, db in peaks if abs(_cents(f, p)) <= PEAK_TOL_CENTS]
        if not cands:
            present[p] = False
            continue
        level = max(cands)
        rise = level - _band_max(before, fb, p)
        need = (min_rise or {}).get(p, RISE_DB)
        # a big jump out of silence is a strike whatever its loudness (some notes of
        # this instrument carry a weak fundamental); otherwise it must also be within
        # REL_DB of the loudest peak
        present[p] = level >= floor and rise >= need and (rise >= BIG_RISE_DB or level >= loudest - REL_DB)
    # "what sounded instead" compares equal-length windows on both sides of the onset,
    # so sidelobe differences between window lengths can never fake a rise
    w = min(length, before_len)
    after_eq, fe = _spectrum(y, sr, t + ANALYSIS_START, w)
    before_eq, fq = _spectrum(y, sr, t - w - 0.01, w)
    heard, best = None, -1e9
    if after_eq is not None:
        for f, db in _peaks(after_eq, fe):
            p = int(round(69 + 12 * np.log2(f / 440.0)))
            if NOTE_MIN <= p <= NOTE_MAX and abs(_cents(f, p)) <= PEAK_TOL_CENTS and db >= floor:
                if db - _band_max(before_eq, fq, p) >= RISE_DB and db > best:
                    best, heard = db, p
    return present, heard


def onset_times(y: np.ndarray, sr: int) -> np.ndarray:
    import librosa

    return librosa.onset.onset_detect(y=y, sr=sr, hop_length=256, units="time", backtrack=False, delta=0.05)


def count_onsets(y: np.ndarray, sr: int, t0: float, t1: float) -> int:
    import librosa

    a, b = max(0, int(t0 * sr)), min(len(y), int(t1 * sr))
    if b <= a:
        return 0
    return int(len(librosa.onset.onset_detect(y=y[a:b], sr=sr, hop_length=256, units="time", backtrack=False, delta=0.05)))


def onset_envelope(y: np.ndarray, sr: int, hop: int = 256) -> np.ndarray:
    import librosa

    return librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)


def find_offset(y: np.ndarray, sr: int, event_times: list[float], hop: int = 256,
                near: float | None = None, window: tuple[float, float] = (-0.1, 0.6)) -> float:
    """Where in the recording the script starts: the shift that best lines up the
    recording's onset strength with the script's onset pattern. With `near` (the
    recorder's own timestamp) only shifts within `window` of it are considered, which
    absorbs the game's input-to-sound latency without risking a wrong match."""
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
    best = float(np.argmax(corr) * hop / sr)
    if near is not None and not (near + window[0] <= best <= near + window[1]):
        # the recorder's timestamp is only wrong when the game was not in front when F8
        # was pressed (the guard paused); the onset pattern is the stronger evidence
        print(f"note: recorder said {near:.2f}s, onsets say {best:.2f}s; using the onsets", flush=True)
    return best


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


def verify(script: dict, y: np.ndarray, sr: int = SR, offset: float | None = None,
           near: float | None = None) -> VerifyReport:
    """`offset` pins the script start exactly; `near` (e.g. the recorder's sidecar
    value) seeds a local search that also absorbs the game's audio latency."""
    events = script["events"]
    times = [e["t_ms"] / 1000.0 for e in events]
    off = offset if offset is not None else find_offset(y, sr, times, near=near)
    report = VerifyReport(offset=off)
    onsets = onset_times(y, sr)
    ref = reference_level(y, sr, [off + t for t in times])
    last_hit: dict[int, float] = {}  # pitch -> script time it was last struck
    for i, ev in enumerate(events):
        expected = sorted(note_to_midi(n) for n in ev["notes"])
        t = off + times[i]
        gap_next = times[i + 1] - times[i] if i + 1 < len(events) else 1.0
        gap_prev = times[i] - times[i - 1] if i > 0 else 1.0
        length = float(min(0.6, max(0.08, gap_next - 0.02)))
        before_len = float(min(0.35, max(0.04, gap_prev - 0.02)))
        onset_ok = bool(len(onsets)) and bool(np.min(np.abs(onsets - t)) <= 0.08)
        # a pitch struck within the last 1.5 s still rings (this instrument sustains for
        # seconds and loses only ~2 dB over the analysis span): a re-strike only needs
        # to hold its level, a mere ring keeps falling
        recent = {p: 0.0 for p in expected if times[i] - last_hit.get(p, -9.0) < 1.5}
        present, heard = analyse_onset(y, sr, t, length, before_len, expected, min_rise=recent, ref_level=ref)
        fast = gap_next < 0.15 or gap_prev < 0.15
        missing = []
        for p in expected:
            if present[p]:
                continue
            if p in recent and times[i] - last_hit.get(p, -9.0) < 0.3:
                continue  # a repeat too quick to measure: the onset is the evidence
            if fast and onset_ok:
                continue  # fast passages: neighbouring semitones blur together; the onset counts
            missing.append(p)
        leaked = [heard] if (missing and heard is not None and heard not in expected
                             and any(abs(heard - q) == 1 for q in missing)) else []
        for p in expected:
            last_hit[p] = times[i]
        report.events.append(EventResult(i, times[i], expected, missing, leaked))
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
        silent = [pitch_name(e.expected[0]) for e in sharps if e.missing and not e.leaked]
        natural = [pitch_name(e.expected[0]) for e in sharps if e.missing and e.leaked]
        if silent:
            lines.append(f"  sharps that produced nothing: {' '.join(silent)} -> the game has no such Shift combo; run the probe")
        if natural:
            lines.append(f"  sharps that played the natural instead: {' '.join(natural)} -> Shift arrived late; try --modifier-settle-ms 50")
    chord_ok = sum(not e.missing for e in chords)
    lines.append(f"C major chord (4 keys at once): {chord_ok}/2 complete" + ("" if chord_ok == 2 else " -> lower --voices in wwm arrange"))
    mixed_ok = sum(not e.missing and not e.leaked for e in mixed)
    lines.append(f"C-E-G# chord (Shift must not leak): {mixed_ok}/2 clean" + ("" if mixed_ok == 2 else " -> raise --modifier-settle-ms"))
    for name, (a, b) in CALIBRATION_SECTIONS.items():
        t0 = report.offset + ev[a].t - 0.05
        t1 = report.offset + ev[b - 1].t + 0.12
        report.onset_counts[name] = (count_onsets(y, sr, t0, t1), b - a)
    rep, rep_n = report.onset_counts["repeats"]
    run, run_n = report.onset_counts["run"]
    lines.append(f"fast G4 repeats (100 ms apart): {rep}/{rep_n} onsets heard" + ("" if rep >= rep_n else " -> raise --retrigger-ms in wwm arrange"))
    lines.append(f"16th-note run (125 ms apart): {run}/{run_n} onsets heard" + ("" if run >= run_n else " -> try --speed 0.8"))
    return lines


def classify_misses(report: VerifyReport, y: np.ndarray, sr: int = SR) -> dict[str, int]:
    """Split the misses: a pitch whose band is below the take's floor right after its
    onset was really dropped; one that is loud because it was struck within the last
    1.5 s is sustaining and a re-strike cannot be told apart; the rest are sounding
    but masked by louder chord notes."""
    if not report.events:
        return {}
    times = [report.offset + e.t for e in report.events]
    floor = reference_level(y, sr, times) - ABS_FLOOR_DB
    counts = {"dropped (silent)": 0, "sustaining, re-strike unclear": 0, "sounding but masked": 0}
    last: dict[int, float] = {}
    for e in report.events:
        for p in e.missing:
            spec, fr = _spectrum(y, sr, report.offset + e.t + ANALYSIS_START, 0.3)
            level = _band_max(spec, fr, p) if spec is not None else -200.0
            if level < floor:
                counts["dropped (silent)"] += 1
            elif e.t - last.get(p, -9.0) < 1.5:
                counts["sustaining, re-strike unclear"] += 1
            else:
                counts["sounding but masked"] += 1
        for p in e.expected:
            last[p] = e.t
    return counts


def report_lines(report: VerifyReport, verbose: bool = False, y: np.ndarray | None = None, sr: int = SR) -> list[str]:
    lines = [
        f"script starts {report.offset:.2f}s into the recording",
        f"notes heard: {report.sounded_notes}/{report.expected_notes} ({100 * report.sounded_notes / max(1, report.expected_notes):.0f}%)",
    ]
    if y is not None and report.sounded_notes < report.expected_notes:
        counts = classify_misses(report, y, sr)
        lines.append("misses: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    many = len(report.events) > 200
    for e in report.events:
        if (e.missing or e.leaked or verbose) and (verbose or not many):
            exp = " ".join(pitch_name(p) for p in e.expected)
            miss = " ".join(pitch_name(p) for p in e.missing) or "-"
            leak = " ".join(pitch_name(p) for p in e.leaked) or "-"
            lines.append(f"  {e.t:7.2f}s  expected {exp:<16} missing {miss:<12} instead {leak}")
    return lines


def load_script(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
