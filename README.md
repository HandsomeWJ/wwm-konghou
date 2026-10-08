# wwm-konghou

Turns a piano MP3 or a piano score into a Konghou performance in **Where Winds Meet**.

```
MP3 ──► wwm transcribe ──┐
                         ├─► wwm arrange ──► song.wwm.json ──► wwm-play.exe (Windows) ──► game
PDF/PNG ──► wwm omr ─────┘        │
                                  └─► song.preview.wav (listen before you play)
```

The converter runs on the Mac (Python 3.12). The player is a single Windows exe
(Rust, cross-compiled here) that presses the keys with scan codes.

## Setup (Mac)

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev,ml,omr]'
```

`[ml]` pulls torch and ByteDance's piano transcription model (the 165 MB checkpoint
downloads on first use). `[omr]` pulls homr (transformer OMR) and PyMuPDF.
Audiveris (engraved-score OMR) lives in `~/Applications/Audiveris.app` (or set
`AUDIVERIS` to its binary). `wwm omr` picks Audiveris for PDFs and homr for
images unless you pass `--engine`.

## Workflow

```bash
# 1. get a MIDI
.venv/bin/wwm transcribe song.mp3 -o out/song.mid              # mp3 / wav / flac
.venv/bin/wwm omr score.pdf --bpm 90 -o out/song.mid           # pdf / png / jpg; --engine audiveris|homr

#    OMR never reads tempo marks: give the tempo, or a map by measure number
.venv/bin/wwm omr score.pdf --bpm "1:112.5,32:87,44:110" -o out/song.mid   # quarter BPM from each measure on
#    fixed a misread in MuseScore? export MusicXML and convert it:
.venv/bin/wwm xml2mid out/song.musicxml --bpm 112.5 -o out/song.mid   # bpm is per quarter note

# 2. reduce it to the Konghou's 3 octaves and write the key script
.venv/bin/wwm arrange out/song.mid --mode 36 --voices 4
#    -> out/song.wwm.mid   plain MIDI inside C3-B5 (any community MIDI player can play it)
#    -> out/song.wwm.json  key script for wwm-play.exe
#    -> out/song.preview.wav

# 3. on the Windows PC
wwm-play.exe song.wwm.json
#    open the Konghou in free play, then press F8. F8 pauses, F9 or Esc stops.
```

`wwm arrange` prints what it did: the transposition it chose, how many notes were
folded by an octave, how many accidentals it met and how many notes it dropped to
respect the voice limit or the per-key re-trigger gap.

### Rolled chords and key regions (36-key mode)

The player sends a chord as key groups: naturals, then Shift notes, then Ctrl
notes, about 50 ms apart. A chord that needs two groups rolls slightly; three
groups roll for 100 ms and sound messy. Keys with many flats (G♭ major has five)
produce such chords constantly. `wwm arrange` therefore:

- picks the transposition **per key region** (`--segments`, default on): a region
  is re-keyed only when it is full of multi-group chords, so a verse in G♭ may move
  to F while the G major chorus stays put. The report prints the regions.
- caps chords at `--max-groups 2` by dropping inner notes (melody and bass stay).
  `--max-groups 1` never rolls a chord at all, at the cost of chord colour.

### Following the recording's tempo

A score-based or merged MIDI runs at the printed tempo, metronomically. To make it
breathe like the pianist:

```bash
.venv/bin/wwm retime out/song.merged.mid out/song_from_mp3.mid -o out/song.final.mid
.venv/bin/wwm arrange out/song.final.mid
```

Every chord both sources agree on becomes a fixed point; times in between are
interpolated, so the result keeps the recording's tempo changes and rubato.

### Thinning the accompaniment

Dense piano accompaniment, especially repeated chords, piles up on a sustaining
instrument. `wwm thin` keeps the melody and prunes the rest with four rules:

- a string that would be plucked twice within 0.22 s is swapped, on the second
  pluck, for the nearest pitch sounding around it: a pianistic shimmer A B B B A E
  becomes A B A B A E, same harmony and rhythm, no stutter (`--no-refigure` to
  keep the literal notes);
- a pianistic figuration (upper register running at 4+ notes/s over 6 or fewer
  pitches) can additionally be thinned to every Nth onset (`--figuration-keep 2`);
  a slow melody riding on it keeps every note;
- under such a figuration the last bass chord is re-struck whenever the bass has
  been silent for 2.2 s, so the harmony keeps ringing;
- an accompaniment pitch is not struck again within 0.3 s (the previous strike
  still rings);
- while the melody runs fast, accompaniment onsets stay at least 0.25 s apart.

Ranges you already like can be protected:

```bash
.venv/bin/wwm thin out/song.final.mid --melody out/song.melody.mid --protect 25-111 -o out/song.thin.mid
.venv/bin/wwm arrange out/song.thin.mid --transpose-map "0-50:-1,50-999:0"   # keep the key regions of the earlier take
```

### Melody only

A sustaining harp turns dense piano accompaniment into a wash. For a clean
single line, take the top of the score's treble staff, move it onto the
recording's timeline and let the transcription check every note (exact matches
kept, octave misreads fixed, contradicted notes replaced by the recording's top
note nearby):

```bash
.venv/bin/wwm melody out/song.omr/song.mxl --bpm "1:112.5,32:87,44:110" \
    --align out/song.merged.mid --recording out/song_from_mp3.mid -o out/song.melody.mid
