"""Piano score (PDF, PNG, JPG) -> MusicXML -> MIDI.

Engines:
  auto       Audiveris for PDFs when installed, homr for images (default)
  audiveris  best on clean engraved scores; needs the Audiveris app (AUDIVERIS env
             var or ~/Applications/Audiveris.app)
  homr       transformer OMR built for photos and screenshots; `pip install homr`
The MusicXML is kept next to the MIDI so misreads can be fixed in MuseScore and
re-exported.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

AUDIVERIS_CANDIDATES = (
    os.environ.get("AUDIVERIS"),
    "~/Applications/Audiveris.app/Contents/MacOS/Audiveris",
    "/Applications/Audiveris.app/Contents/MacOS/Audiveris",
    shutil.which("audiveris"),
    shutil.which("Audiveris"),
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
MIN_WIDTH_PX = 2400  # screenshots are ~1000 px wide; OMR models expect ~300 dpi pages


def find_audiveris() -> str | None:
    for cand in AUDIVERIS_CANDIDATES:
        if cand and Path(cand).expanduser().exists():
            return str(Path(cand).expanduser())
    return None


def pdf_to_images(pdf: Path, workdir: Path, dpi: int = 300) -> list[Path]:
    import pymupdf as fitz

    pages = []
    with fitz.open(pdf) as doc:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
            out = workdir / f"{pdf.stem}-p{i + 1}.png"
            pix.save(out)
            pages.append(out)
    return pages


def prepare_image(image: Path, workdir: Path) -> Path:
    """Grayscale, upscale small screenshots, add a white margin."""
    from PIL import Image, ImageOps

    img = Image.open(image)
    img = ImageOps.exif_transpose(img).convert("L")
    if img.width < MIN_WIDTH_PX:
        scale = MIN_WIDTH_PX / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    pad = int(0.05 * img.width)
    img = ImageOps.expand(img, border=pad, fill=255)
    out = workdir / f"{image.stem}-prep.png"
    img.save(out, dpi=(300, 300))
    return out


def _find_musicxml(folder: Path) -> list[Path]:
    found = [p for ext in ("*.mxl", "*.musicxml", "*.xml") for p in folder.rglob(ext)]
    return sorted(set(found))


def run_audiveris(inputs: list[Path], workdir: Path) -> list[Path]:
    exe = find_audiveris()
    if not exe:
        raise RuntimeError("Audiveris not found: install the app or set AUDIVERIS=/path/to/Audiveris")
    cmd = [exe, "-batch", "-export", "-output", str(workdir), *map(str, inputs)]
    print("running:", " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Audiveris failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    found = _find_musicxml(workdir)
    if not found:
        raise RuntimeError(f"Audiveris produced no MusicXML in {workdir}:\n{proc.stdout[-2000:]}")
    return found


def run_homr(inputs: list[Path], workdir: Path) -> list[Path]:
    exe = shutil.which("homr") or str(Path(sys.executable).with_name("homr"))
    if not Path(exe).exists():
        raise RuntimeError("homr not found: pip install homr")
    outputs = []
    for img in inputs:
        cmd = [exe, str(img)]
        print("running:", " ".join(cmd), file=sys.stderr)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"homr failed on {img.name} ({proc.returncode}):\n{proc.stderr[-2000:]}")
        produced = [p for p in img.parent.glob(f"{img.stem}*.musicxml")]
        if not produced:
            raise RuntimeError(f"homr produced no MusicXML for {img.name}:\n{proc.stdout[-1000:]}")
        outputs.extend(produced)
    return outputs


def parse_tempo_map(spec: str | float | None) -> dict[int, float] | None:
    """'1:112.5,34:87,46:110' -> {measure: quarter BPM from that measure on}; a bare number is measure 1."""
    if spec is None:
        return None
    if isinstance(spec, (int, float)):
        return {1: float(spec)}
    out: dict[int, float] = {}
    for item in str(spec).split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            m, b = item.split(":", 1)
            out[int(m)] = float(b)
        else:
            out[1] = float(item)
    return out or None


def score_notes(score, tempo_map: dict[int, float] | None):
    """Flatten a music21 score to Note events in seconds without makeNotation (which
    chokes on OMR voice numbering). Tempo: the explicit map by measure number, else the
    score's metronome marks, else 120 quarter BPM."""
    from music21 import stream, tempo as m21tempo

    from .arrange import Note

    try:
        expanded = score.expandRepeats()
        if expanded is not None:
            score = expanded
    except Exception:
        pass

    # tempo segments as (offset in quarter lengths, quarter BPM)
    segments: list[tuple[float, float]] = []
    if tempo_map:
        measure_offsets: dict[int, float] = {}
        for part in score.parts or [score]:
            for m in part.getElementsByClass(stream.Measure):
                if m.number not in measure_offsets:
                    measure_offsets[m.number] = m.getOffsetInHierarchy(score)
        for measure, bpm in sorted(tempo_map.items()):
            off = measure_offsets.get(measure)
            if off is None:
                nearest = min(measure_offsets, key=lambda k: abs(k - measure), default=None)
                off = measure_offsets.get(nearest, 0.0) if nearest is not None else 0.0
            segments.append((off, bpm))
    else:
        for mm in score.recurse().getElementsByClass(m21tempo.MetronomeMark):
            q = mm.getQuarterBPM() if hasattr(mm, "getQuarterBPM") else mm.number
            if q:
                segments.append((mm.getOffsetInHierarchy(score), float(q)))
    segments.sort()
    if not segments or segments[0][0] > 0:
        segments.insert(0, (0.0, segments[0][1] if segments else 120.0))

    def seconds(ql: float) -> float:
        total = 0.0
        for i, (off, bpm) in enumerate(segments):
            nxt = segments[i + 1][0] if i + 1 < len(segments) else float("inf")
            if ql <= off:
                break
            total += (min(ql, nxt) - off) * 60.0 / bpm
        return total

    notes: list[Note] = []
    for el in score.recurse().notes:
        if el.isRest:
            continue
        start = el.getOffsetInHierarchy(score)
        t = seconds(start)
        dur = max(seconds(start + float(el.duration.quarterLength)) - t, 0.05)
        vel = el.volume.velocity if el.volume and el.volume.velocity else 80
        for p in el.pitches:
            notes.append(Note(t, int(p.midi), int(vel), dur))
    notes.sort(key=lambda n: (n.t, n.pitch))
    return notes


