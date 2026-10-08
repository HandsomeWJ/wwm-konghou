"""Score a recognised/transcribed MIDI against a ground-truth MIDI.

Reports note-level precision/recall/F1 with an onset tolerance (default 80 ms after
aligning both files to their first onset and, optionally, rescaling tempo) and a
pitch-sequence similarity that ignores timing (useful for OMR, where the tempo is a guess).
"""
from __future__ import annotations

import argparse
import difflib
import sys

sys.path.insert(0, ".")
from wwm.arrange import load_midi  # noqa: E402


def align(notes):
    if not notes:
        return []
    t0 = notes[0].t
    return [(n.t - t0, n.pitch) for n in notes]


def f1(truth, pred, tol):
    used = [False] * len(pred)
    hits = 0
    for t, p in truth:
        for j, (t2, p2) in enumerate(pred):
            if not used[j] and p2 == p and abs(t2 - t) <= tol:
                used[j] = True
                hits += 1
                break
    prec = hits / len(pred) if pred else 0.0
    rec = hits / len(truth) if truth else 0.0
    f = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f


def pitch_similarity(truth, pred):
    a = [p for _, p in sorted(truth)]
    b = [p for _, p in sorted(pred)]
    return difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("truth")
    ap.add_argument("pred")
    ap.add_argument("--tol", type=float, default=0.08)
    ap.add_argument("--fit-tempo", action="store_true", help="rescale pred time so total durations match")
    args = ap.parse_args()
    truth = align(load_midi(args.truth))
    pred = align(load_midi(args.pred))
    if args.fit_tempo and truth and pred and pred[-1][0] > 0:
        k = truth[-1][0] / pred[-1][0]
        pred = [(t * k, p) for t, p in pred]
    prec, rec, f = f1(truth, pred, args.tol)
    print(f"truth {len(truth)} notes, pred {len(pred)} notes")
    print(f"onset+pitch  P {prec:.2f}  R {rec:.2f}  F1 {f:.2f}  (tol {args.tol * 1000:.0f} ms)")
    print(f"pitch sequence similarity {pitch_similarity(truth, pred):.2f}")


if __name__ == "__main__":
    main()
