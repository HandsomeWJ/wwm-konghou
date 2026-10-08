from music21 import chord, clef, instrument, meter, note, stream

from wwm.arrange import Note
from wwm.melody import snap_to_recording, treble_skyline


def two_staff_score(path):
    s = stream.Score()
    rh, lh = stream.Part(id="rh"), stream.Part(id="lh")
    for p, c in ((rh, clef.TrebleClef()), (lh, clef.BassClef())):
        p.insert(0, instrument.Piano()); p.append(c); p.append(meter.TimeSignature("4/4"))
    for name in ("E5", "G5", "C6", "B5"):
        rh.append(chord.Chord([name, "C5", "G4"], quarterLength=1))  # melody on top of RH chords
    for name in ("C3", "G2", "A2", "G2"):
        lh.append(chord.Chord([name, "E3"], quarterLength=1))
    s.insert(0, rh); s.insert(0, lh)
    s.write("musicxml", fp=str(path))


def test_treble_skyline_takes_the_top_of_the_right_hand(tmp_path):
    xml = tmp_path / "s.musicxml"
    two_staff_score(xml)
    mel = treble_skyline(xml, {1: 120.0})
    assert [n.pitch for n in mel] == [76, 79, 84, 83]
    assert [round(n.t, 2) for n in mel] == [0.0, 0.5, 1.0, 1.5]


def test_snap_fixes_octave_and_keeps_confirmed():
    melody = [Note(0.0, 76), Note(0.5, 91), Note(1.0, 84), Note(1.5, 60)]  # 91 is an octave misread of 79
    recording = [Note(0.01, 76), Note(0.5, 79), Note(0.49, 67), Note(1.0, 84), Note(1.5, 81)]
    out, rep = snap_to_recording(melody, recording)
    assert [n.pitch for n in out] == [76, 79, 84, 60]
    assert (rep.confirmed, rep.octave_fixed, rep.replaced, rep.unconfirmed) == (2, 1, 0, 1)
