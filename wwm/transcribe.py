"""Solo-piano audio -> MIDI with ByteDance's piano transcription CRNN.

The model needs torch; install with `pip install -e '.[ml]'`. The checkpoint
(~165 MB) is fetched once into ~/piano_transcription_inference_data/.
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

CHECKPOINT_URL = (
    "https://zenodo.org/record/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1"
)
CHECKPOINT_DIR = Path.home() / "piano_transcription_inference_data"
CHECKPOINT = CHECKPOINT_DIR / "note_F1=0.9677_pedal_F1=0.9186.pth"
CHECKPOINT_BYTES = 170_000_000  # sanity floor for a complete download


def ensure_checkpoint() -> Path:
    if CHECKPOINT.exists() and CHECKPOINT.stat().st_size > CHECKPOINT_BYTES * 0.9:
        return CHECKPOINT
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT.with_suffix(".part")
    print(f"downloading piano transcription checkpoint to {CHECKPOINT} ...", file=sys.stderr)

    def progress(blocks: int, block_size: int, total: int) -> None:
        done = blocks * block_size
        if total > 0 and blocks % 200 == 0:
            print(f"  {done / 1e6:6.0f} / {total / 1e6:.0f} MB", file=sys.stderr)

    urllib.request.urlretrieve(CHECKPOINT_URL, tmp, reporthook=progress)
    os.replace(tmp, CHECKPOINT)
    return CHECKPOINT


def pick_device(requested: str) -> str:
    import torch

    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"  # the CRNN runs fine on CPU; MPS lacks some ops it uses


def load_audio(path: str, sr: int):
    """Mono float32 at the model's sample rate; any format ffmpeg can read (mp3, m4a,
    mp4, wav, flac)."""
    from .verify import load_audio as _load

    return _load(path, sr=sr)


def transcribe_file(audio_path: str, midi_out: str | Path, device: str = "auto") -> dict:
    from piano_transcription_inference import PianoTranscription, sample_rate

    checkpoint = ensure_checkpoint()
    dev = pick_device(device)
    audio = load_audio(audio_path, sample_rate)
    seconds = len(audio) / sample_rate
    print(f"transcribing {audio_path} ({seconds:.0f}s) on {dev} ...", file=sys.stderr)
    transcriptor = PianoTranscription(device=dev, checkpoint_path=str(checkpoint))
    result = transcriptor.transcribe(audio, str(midi_out))
    notes = result.get("est_note_events", [])
    return {"notes": len(notes), "seconds": seconds, "model": "bytedance-piano-crnn", "device": dev}
