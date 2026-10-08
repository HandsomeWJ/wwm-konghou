import json

import mido
import pytest

from wwm.arrange import (
    ArrangeOptions, Note, Onset, arrange, choose_transposition, cluster_onsets,
    fold, limit_polyphony, load_midi,
)
from wwm.calib import calibration_notes
from wwm.export import build_script, flatten, write_midi, write_script
from wwm.keymap import NOTE_MAX, NOTE_MIN, KeyMap, KeyPress
from wwm.preview import render


def notes_at(pitches, t=0.0, vel=90):
    return [Note(t, p, vel, 0.1) for p in pitches]


# --- keymap ---------------------------------------------------------------

def test_keymap_rows_and_sharps():
    km = KeyMap.load()
    assert km.press_for(48) == KeyPress("z")          # C3 bottom row
    assert km.press_for(60) == KeyPress("a")          # C4 middle row
    assert km.press_for(72) == KeyPress("q")          # C5 top row
    assert km.press_for(83) == KeyPress("u")          # B5
    assert km.press_for(61) == KeyPress("a", "lshift")  # C#4 = Shift+C
    assert km.press_for(70) == KeyPress("h", "lshift")  # A#4 = Shift+A
    assert km.press_for(65) == KeyPress("f")          # F4 has no sharp below it


def test_keymap_rejects_out_of_range():
    km = KeyMap.load()
    with pytest.raises(ValueError):
        km.press_for(47)
    with pytest.raises(ValueError):
        km.press_for(84)


def test_keymap_scancodes_cover_used_keys():
    km = KeyMap.load()
    codes = km.to_json()["scancodes"]
    assert codes["q"] == 0x10 and codes["a"] == 0x1E and codes["z"] == 0x2C and codes["lshift"] == 0x2A
    assert set(km.used_keys()) <= set(codes)


# --- fold / cluster / polyphony -----------------------------------------------

def test_fold_brings_everything_into_three_octaves():
    for p in range(0, 128):
        assert NOTE_MIN <= fold(p) <= NOTE_MAX
        assert (fold(p) - p) % 12 == 0
    assert fold(36) == 48 and fold(95) == 83 and fold(60) == 60


def test_cluster_groups_jittered_onsets():
    notes = [Note(0.000, 60), Note(0.010, 64), Note(0.025, 67), Note(0.200, 72)]
    onsets = cluster_onsets(notes, 0.030)
    assert [len(o.notes) for o in onsets] == [3, 1]
    assert onsets[0].t == 0.0 and onsets[1].t == 0.2


def test_limit_polyphony_keeps_melody_and_bass():
    chord = notes_at([79, 76, 72, 67, 60, 48])  # highest first
    kept = limit_polyphony(chord, 4)
    assert [n.pitch for n in kept] == [79, 76, 72, 48]
    assert limit_polyphony(chord, 1)[0].pitch == 79
    assert limit_polyphony(chord, 10) == chord


# --- transposition ------------------------------------------------------------

def test_transposition_prefers_no_shift_when_in_range():
    onsets = cluster_onsets(notes_at([60, 64, 67, 72]), 0.03)
    assert choose_transposition(onsets, ArrangeOptions()) == 0


def test_transposition_moves_low_song_up_by_octaves():
    low = [Note(i * 0.5, p) for i, p in enumerate([36, 40, 43, 48, 52])]  # C2..E3
    onsets = cluster_onsets(low, 0.03)
    assert choose_transposition(onsets, ArrangeOptions()) == 12


def test_21_key_mode_transposes_to_c_major():
    # D major scale: D E F# G A B C#  -> in 21-key mode shifting by -2 removes all accidentals
    d_major = [Note(i * 0.5, p) for i, p in enumerate([62, 64, 66, 67, 69, 71, 73, 74])]
    onsets = cluster_onsets(d_major, 0.03)
    shift = choose_transposition(onsets, ArrangeOptions(mode="21"))
    assert shift in (-2, 10)
    assert all((p + shift) % 12 in {0, 2, 4, 5, 7, 9, 11} for p in [62, 64, 66, 67, 69, 71, 73, 74])


def test_21_key_mode_prefers_moving_key_over_wrong_notes():
    # G major melody over a low bass: shifting -7 removes the F# at the cost of folding bass octaves
    rh = [Note(i * 0.5, p) for i, p in enumerate([74, 79, 78, 79, 81, 83, 81, 79, 78, 74])]
    lh = [Note(i * 1.0, p) for i, p in enumerate([43, 50, 55, 43, 47])]
    onsets = cluster_onsets(rh + lh, 0.03)
    shift = choose_transposition(onsets, ArrangeOptions(mode="21"))
    assert shift != 0
    assert all((n.pitch + shift) % 12 in {0, 2, 4, 5, 7, 9, 11} for n in rh + lh)
    assert choose_transposition(onsets, ArrangeOptions(mode="36")) == 0


def test_36_key_mode_keeps_key_when_accidentals_are_cheap():
    d_major = [Note(i * 0.5, p) for i, p in enumerate([62, 64, 66, 67, 69, 71, 73, 74])]
    onsets = cluster_onsets(d_major, 0.03)
    assert choose_transposition(onsets, ArrangeOptions(mode="36")) == 0


