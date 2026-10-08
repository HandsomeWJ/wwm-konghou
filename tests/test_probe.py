import numpy as np

from wwm.arrange import Note
from wwm.keymap import KeyMap
from wwm.preview import render
from wwm.probe import probe_report, probe_script
from wwm.verify import SR, load_audio

# hypothetical game: Shift sharpens C F G, Ctrl flattens E B, other combos are silent
GAME = {(0, "lshift"): 1, (5, "lshift"): 1, (7, "lshift"): 1, (4, "lctrl"): -1, (11, "lctrl"): -1}


def test_probe_reads_modifier_table(tmp_path):
    km = KeyMap.load()
    script = probe_script(km, repeats=1)
    notes = []
    for ev in script["events"]:
        pr = ev["probe"]
        key, mod, nat = pr["key"], pr["mod"], pr["natural"]
        if mod is None:
            notes.append(Note(ev["t_ms"] / 1000, nat, 96, 0.3))
        elif (nat % 12, mod) in GAME:
            notes.append(Note(ev["t_ms"] / 1000, nat + GAME[(nat % 12, mod)], 96, 0.3))
        # else: silent
    wav = tmp_path / "probe.wav"
    render(notes, wav)
    y = np.concatenate([np.zeros(int(1.0 * SR), dtype=np.float32), load_audio(wav)])
    lines = probe_report(script, y)
    table = {tuple(l.split()[:2]): l for l in lines[2:]}
    assert "(+1 semitone)" in table[("a", "lshift")]   # key a = C4
    assert "(-1 semitone)" in table[("d", "lctrl")]    # key d = E4
    assert "silent" in table[("s", "lshift")]          # key s = D4: no D#
    assert "silent" in table[("h", "lctrl")]           # key h = A4: no A-flat
    assert "(natural)" in table[("s", "-")]
    assert "silent" in table[("x", "lshift")]          # low row D
    assert abs(float(lines[0].split()[2].rstrip("s")) - 1.0) < 0.06
