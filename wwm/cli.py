"""wwm command line: calib | arrange | preview | transcribe | omr."""
from __future__ import annotations

import json
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
@click.option("--segments/--no-segments", default=True, show_default=True, help="36-key: choose the transposition per key region to avoid chords that need Shift and Ctrl at once")
@click.option("--max-groups", default=2, show_default=True, help="36-key: a chord may need this many key groups (naturals, Shift, Ctrl); inner notes beyond that are dropped. 1 = never roll a chord")
@click.option("--preview/--no-preview", default=True, show_default=True, help="Also render a WAV to listen to")
def arrange_cmd(midi, out, mode, voices, transpose, keymap_path, snap, retrigger_ms, cluster_ms, min_velocity, hold_ms, segments, max_groups, preview) -> None:
    """Reduce a MIDI to the Konghou range and write .wwm.mid + .wwm.json (+ preview WAV)."""
    keymap = KeyMap.load(keymap_path)
    opts = ArrangeOptions(
        mode=mode, max_voices=voices, transpose=transpose, snap=snap,
        min_retrigger=retrigger_ms / 1000, cluster_window=cluster_ms / 1000, min_velocity=min_velocity,
        segment_transpose=segments, max_groups=max_groups,
    )
    notes = load_midi(midi)
    onsets, report = arrange(notes, opts, keymap)
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
@click.option("--bpm", default=None, help="Quarter-note tempo, or a map 'measure:bpm,...' (OMR never reads tempo marks; default 120)")
def omr(score: str, out: str | None, engine: str, bpm: str | None) -> None:
    """Read a PDF or image of a piano score and write MIDI (+ MusicXML for fixes)."""
    from .omr import recognise

    out_path = Path(out) if out else Path(score).with_suffix(".mid")
    info = recognise(score, out_path, engine=engine, bpm=bpm)
    click.echo(f"wrote {out_path} ({info['notes']} notes, {info['pages']} page(s), {info['engine']}); MusicXML at {info['musicxml']}")