# --- full arrange -------------------------------------------------------------

def test_arrange_snaps_or_drops_accidentals_in_21_mode():
    notes = notes_at([61, 63])  # C#4, D#4
    out, rep = arrange(notes, ArrangeOptions(mode="21", transpose=0, snap="down"))
    assert sorted(n.pitch for n in out[0].notes) == [60, 62]
    assert rep.snapped == 2
    out, rep = arrange(notes, ArrangeOptions(mode="21", transpose=0, snap="drop"))
    assert out == [] and rep.dropped_snap == 2


def test_arrange_dedupes_after_folding():
    notes = notes_at([48, 36])  # C3 and C2 -> both C3
    out, rep = arrange(notes, ArrangeOptions(transpose=0))
    assert [n.pitch for n in out[0].notes] == [48]
    assert rep.dropped_duplicate == 1 and rep.folded == 1


def test_arrange_enforces_retrigger_and_rebases_time():
    notes = [Note(1.0, 67), Note(1.02, 67), Note(1.05, 67), Note(1.10, 67)]
    out, rep = arrange(notes, ArrangeOptions(transpose=0, cluster_window=0.0, min_retrigger=0.040))
    assert [round(o.t, 3) for o in out] == [0.0, 0.05, 0.1]
    assert rep.dropped_retrigger == 1


def test_arrange_polyphony_report():
    notes = notes_at([79, 76, 72, 67, 60, 48])
    out, rep = arrange(notes, ArrangeOptions(transpose=0, max_voices=3))
    assert len(out[0].notes) == 3 and rep.dropped_polyphony == 3


# --- export ---------------------------------------------------------------------

def test_script_orders_naturals_before_shift_group():
    km = KeyMap.load()
    onsets = [Onset(0.0, notes_at([60, 64, 68]))]  # C4 E4 G#4
    script = build_script(onsets, km, "36")
    ev = script["events"][0]
    assert ev["t_ms"] == 0
    assert ev["groups"][0] == {"mod": None, "keys": ["a", "d"]}
    assert ev["groups"][1] == {"mod": "lshift", "keys": ["g"]}
    assert ev["notes"] == ["C4", "E4", "G#4"]
    assert script["keymap"]["scancodes"]["lshift"] == 0x2A


def test_midi_roundtrip_and_no_overlap(tmp_path):
    notes = [Note(0.0, 60, 90, 2.0), Note(0.1, 60, 90, 2.0), Note(0.1, 64, 90, 0.01), Note(0.5, 83, 90, 0.1)]
    path = tmp_path / "x.mid"
    write_midi(notes, path)
    back = load_midi(str(path))
    assert [(round(n.t, 3), n.pitch) for n in back] == [(0.0, 60), (0.1, 60), (0.1, 64), (0.5, 83)]
    assert all(n.dur <= 0.26 for n in back)
    assert back[0].dur <= 0.1  # cut short so the repeat at 0.1 s does not overlap
    mid = mido.MidiFile(str(path))
    assert mid.type == 0 and len(mid.tracks) == 1


def test_calibration_piece_arranges_losslessly(tmp_path):
    notes = calibration_notes()
    out, rep = arrange(notes, ArrangeOptions(transpose=0, max_voices=4))
    assert rep.transpose == 0 and rep.folded == 0 and rep.dropped_polyphony == 0
    assert rep.kept == len(notes)
    km = KeyMap.load()
    script = build_script(out, km, "36")
    write_script(script, tmp_path / "c.wwm.json")
    loaded = json.loads((tmp_path / "c.wwm.json").read_text())
    assert len(loaded["events"]) == len(out)
    assert loaded["events"][1]["groups"][0] == {"mod": "lshift", "keys": ["z"]}  # C#3


def test_preview_renders_wav(tmp_path):
    seconds = render(notes_at([60, 64, 67]) + [Note(0.5, 72)], tmp_path / "p.wav")
    assert 2.4 < seconds < 2.6
    assert (tmp_path / "p.wav").stat().st_size > 44


def test_load_midi_skips_drums_and_applies_tempo(tmp_path):
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack(); mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))  # 60 BPM: 480 ticks = 1 s
    tr.append(mido.Message("note_on", note=60, velocity=80, time=0))
    tr.append(mido.Message("note_on", note=36, velocity=80, channel=9, time=0))
    tr.append(mido.Message("note_off", note=60, velocity=0, time=480))
    tr.append(mido.Message("note_off", note=36, velocity=0, channel=9, time=0))
    tr.append(mido.Message("note_on", note=62, velocity=80, time=480))
    tr.append(mido.Message("note_off", note=62, velocity=0, time=240))
    mid.save(str(tmp_path / "t.mid"))
    notes = load_midi(str(tmp_path / "t.mid"))
    assert [(n.t, n.pitch, round(n.dur, 2)) for n in notes] == [(0.0, 60, 1.0), (2.0, 62, 0.5)]
