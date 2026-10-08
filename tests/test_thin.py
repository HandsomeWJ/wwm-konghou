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
    melody = [Note(i * 0.125, 72 + (i % 5)) for i in range(40)]  # 8 notes/s
    acc = [Note(i * 0.125, 36 + (i % 7) * 2) for i in range(40)]  # 8 notes/s, never an octave below a melody note
    out, rep = thin(sorted(melody + acc, key=lambda n: (n.t, n.pitch)), melody, busy_gap=0.25)
    acc_out = sorted((n for n in out if n.pitch < 70), key=lambda n: n.t)
    assert rep.dropped_busy > 0
    assert all(b.t - a.t >= 0.25 - 1e-9 for a, b in zip(acc_out, acc_out[1:]))