.venv/bin/wwm arrange out/song.melody.mid --voices 1
```

Add `--bass` for a sparse bass line under it: the bass staff's lowest note on the
strong beats only (beat 1, and the half-measure beat in even meters), each checked
against the recording the same way; `--bass-min-gap 2` thins it further. Arrange the
result with `--voices 2`.

### Modes

- `--mode 36` (default): naturals on the keys, sharps as Shift + the natural below.
  Needs the in-game 36-key setting.
- `--mode 21`: naturals only. The arranger searches for the transposition with the
  fewest accidentals and snaps the rest (`--snap down|up|drop`).

### Key map

`keymaps/konghou_default.json` is the default layout: Q–U = C5–B5, A–J = C4–B4,
Z–M = C3–B3. The five black keys are spelled the way the game answers them
(probed on 2026-10-09): **C♯ = Shift+C, E♭ = Ctrl+E, F♯ = Shift+F, G♯ = Shift+G,
B♭ = Ctrl+B**. Shift+D and Shift+A produce nothing in this game. `accidental_style`
can be `mixed` (that table), `sharp` (Shift on the natural below) or `flat` (Ctrl on
the natural above), and `accidentals` overrides single keys, e.g. `{"D#": "flat"}`.
If your binds differ, copy the file, edit it and pass `--keymap my.json`. Scan codes
for every key are built in.

### Calibration

```bash
.venv/bin/wwm calib                                   # examples/calibration_chromatic.mid
.venv/bin/wwm arrange examples/calibration_chromatic.mid -o out/calibration
```

Play `out/calibration.wwm.json` in game. It contains a chromatic scale C3→B5
(every key and every Shift combo), a C major chord twice, a C–E–G# chord twice
(Shift must not leak onto C and E), eight fast G4 repeats, then a scale run in 16ths.

You do not need to judge it by ear. The player can record what the PC plays while
the script runs (WASAPI loopback of the default output device; turn game music and
ambience down first so only the instrument is heard):

```bash
wwm-play.exe calibration.wwm.json --record calibration.wav
```

That writes `calibration.wav` and `calibration.wav.json` (the exact offset of the
first note). `--list-devices` shows the output devices, `--record-device "<name>"`
picks one that is not the default. Copy both files to the Mac and let the verifier
read them:

```bash
.venv/bin/wwm verify calibration.wav out/calibration.wwm.json
```

Any other recording works too (Xbox Game Bar, OBS; mp4/mkv/mp3/wav); without the
sidecar the verifier locates the script in the recording by its onset pattern.

It finds where the script starts in the recording, checks every onset for the
expected pitches (pYIN for slow notes, spectral rise and onset detection for fast
ones), and prints a summary that names the setting to change:

```
chromatic scale: naturals 21/21, sharps 15/15
C major chord (4 keys at once): 2/2 complete
C-E-G# chord (Shift must not leak): 2/2 clean
fast G4 repeats (100 ms apart): 8/8 onsets heard
16th-note run (125 ms apart): 15/15 onsets heard
```

`wwm verify` works for any script, not only the calibration: it reports the
percentage of notes heard and lists the misses. On the real Konghou (2026-10-09,
second calibration, settle 50 ms) it read 73/73: every natural, every sharp and
flat, both chords, all eight fast repeats and the whole run.

### Probing the accidental layout

If the calibration reports sharps that "produced nothing", the game maps those black
keys to a different combo (for example E♭ as Ctrl+E rather than D♯ as Shift+D). The
probe presses every natural key alone, with Shift and with Ctrl, and the reader
tells you what each combo produced:

```bash
.venv/bin/wwm probe-script -o out/probe.wwm.json       # on the Mac
wwm-play.exe probe.wwm.json --record probe.wav          # on the PC, about 70 s
.venv/bin/wwm probe probe.wav out/probe.wwm.json        # back on the Mac
```

Edit the keymap's accidental table from the answer and regenerate the scripts.

### Player options

```
wwm-play.exe song.wwm.json [--speed 1.0] [--hold-ms 20] [--modifier-settle-ms 50]
                           [--lead-in 3] [--start-at 30] [--window "Where Winds Meet"]
                           [--process wwm.exe] [--focus] [--background] [--no-guard]
                           [--record out.wav] [--record-device "<name>"] [--now] [--dry-run]
