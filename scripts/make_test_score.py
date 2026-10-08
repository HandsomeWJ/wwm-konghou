"""Build a small two-hand piano score with a known answer, render it with MuseScore
to PDF and a screenshot-like PNG, and save the ground-truth MIDI. Used to measure OMR."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from music21 import chord, key, meter, note, stream, tempo, clef, instrument

MSCORE = "/Applications/MuseScore 4.app/Contents/MacOS/mscore"


def build() -> stream.Score:
    s = stream.Score()
    rh = stream.Part(id="rh")
    lh = stream.Part(id="lh")
    for p, c in ((rh, clef.TrebleClef()), (lh, clef.BassClef())):
        p.insert(0, instrument.Piano())
        p.append(c)
        p.append(key.Key("G"))
        p.append(meter.TimeSignature("4/4"))
    rh.insert(0, tempo.MetronomeMark(number=96))
    melody = [
        ("D5", 1), ("G5", 1), ("F#5", 0.5), ("G5", 0.5), ("A5", 1),
        ("B5", 1), ("A5", 0.5), ("G5", 0.5), ("F#5", 1), ("D5", 1),
        ("E5", 1), ("C5", 1), ("D5", 1), ("B4", 1),
        ("A4", 0.5), ("B4", 0.5), ("C5", 0.5), ("D5", 0.5), ("G4", 2),
    ]
    for name, ql in melody:
        rh.append(note.Note(name, quarterLength=ql))
    bass = [
        (["G2", "D3"], 2), (["G2", "B3"], 2),
        (["D3", "A3"], 2), (["D3", "F#3"], 2),
        (["C3", "G3"], 2), (["G2", "D3"], 2),
        (["D3", "A3"], 2), (["G2", "D3", "G3"], 2),
    ]
    for names, ql in bass:
        lh.append(chord.Chord(names, quarterLength=ql))
    s.insert(0, rh)
    s.insert(0, lh)
    return s.makeMeasures() if False else s


def main(out_dir: str = "out") -> None:
    out = Path(out_dir)
    out.mkdir(exist_ok=True)
    s = build()
    xml = out / "test_score.musicxml"
    s.write("musicxml", fp=str(xml))
    s.write("midi", fp=str(out / "test_score_truth.mid"))
    for target in ("test_score.pdf", "test_score.png"):
        cmd = [MSCORE, "-o", str(out / target), str(xml)]
        if target.endswith(".png"):
            cmd[1:1] = ["-r", "110"]  # ~screenshot resolution
        proc = subprocess.run(cmd, capture_output=True, text=True)
        # MuseScore 4 on macOS often aborts while shutting down after writing the file
        produced = list(out.glob(Path(target).stem + "*" + Path(target).suffix))
        if not produced:
            raise RuntimeError(f"MuseScore wrote nothing for {target} (rc {proc.returncode}):\n{proc.stderr[-1500:]}")
    print("wrote", xml, "and renders:", sorted(p.name for p in out.glob("test_score*")))


if __name__ == "__main__":
    main(*sys.argv[1:])