@main.command()
@click.argument("musicxml", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", default=None, help="MIDI path (default: next to the MusicXML)")
@click.option("--bpm", default=None, help="Quarter-note tempo or a map 'measure:bpm,...' (a dotted-quarter 75 in 6/8 is 112.5)")
def xml2mid(musicxml: str, out: str | None, bpm: str | None) -> None:
    """Convert a MusicXML file (e.g. fixed in MuseScore) to MIDI."""
    from .omr import musicxml_to_midi

    out_path = Path(out) if out else Path(musicxml).with_suffix(".mid")
    notes, _ = musicxml_to_midi([Path(musicxml)], out_path, bpm=bpm)
    click.echo(f"wrote {out_path} ({notes} notes)")


@main.command()
@click.argument("score_midi", type=click.Path(exists=True, dir_okay=False))
@click.argument("recording_midi", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", default=None, help="Merged MIDI (default: <score>.merged.mid)")
@click.option("--min-jaccard", default=0.5, show_default=True, help="Chord agreement needed to anchor the two sources")
@click.option("--min-velocity", default=30, show_default=True, help="Ignore recording notes quieter than this (transcription ghosts)")
@click.option("--musicxml", default=None, type=click.Path(exists=True), help="Score MusicXML, to report measure numbers")
@click.option("--bpm", default=None, help="Tempo map used when the score MIDI was made, for the measure numbers")
def merge_cmd(score_midi, recording_midi, out, min_jaccard, min_velocity, musicxml, bpm) -> None:
    """Patch an OMR score MIDI with a transcription of a recording of the same piece."""
    from .export import write_midi
    from .merge import merge

    score = load_midi(score_midi)
    recording = load_midi(recording_midi)
    merged, report = merge(score, recording, min_jaccard=min_jaccard, min_velocity=min_velocity)
    out_path = Path(out) if out else Path(score_midi).with_suffix(".merged.mid")
    write_midi(merged, out_path, clamp=False)
    measure_of = None
    if musicxml:
        from .omr import measure_times, parse_tempo_map

        starts = measure_times(Path(musicxml), parse_tempo_map(bpm))

        def measure_of(t: float) -> int:
            num = starts[0][0]
            for number, start in starts:
                if start <= t + 1e-6:
                    num = number
            return num

    for line in report.lines(measure_of):
        click.echo(line)
    click.echo(f"wrote {out_path}")


main.add_command(merge_cmd, name="merge")


@main.command()
@click.argument("musicxml", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", default=None, help="Output MIDI (default: <score>.melody.mid)")
@click.option("--bpm", default=None, help="Tempo or 'measure:bpm,...' map, as used for the score MIDI")
@click.option("--staff", default=0, show_default=True, help="Which staff carries the melody (0 = treble)")
@click.option("--align", "align_midi", default=None, type=click.Path(exists=True), help="Full score or merged MIDI on the same timeline, used to align with the recording")
@click.option("--recording", "recording_midi", default=None, type=click.Path(exists=True), help="Transcription of the recording: moves the melody onto its timeline and checks every note")
def melody(musicxml, out, bpm, staff, align_midi, recording_midi) -> None:
    """Extract the melody (top line of one staff) from a score, optionally timed and checked against a recording."""
    from .export import write_midi
    from .melody import melody_from_score
    from .omr import parse_tempo_map

    align = load_midi(align_midi) if align_midi else None
    rec = load_midi(recording_midi) if recording_midi else None
    if (align is None) != (rec is None):
        raise click.ClickException("--align and --recording go together")
    notes, report, anchors = melody_from_score(musicxml, parse_tempo_map(bpm), align, rec, part_index=staff)
    out_path = Path(out) if out else Path(musicxml).with_suffix(".melody.mid")
    write_midi(notes, out_path, clamp=False)
    if report:
        for line in report.lines():
            click.echo(line)
        click.echo(f"aligned through {anchors} anchors")
    click.echo(f"wrote {out_path} ({len(notes)} notes, {notes[-1].t:.1f}s)")


@main.command()
@click.argument("score_midi", type=click.Path(exists=True, dir_okay=False))
@click.argument("recording_midi", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", default=None, help="Output MIDI (default: <score>.retimed.mid)")
def retime(score_midi, recording_midi, out) -> None:
    """Move a score (or merged) MIDI onto the recording's timeline: same notes, the pianist's tempo and rubato."""
    from .export import write_midi
    from .merge import retime as do_retime

    notes, anchors = do_retime(load_midi(score_midi), load_midi(recording_midi))
    out_path = Path(out) if out else Path(score_midi).with_suffix(".retimed.mid")
    write_midi(notes, out_path, clamp=False)
    click.echo(f"wrote {out_path} ({len(notes)} notes, {anchors} anchors, {notes[-1].t:.1f}s)")


@main.command("probe-script")
@click.option("-o", "--out", default="out/probe.wwm.json", show_default=True)
@click.option("--keymap", "keymap_path", default=None, type=click.Path(exists=True))
@click.option("--repeats", default=2, show_default=True)
def probe_script_cmd(out: str, keymap_path: str | None, repeats: int) -> None:
    """Write a script that presses every natural key alone, with Shift and with Ctrl."""
    from .probe import probe_script

    script = probe_script(KeyMap.load(keymap_path), repeats=repeats)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    write_script(script, out)
    click.echo(f"wrote {out} ({len(script['events'])} presses, {script['events'][-1]['t_ms'] / 1000:.0f}s)")


@main.command()
@click.argument("recording", type=click.Path(exists=True, dir_okay=False))
@click.argument("script", type=click.Path(exists=True, dir_okay=False))
@click.option("--offset", default=None, type=float)
def probe(recording: str, script: str, offset: float | None) -> None:
    """Read a recording of the probe script: which pitch each key+modifier produced."""
    from .probe import probe_report
    from .verify import load_audio, load_script

    data = load_script(script)
    y = load_audio(recording)
    sidecar = Path(recording + ".json")
    near = None
    if offset is None and sidecar.exists():
        near = float(json.loads(sidecar.read_text(encoding="utf-8")).get("offset_s", 0.0))
    for line in probe_report(data, y, offset=offset, near=near):
        click.echo(line)


@main.command()
@click.argument("recording", type=click.Path(exists=True, dir_okay=False))
@click.argument("script", type=click.Path(exists=True, dir_okay=False))
@click.option("--offset", default=None, type=float, help="Seconds into the recording where the script starts (default: detected)")
@click.option("-v", "--verbose", is_flag=True, help="List every onset, not only the misses")
def verify(recording: str, script: str, offset: float | None, verbose: bool) -> None:
    """Check a game recording (mp4/mkv/mp3/wav) against the .wwm.json that was played."""
    from .verify import calibration_summary, load_audio, load_script, report_lines, verify as run_verify

    data = load_script(script)
    y = load_audio(recording)
    sidecar = Path(recording + ".json")
    near = None
    if offset is None and sidecar.exists():
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        near = float(meta.get("offset_s", 0.0))
        click.echo(f"recorder says the first key went out {near:.2f}s in; refining for audio latency")
    report = run_verify(data, y, offset=offset, near=near)
    for line in report_lines(report, verbose, y=y):
        click.echo(line)
    if len(report.events) > 200 and not verbose:
        win = 20.0
        click.echo("per 20 s window:")
        k = 0
        while k * win <= report.events[-1].t:
            evs = [e for e in report.events if k * win <= e.t < (k + 1) * win]
            exp = sum(len(e.expected) for e in evs)
            if exp:
                heard = exp - sum(len(e.missing) for e in evs)
                click.echo(f"  {k * win:4.0f}-{(k + 1) * win:4.0f}s  {100 * heard / exp:4.0f}%  ({heard}/{exp})")
            k += 1
    summary = calibration_summary(report, y)
    if summary:
        click.echo("calibration summary:")
        for line in summary:
            click.echo("  " + line)


if __name__ == "__main__":
    main()
