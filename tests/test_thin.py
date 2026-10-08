from wwm.arrange import Note
from wwm.thin import thin


def test_thin_drops_repeated_chords_but_not_melody_or_moving_bass():
    melody = [Note(i * 0.5, 76 + (i % 3)) for i in range(8)]
    acc = []
    for i in range(8):  # triplet chord pulses: same C-E-G every 0.18 s
        for k in range(3):
            acc += [Note(i * 0.5 + k * 0.18, p) for p in (48, 52, 55)]
    acc[-3] = Note(acc[-3].t, 50)  # a moving bass note in the last pulse
    full = sorted(melody + acc, key=lambda n: (n.t, n.pitch))
    out, rep = thin(full, melody, repeat_window=0.3, busy_rate=99)
    mel_out = [n for n in out if n.pitch >= 76]
    assert len(mel_out) == 8
    acc_out = [n for n in out if n.pitch < 76]
    assert rep.dropped_repeats > 0
    assert any(n.pitch == 50 for n in acc_out)  # the moving bass survives
    # within any 0.3 s no accompaniment pitch is struck twice
    seen = {}
    for n in sorted(acc_out, key=lambda n: n.t):
        assert n.t - seen.get(n.pitch, -9) >= 0.3
        seen[n.pitch] = n.t


def test_thin_spaces_accompaniment_under_a_running_melody():
    melody = [Note(i * 0.125, 60 + (i * 5) % 24) for i in range(40)]  # 8 notes/s, wide-ranging: a run, not a figure
    acc = [Note(i * 0.125, 24 + (i % 7) * 2) for i in range(40)]  # 8 notes/s, two octaves under the melody
    out, rep = thin(sorted(melody + acc, key=lambda n: (n.t, n.pitch)), melody, busy_gap=0.25)
    acc_out = sorted((n for n in out if n.pitch < 70), key=lambda n: n.t)
    assert rep.dropped_busy > 0
    assert all(b.t - a.t >= 0.25 - 1e-9 for a, b in zip(acc_out, acc_out[1:]))


def test_figuration_is_halved_and_supported():
    # 12 s of A4 B4 B4 B4 A4 E4 at 6 notes/s as "melody", one held chord struck at t=0 only
    pattern = [69, 71, 71, 71, 69, 64]
    melody = [Note(i / 6.0, pattern[i % 6]) for i in range(72)]
    chord = [Note(0.0, p) for p in (40, 47, 52)]
    out, rep = thin(sorted(melody + chord, key=lambda n: (n.t, n.pitch)), melody, support_gap=2.2)
    mel_out = sorted((n for n in out if n.pitch >= 60), key=lambda n: n.t)
    assert 30 <= len(mel_out) <= 40 and rep.dropped_figuration >= 30
    gaps = [b.t - a.t for a, b in zip(mel_out, mel_out[1:])]
    assert max(gaps) < 0.4 and min(gaps) > 0.3  # regular: every other note
    assert rep.added_support >= 9  # the chord was re-struck about every 2.2 s


def test_figuration_rule_leaves_a_varied_melody_alone():
    melody = [Note(i / 6.0, 60 + (i * 5) % 24) for i in range(72)]  # fast but wide-ranging
    out, rep = thin(melody, melody)
    assert rep.dropped_figuration == 0 and len(out) == 72
