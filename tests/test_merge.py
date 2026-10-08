import random

from wwm.arrange import Note
from wwm.merge import merge


def truth_piece():
    notes = []
    t = 0.0
    melody = [72, 74, 76, 77, 79, 81, 83, 84, 83, 81, 79, 77, 76, 74, 72, 74, 76, 77, 79, 81]
    for i, m in enumerate(melody * 2):
        notes.append(Note(t, m, 90, 0.4))
        notes.append(Note(t, 48 + (i % 4) * 2, 80, 0.4))
        t += 0.5
    return notes


def test_merge_repairs_wrong_and_missing_notes():
    truth = truth_piece()
    score = []
    for n in truth:
        if 4.0 <= n.t < 6.0 and n.pitch > 60:  # misread melody pitches
            score.append(Note(n.t, n.pitch - 3, n.vel, n.dur))
        elif abs(n.t - 10.0) < 1e-6:  # a chord the OMR dropped entirely
            continue
        else:
            score.append(n)
    rnd = random.Random(0)
    recording = [Note(0.7 + n.t * 1.08 + rnd.uniform(-0.01, 0.01), n.pitch, n.vel, n.dur) for n in truth]
    recording += [Note(3.3, 100, 12, 0.05), Note(12.1, 30, 15, 0.05)]  # transcription ghosts, low velocity

    merged, rep = merge(score, recording)
    assert rep.anchors > 30
    assert len(rep.patches) >= 2
    truth_set = {(round(n.t, 1), n.pitch) for n in truth}
    merged_set = {(round(n.t, 1), n.pitch) for n in merged}
    assert len(truth_set & merged_set) / len(truth_set) >= 0.97
    assert all(n.pitch not in (100, 30) for n in merged)  # ghosts filtered by velocity


def test_merge_keeps_consistent_score_untouched():
    truth = truth_piece()
    recording = [Note(1.0 + n.t * 0.95, n.pitch, n.vel, n.dur) for n in truth]
    merged, rep = merge(truth, recording)
    assert rep.patches == []
    assert sorted((round(n.t, 3), n.pitch) for n in merged) == sorted((round(n.t, 3), n.pitch) for n in truth)