def measure_times(xml_path: Path, tempo_map: dict[int, float] | None) -> list[tuple[int, float]]:
    """(measure number, start time in seconds) for every measure, using the same tempo
    logic as score_notes, so merge/align reports can name measures."""
    from music21 import converter, stream

    from music21 import tempo as m21tempo

    score = converter.parse(str(xml_path))
    part = (score.parts or [score])[0]
    measures = list(part.getElementsByClass(stream.Measure))
    offsets = [(m.number, m.getOffsetInHierarchy(score)) for m in measures]

    segments: list[tuple[float, float]] = []
    if tempo_map:
        moff = {num: off for num, off in offsets}
        for measure, bpm in sorted(tempo_map.items()):
            off = moff.get(measure)
            if off is None and moff:
                off = moff[min(moff, key=lambda k: abs(k - measure))]
            segments.append((off or 0.0, bpm))
    else:
        for mm in score.recurse().getElementsByClass(m21tempo.MetronomeMark):
            q = mm.getQuarterBPM() if hasattr(mm, "getQuarterBPM") else mm.number
            if q:
                segments.append((mm.getOffsetInHierarchy(score), float(q)))
    segments.sort()
    if not segments or segments[0][0] > 0:
        segments.insert(0, (0.0, segments[0][1] if segments else 120.0))

    def seconds(ql: float) -> float:
        total = 0.0
        for i, (off, bpm) in enumerate(segments):
            nxt = segments[i + 1][0] if i + 1 < len(segments) else float("inf")
            if ql <= off:
                break
            total += (min(ql, nxt) - off) * 60.0 / bpm
        return total

    return [(num, seconds(off)) for num, off in offsets]


def _score_to_midi(score, midi_out: Path, tempo_map: dict[int, float] | None) -> int:
    from .export import write_midi

    notes = score_notes(score, tempo_map)
    write_midi(notes, midi_out, clamp=False)
    return len(notes)


