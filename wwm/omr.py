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


def musicxml_to_midi(xml_paths: list[Path], midi_out: Path, bpm: float | None = None) -> tuple[int, Path]:
    """Concatenate pages in order, write MIDI; returns (note count, merged MusicXML path)."""
    from music21 import converter, stream, tempo

    scores = [converter.parse(str(p)) for p in xml_paths]
    score = scores[0]
    for extra in scores[1:]:  # pages: append measures of each part
        for part, extra_part in zip(score.parts, extra.parts):
            offset = part.highestTime
            for m in extra_part.getElementsByClass(stream.Measure):
                part.insert(offset + m.offset, m)
    try:
        expanded = score.expandRepeats()
        if expanded is not None:
            score = expanded
    except Exception:
        pass
    if bpm:
        for mm in list(score.recurse().getElementsByClass(tempo.MetronomeMark)):
            mm.activeSite.remove(mm)
        score.insert(0, tempo.MetronomeMark(number=bpm))
    note_count = sum(len(n.pitches) for n in score.recurse().notes)
    merged = midi_out.with_suffix(".musicxml")
    score.write("musicxml", fp=str(merged))
    score.write("midi", fp=str(midi_out))
    return note_count, merged


def recognise(score_path: str, midi_out: str | Path, engine: str = "auto", bpm: float | None = None) -> dict:
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