wwm-play.exe --list-windows | --list-devices
```

- Default (foreground) mode sends scan codes to whatever is in front, exactly like a
  keyboard. The player looks for the game window by title and **auto-pauses whenever
  the game is not in front**, so keys never land in a chat window; click back into
  the game to resume. `--no-guard` disables that.
- `--background` posts key messages straight to the game window, so the game can
  stay behind other windows (not minimised). This relies on the game reading window
  messages; run the calibration piece once in this mode to confirm sharps (Shift)
  still register.
- `--focus` brings the game window to the front before the lead-in.
- The game window is found by title (`--window`, default "Where Winds Meet") **or by
  process name**: `wwm.exe` and `yysls.exe` are matched automatically, so a Chinese
  window title is fine. `--process <name>` matches any other executable, and
  `--list-windows` prints every visible window with its process name.

- Dropped sharps → raise `--modifier-settle-ms` (50 passed the in-game calibration; 25 dropped a few).
- Missed fast repeats → raise `--retrigger-ms` in `wwm arrange` (40 → 60).
- Muddy chords → lower `--voices` (4 → 3).
- Run the exe as administrator if the game does not react at all.

## Build the player

```bash
export PATH="/opt/homebrew/opt/rustup/bin:/opt/homebrew/opt/llvm/bin:/opt/homebrew/opt/lld/bin:$HOME/.cargo/bin:$PATH"
cd player && cargo xwin build --release --target x86_64-pc-windows-msvc
# -> player/target/x86_64-pc-windows-msvc/release/wwm-play.exe
```

A native `cargo build` gives a Mac binary that only supports `--dry-run`.

## Tests

```bash
.venv/bin/python -m pytest
.venv/bin/python scripts/make_test_score.py          # renders a known score with MuseScore
.venv/bin/python scripts/compare_midi.py out/test_score_truth.mid out/test_score_homr.mid --fit-tempo
```

Measured on that fixture (G major, two hands, 36 notes): homr 36/36 on both the
910 px PNG and the PDF; Audiveris 36/36 on the PDF, 33/36 on the PNG.

When you have both a recording and a score of the same piece, align them to see
how much of the score the OMR got and where it went wrong:

```bash
.venv/bin/python scripts/align_midi.py out/song_from_mp3.mid out/song_from_score.mid
```

On a real 7-page scanned arrangement (6 flats then 1 sharp, 6/8 then 4/4, tuplets,
watermarked) against the ByteDance transcription of its recording: Audiveris
reproduced 87% of the notes exactly (91% ignoring octave) with 3 divergent
regions, homr 75% with 8. Per 20-second window Audiveris scored 90-95% in the
6/8 half and 69-86% in the tuplet-heavy 4/4 half. Removing the watermark before
OMR changed nothing for either engine. Neither engine reads tempo marks (this
piece has three), so pass `--bpm` with a measure map.

### Fixing score misreads

1. Tempo and meter: give `--bpm "measure:bpm,..."` using the measure numbers of the
   OMR output (open the MusicXML in MuseScore to see them; they can be one or two
   off from the print).
2. With a recording of the same arrangement, let the transcription patch the score:

   ```bash
   .venv/bin/wwm merge out/song_from_score.mid out/song_from_mp3.mid -o out/song.merged.mid \
       --musicxml out/song_from_score.omr/song.mxl --bpm "1:112.5,32:87,44:110"
   ```

   The score keeps its clean timeline. Chords that agree in both sources become
   anchors; everything between two anchors that disagrees (misread pitches, dropped
   notes, garbled tuplets) is replaced by the recording's notes, time-warped to fit.
   The report lists every patched region with measure numbers. On the test piece this
   took the score from 87% to 95% agreement with the recording, with no divergent
   region left.
3. Wrong or missing notes without a recording: open the `.musicxml` next to the MIDI
   in MuseScore, fix the measures that `scripts/align_midi.py` flags, export
   MusicXML, `wwm xml2mid`.

## Risk

Third-party input tools are against most games' terms of service. Community
reports mention warnings for Where Winds Meet but no confirmed bans for MIDI
players. Use at your own risk.
