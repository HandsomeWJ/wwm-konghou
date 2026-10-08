"""wwm command line: calib | arrange | preview | transcribe | omr."""
from __future__ import annotations

from pathlib import Path

import click

from . import __version__
from .arrange import ArrangeOptions, arrange, load_midi
from .export import build_script, flatten, write_midi, write_script
from .keymap import KeyMap
from .preview import render


@click.group()
@click.version_option(__version__)
def main() -> None:
    """Turn piano MP3s and scores into Konghou performances in Where Winds Meet."""


@main.command()
@click.option("-o", "--out", default="examples/calibration_chromatic.mid", show_default=True)
def calib(out: str) -> None:
    """Write the calibration MIDI (chromatic scale, chords, fast repeats, scale run)."""
    from .calib import calibration_notes

    notes = calibration_notes()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    write_midi(notes, out)
    click.echo(f"wrote {out} ({len(notes)} notes, {notes[-1].t:.1f}s)")


@main.command()
@click.argument("midi", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", default=None, help="Output stem (default: input name without extension)")
@click.option("--mode", type=click.Choice(["36", "21"]), default="36", show_default=True, help="36: sharps via Shift; 21: naturals only")
@click.option("--voices", default=4, show_default=True, help="Max simultaneous keys")
@click.option("--transpose", default=None, type=int, help="Semitones; omit to search automatically")
@click.option("--keymap", "keymap_path", default=None, type=click.Path(exists=True), help="Keymap JSON (default: built-in Konghou layout)")
@click.option("--snap", type=click.Choice(["down", "up", "drop"]), default="down", show_default=True, help="21-key: what to do with accidentals")
@click.option("--retrigger-ms", default=40, show_default=True, help="Min gap between repeats of one key")
@click.option("--cluster-ms", default=30, show_default=True, help="Onsets closer than this become one chord")
@click.option("--min-velocity", default=1, show_default=True, help="Drop notes quieter than this (transcription ghosts)")
@click.option("--hold-ms", default=20, show_default=True, help="How long the player holds each key")
@click.option("--preview/--no-preview", default=True, show_default=True, help="Also render a WAV to listen to")
def arrange_cmd(midi, out, mode, voices, transpose, keymap_path, snap, retrigger_ms, cluster_ms, min_velocity, hold_ms, preview) -> None:
    """Reduce a MIDI to the Konghou range and write .wwm.mid + .wwm.json (+ preview WAV)."""
    keymap = KeyMap.load(keymap_path)
    opts = ArrangeOptions(
        mode=mode, max_voices=voices, transpose=transpose, snap=snap,
        min_retrigger=retrigger_ms / 1000, cluster_window=cluster_ms / 1000, min_velocity=min_velocity,
    )
    notes = load_midi(midi)
    onsets, report = arrange(notes, opts)
    if not onsets:
        raise click.ClickException("no playable notes found")
    stem = Path(out) if out else Path(midi).with_suffix("")
    stem.parent.mkdir(parents=True, exist_ok=True)
    mid_path = stem.with_name(stem.name + ".wwm.mid")
    json_path = stem.with_name(stem.name + ".wwm.json")
    write_midi(flatten(onsets), mid_path)
    write_script(build_script(onsets, keymap, mode, hold_ms), json_path)
    for line in report.lines():
        click.echo(line)
    click.echo(f"wrote {mid_path}")
    click.echo(f"wrote {json_path}")
    if preview:
        wav_path = stem.with_name(stem.name + ".preview.wav")
        seconds = render(flatten(onsets), wav_path)
        click.echo(f"wrote {wav_path} ({seconds:.1f}s)")


main.add_command(arrange_cmd, name="arrange")


@main.command()
@click.argument("midi", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", default=None, help="WAV path (default: next to the MIDI)")
def preview(midi: str, out: str | None) -> None:
    """Render any MIDI (already arranged or not) to a plucked-string WAV."""
    notes = load_midi(midi)
    if not notes:
        raise click.ClickException("no notes in file")
    wav_path = Path(out) if out else Path(midi).with_suffix(".preview.wav")
    seconds = render(notes, wav_path)
    click.echo(f"wrote {wav_path} ({len(notes)} notes, {seconds:.1f}s)")


@main.command()
@click.argument("audio", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", default=None, help="MIDI path (default: next to the audio)")
@click.option("--device", default="auto", show_default=True, help="auto | cpu | mps | cuda")
def transcribe(audio: str, out: str | None, device: str) -> None:
    """Transcribe a solo-piano recording (mp3/wav/flac) to MIDI."""
    from .transcribe import transcribe_file

    out_path = Path(out) if out else Path(audio).with_suffix(".mid")
    info = transcribe_file(audio, out_path, device=device)
    click.echo(f"wrote {out_path} ({info['notes']} notes, {info['seconds']:.1f}s, model {info['model']})")


@main.command()
@click.argument("score", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", default=None, help="MIDI path (default: next to the score)")
@click.option("--engine", type=click.Choice(["auto", "audiveris", "homr"]), default="auto", show_default=True, help="auto = Audiveris for PDFs, homr for images")
@click.option("--bpm", default=None, type=float, help="Override the tempo (scores often carry none; default 120)")
def omr(score: str, out: str | None, engine: str, bpm: float | None) -> None:
    """Read a PDF or image of a piano score and write MIDI (+ MusicXML for fixes)."""
    from .omr import recognise

    out_path = Path(out) if out else Path(score).with_suffix(".mid")
    info = recognise(score, out_path, engine=engine, bpm=bpm)
    click.echo(f"wrote {out_path} ({info['notes']} notes, {info['pages']} page(s), {info['engine']}); MusicXML at {info['musicxml']}")


if __name__ == "__main__":
    main()