def _concat_midis(parts: list[Path], midi_out: Path) -> int:
    """Join per-page MIDIs end to end, keeping pitches, velocities and durations."""
    from .arrange import Note, load_midi
    from .export import write_midi

    merged: list[Note] = []
    offset = 0.0
    for p in parts:
        notes = load_midi(str(p))
        merged.extend(Note(n.t + offset, n.pitch, n.vel, n.dur) for n in notes)
        if notes:
            offset += max(n.t + n.dur for n in notes)
    write_midi(merged, midi_out, clamp=False)
    return len(merged)


def _export_musicxml(score, src: Path, dest: Path) -> Path:
    """Write the fix-up MusicXML; if music21 cannot re-export the OMR output, keep the
    engine's own file (MuseScore opens .mxl and .musicxml alike)."""
    try:
        score.write("musicxml", fp=str(dest))
        return dest
    except Exception:
        fallback = dest.with_suffix(src.suffix)
        shutil.copyfile(src, fallback)
        return fallback


def musicxml_to_midi(xml_paths: list[Path], midi_out: Path, bpm: float | str | None = None) -> tuple[int, Path]:
    """Write MIDI from one or more MusicXML pages; returns (note count, MusicXML path for fixes).

    `bpm` is a quarter-note tempo or a tempo map 'measure:bpm,measure:bpm'. Several
    pages are merged into one score when music21 can; otherwise each page becomes a
    MIDI and the MIDIs are concatenated."""
    from music21 import converter, stream

    tempo_map = parse_tempo_map(bpm)
    if len(xml_paths) == 1:
        score = converter.parse(str(xml_paths[0]))
        fixup = _export_musicxml(score, xml_paths[0], midi_out.with_suffix(".musicxml"))
        return _score_to_midi(score, midi_out, tempo_map), fixup

    scores = [converter.parse(str(p)) for p in xml_paths]
    try:
        score = scores[0]
        for extra in scores[1:]:
            for part, extra_part in zip(score.parts, extra.parts):
                offset = part.highestTime
                for m in extra_part.getElementsByClass(stream.Measure):
                    part.insert(offset + m.offset, m)
        count = _score_to_midi(score, midi_out, tempo_map)
        fixup = _export_musicxml(score, xml_paths[0], midi_out.with_suffix(".musicxml"))
        return count, fixup
    except Exception as exc:  # fall back to page-wise MIDI
        print(f"page merge failed ({type(exc).__name__}); concatenating per-page MIDI", file=sys.stderr)
        page_mids = []
        for i, sc in enumerate(scores):
            pm = midi_out.with_name(f"{midi_out.stem}-page{i + 1}.mid")
            _score_to_midi(sc, pm, tempo_map)
            page_mids.append(pm)
        return _concat_midis(page_mids, midi_out), xml_paths[0]


def recognise(score_path: str, midi_out: str | Path, engine: str = "auto", bpm: float | str | None = None) -> dict:
    src = Path(score_path)
    midi_out = Path(midi_out)
    workdir = midi_out.parent / f"{midi_out.stem}.omr"
    workdir.mkdir(parents=True, exist_ok=True)
    if engine == "auto":
        # measured on the rendered fixture: Audiveris 36/36 on the PDF but 33/36 on the
        # 910 px screenshot, homr 36/36 on both; homr is slower on multi-page PDFs
        engine = "audiveris" if src.suffix.lower() == ".pdf" and find_audiveris() else "homr"

    if src.suffix.lower() == ".pdf":
        inputs = [src] if engine == "audiveris" else pdf_to_images(src, workdir)
    elif src.suffix.lower() in IMAGE_SUFFIXES:
        inputs = [prepare_image(src, workdir)]
    else:
        raise ValueError(f"unsupported score format {src.suffix}; use PDF, PNG or JPG")

    if engine == "audiveris":
        xmls = run_audiveris(inputs, workdir)
    elif engine == "homr":
        xmls = run_homr(inputs, workdir)
    else:
        raise ValueError(f"unknown engine {engine}")
    notes, merged = musicxml_to_midi(xmls, midi_out, bpm=bpm)
    return {"notes": notes, "musicxml": str(merged), "engine": engine, "pages": len(inputs)}
