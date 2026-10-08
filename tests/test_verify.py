import json

import numpy as np
import pytest

from wwm.arrange import ArrangeOptions, Note, arrange
from wwm.calib import calibration_notes
from wwm.export import build_script
from wwm.keymap import KeyMap
from wwm.preview import render
from wwm.verify import SR, calibration_summary, load_audio, verify


def make_script():
    onsets, _ = arrange(calibration_notes(), ArrangeOptions(transpose=0))
    return build_script(onsets, KeyMap.load(), "36")


def render_with_silence(notes, path, lead=2.3):
    render(notes, path)
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32")
    sf.write(str(path), np.concatenate([np.zeros(int(lead * sr), dtype=np.float32), data]), sr)


@pytest.fixture(scope="module")
def script():
    return make_script()


def test_clean_calibration_passes(tmp_path, script):
    wav = tmp_path / "clean.wav"
    render_with_silence(calibration_notes(), wav)
    y = load_audio(wav)
    rep = verify(script, y)
    assert abs(rep.offset - 2.3) < 0.05
    assert rep.sounded_notes == rep.expected_notes
    lines = calibration_summary(rep, y)
    assert "sharps 15/15" in lines[0]
    assert "4/4" not in lines[0]
    assert rep.onset_counts["repeats"][0] >= 8
    assert rep.onset_counts["run"][0] >= 15
    assert all("->" not in l for l in lines)


def test_missing_sharps_detected(tmp_path, script):
    # simulate Shift never registering: sharps play as the natural below
    notes = [Note(n.t, n.pitch - 1 if n.pitch % 12 in (1, 3, 6, 8, 10) else n.pitch, n.vel, n.dur) for n in calibration_notes()]
    wav = tmp_path / "nosharps.wav"
    render_with_silence(notes, wav)
    y = load_audio(wav)
    rep = verify(script, y)
    lines = calibration_summary(rep, y)
    assert "sharps 0/15" in lines[0]
    assert any("sharps that" in l for l in lines)
    assert any("C-E-G# chord" in l and "0/2" in l for l in lines)


def test_shift_leak_detected(tmp_path, script):
    # Shift leaking onto the naturals of the mixed chord: C#4 F4 G#4 instead of C4 E4 G#4
    notes = []
    for n in calibration_notes():
        if 20.9 <= n.t <= 22.1 and n.pitch in (60, 64):
            notes.append(Note(n.t, n.pitch + 1, n.vel, n.dur))
        else:
            notes.append(n)
    wav = tmp_path / "leak.wav"
    render_with_silence(notes, wav)
    y = load_audio(wav)
    rep = verify(script, y)
    mixed = rep.events[38:40]
    assert all(e.missing for e in mixed)
    assert any(61 in e.leaked or 65 in e.leaked for e in mixed)


def test_missed_repeats_detected(tmp_path, script):
    repeats = [n for n in calibration_notes() if n.dur == 0.05]  # the eight fast G4 hits
    dropped = {id(n) for n in repeats[1::2]}
    notes = [n for n in calibration_notes() if id(n) not in dropped] if False else [n for n in calibration_notes()]
    notes = [n for n in notes if not (n.dur == 0.05 and round((n.t - repeats[0].t) * 10) % 2 == 1)]
    wav = tmp_path / "repeats.wav"
    render_with_silence(notes, wav)
    y = load_audio(wav)
    rep = verify(script, y)
    lines = calibration_summary(rep, y)
    heard, expected = rep.onset_counts["repeats"]
    assert expected == 8 and heard <= 5
    assert any("raise --retrigger-ms" in l for l in lines)
