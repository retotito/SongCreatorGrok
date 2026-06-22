# Vocal Trace (Pink) vs Mic Lines (Green) — Design Reference

## Overview

Both features draw colored overlays on notes in the piano roll to show how well the pitch matches.  
They share the same coordinate system (`pitchToY(midiNote)`) but differ in audio source, recording strategy, and draw-time logic.

---

## Green Lines (Mic / Sing-Along)

**Audio source:** Live microphone via Web Audio API (`micAnalyser`)  
**Triggered:** Every rAF tick inside `sampleMicPitch()` while `micEnabled`  
**Operates:** Only inside a note window (`currentBeat` inside `note.startBeat … startBeat+duration`)

### Record pipeline (`sampleMicPitch`, line ~8616)
1. Read mic buffer from `micAnalyser`
2. Pitch detect → frequency → MIDI (`Math.round(12 * log2(f/440) + 69)`)
3. Rolling median over 5 samples (smoothing)
4. Sticky prediction: hold pitch if drift ≤ 2 semitones and confidence ≥ 4
5. Octave-correct toward `targetNote.pitch` (shift ±12 until diff ≤ 6)
6. Clamp to MIDI 36–84 (C2–C6)
7. **Apply `pitchTolerance` at record time → bake `isHit: boolean` into frame**
8. Append to `micNoteHits: Map<noteId, Array<{beat, sungPitch, isHit}>>`

### Draw pipeline
- Group consecutive same-`isHit` frames into filled rectangles
- **Hit:** draw at `pitchToY(note.pitch)` (green/gold/orange) — snapped to note row
- **Miss:** draw at `pitchToY(sungPitch)` — at actual sung pitch (octave-corrected)
- `pitchTolerance` NOT re-applied in draw (baked in at record time)

### Key properties
- `isHit` is immutable once recorded; changing `pitchTolerance` has no effect until next session
- Miss blocks appear at the correct pitch because `sungPitch` was already octave-corrected

---

## Pink Lines (Vocal Trace)

**Audio source:** Pre-recorded vocal WAV file (`vocalTraceDecodedBuffer`)  
**Triggered:** Fixed-grid deterministic loop in `updatePlayback()` — `sampleVocalTrace()` called once per `VOCAL_TRACE_STEP_SEC` (25ms) interval  
**Operates:** Only inside note windows (recording restricted to `note.startBeat … startBeat+duration`)

### Record pipeline (`sampleVocalTrace`, line ~8846)
1. Copy `channelData[sample window]` into `vocalTraceSampleBuf`
2. Pitch detect → frequency → MIDI
3. If unvoiced (low clarity or out of 60–2000 Hz): skip frame
4. Rolling median over 5 samples (smoothing)
5. **Octave-correct toward `note.pitch` (shift ±12 until diff ≤ 6)** — baked into `sungPitch`
6. Clamp to MIDI 36–84
7. **No `isHit` stored** — hit/miss is evaluated at draw time against current note state
8. Upsert by beat (replace existing frame within `VOCAL_TRACE_STEP_SEC * 0.5` tolerance — no duplicates on re-pass)
9. Append `{ beat, sungPitch }` to `vocalTraceFrames`

### Draw pipeline (canvas draw function, line ~2839)
- Iterate all `vocalTraceFrames`, skip frames outside visible range
- Per frame: look up which note (if any) covers `frame.beat`
- **First frame of each note** snaps its left edge to `beatToX(note.startBeat)` — closes the up-to-25ms gap caused by the fixed 25ms sampling grid
- **Hit** (`abs(frame.sungPitch - note.pitch) <= HARD_TOL` where `HARD_TOL = 1`):
  - Draw at **`pitchToY(note.pitch)`** — snapped to note row (Ultrastar style ✓)
  - Color: pink (normal) / gold (golden note) / orange-amber (rap note)
- **Miss** (no covering note, or diff > 1 semitone):
  - Draw at **`pitchToY(frame.sungPitch)`** — actual recorded pitch position
  - Color: orange `rgba(255, 140, 50, 0.45)`
- Frame width = `VOCAL_TRACE_STEP_SEC` converted to beats, capped to note right edge
- **`HARD_TOL` is re-evaluated at draw time** — changing note pitch immediately updates color/position without re-recording

---

## Resolved Issues

### ✅ Bug 1 — Pink hit detection was unreliable (fixed)
Octave correction at draw time was collapsing pitch distance before tolerance check. Fixed by baking `sungPitch` (octave-corrected) into the frame at record time and using a hard `HARD_TOL = 1` semitone at draw time.

### ✅ Bug 3 — Hit frames drawn at wrong Y height (fixed)
Hit frames were drawn at `pitchToY(frame.sungPitch)` instead of `pitchToY(note.pitch)`, placing them up to 1 semitone above/below the note row.  
Fixed: hits now snap to `pitchToY(note.pitch)` (Ultrastar style). Misses still draw at `pitchToY(frame.sungPitch)`.

### ✅ Timing offset — PitchLine dots appeared ~23ms early (fixed)
The blue (and green) pitch line dots were stored at `startSample / sampleRate` — the **start** of the 2048-sample FFT window (~46ms wide). The detected pitch actually represents the **center** of that window (+23ms).  
Fixed: beats now stored at `(startSample + fftSize/2) / sampleRate` in `_detectPitchFrames`.  
Applies to both the blue baseline line and the green recorded-patch line.

### ✅ VT first-block gap — pink block started up to 25ms after note start (fixed)
Because VT samples on a fixed 25ms grid, the first frame inside a note could appear up to 25ms after `note.startBeat`, leaving a visible gap at the note's left edge.  
Fixed: the first VT frame of each note snaps its draw X to `beatToX(note.startBeat)`.

### ✅ PitchLine Y snapping — dots snapped to integer semitone rows (fixed)
Stored `pitch` was `Math.round(midiFloat)`, causing dots to staircase on exact semitone rows.  
Fixed: `_detectPitchFrames` now also stores `pitchRaw` (float MIDI before rounding). Draw loops use `pitchRaw` for Y, giving a smooth continuous curve. Applies to both blue and green lines.

---

## Known/Open Issues

### Bug 2 — Orange miss blocks flicker (low priority)
Miss blocks appear for very short durations (single frame) at note boundaries.  
**Root cause:** `vtBeatGap` is recomputed each draw call from `VOCAL_TRACE_STEP_SEC * bpm / 15` — may be slightly off at low BPM.  
**Fix candidate:** Store as a derived constant; add a minimum frame width of 3px.

---

## Open Issues & Design Decisions

### Issue 1 — Duplicate frames on re-pass (long notes draw twice)

**Root cause:** `sampleVocalTrace()` always `push`es new frames. On a second pass, both old and new frames exist at the same beat positions. The draw loop processes both → visual overlap.

**Fix:** Upsert by beat in `sampleVocalTrace`. Before pushing, binary-search `vocalTraceFrames` for an existing frame within ±half a step and replace it in-place.

---

### Issue 2 — Moved note doesn't re-evaluate hit/miss

**Root cause:** `isHit` and `sungPitch` are baked at record time with the note position frozen. Moving a note makes the stored `isHit` stale. `sungPitch` is also octave-corrected toward the OLD note, so a large move (octave) would be wrong.

**Fix:** Stop baking `isHit`/`sungPitch`. Store only `{ beat, pitch }` (raw smoothed MIDI). Recalculate octave-correction and `isHit` at draw time against the **current** note pitch. This makes all evaluation live.

---

### Issue 3 — "Lines should stay at position but change color when note moves"

**Behavior requested:** If a note is at C and the trace shows a pink (hit) block, moving the note to D should leave the block visually at C but turn it orange (miss). Moving it back to C makes it pink again.

**Implication:** Hit blocks must NOT be drawn snapped to `noteY`. Both hits and misses draw at the actual detected pitch position (`pitchToY(correctedPitch)`). Color alone encodes hit/miss.

---

### Issue 4 — Pausing + editing: should lines persist?

**Requested behavior:** Yes — pink/orange lines should persist when paused and notes are edited. Editing a note changes whether existing frames are hits or misses, but doesn't delete any lines.

**Implementation:** Naturally follows from Issue 2 fix — draw-time recalculation means lines auto-update on every canvas redraw whenever notes change.

---

## Final Architecture (agreed 2026-06-22)

### X axis — absolute time, no note attachment
- Sample every 25ms (`VOCAL_TRACE_STEP_SEC`)
- **Only record when a note exists at that moment** (like mic — no frames in gaps between notes)
- Store `frame.beat` = beat number converted from ms timestamp (`timeToBeat(timeSec)`)
- Beat is equivalent to ms precision — it's just a unit conversion via BPM
- Draw: `x = beatToX(frame.beat)` — **fixed forever**, no note lookup for X position
- Moving/resizing/deleting a note after recording has **no effect on frame X positions**
- Zoom in/out: `beatToX()` scales naturally, all frames scale with the view

### Y axis — octave-corrected at record time, fixed forever
- At record time: find the note containing the current beat, octave-correct raw MIDI pitch toward that note's pitch (shift ±12 until `|pitch - note.pitch| <= 6`)
- Store `frame.sungPitch` = octave-corrected MIDI (e.g. C1, C2, C3 all collapse to the same C near the note)
- Draw: `y = pitchToY(frame.sungPitch)` — **fixed forever**, no note lookup for Y position
- Moving a note up/down has **no effect on frame Y positions**

### Color — only thing evaluated against current note state at draw time
- For each frame, find the note currently at `frame.beat` (if any)
- `isHit = note exists AND |frame.sungPitch - note.pitch| <= 1` (hard tolerance, ±1 semitone)
- **Hit** → pink (or gold for golden notes, orange for rap notes) — Ultrastar style
- **Miss** → orange — drawn at `pitchToY(frame.sungPitch)` (actual recorded pitch row)
- **No note at beat** (note was moved/deleted) → still draw orange — frame always renders
- Moving a note up/down: color updates on next redraw (sungPitch vs new note.pitch rechecked)
- Moving a note horizontally: frames no longer covered by that note → show orange (no note found)

### Ultrastar-style hit rendering
- **Hits** draw at `pitchToY(note.pitch)` — snapped to the note row, overlaid on top of the note rectangle at ~45% opacity
- **Misses** draw at `pitchToY(frame.sungPitch)` — float Y position (exact detected pitch, not snapped)
- The semi-transparent overlay lets the note color show through while clearly indicating hit/miss
- This is Ultrastar-style: the hit block sits exactly on the note, miss blocks float above or below it

### Frame positions are fixed at record time — no attachment to notes
- `frame.beat` and `frame.sungPitch` are written once at record time and never change
- Moving, resizing, or deleting a note has **no effect** on any frame's X or Y position
- Only the **color** re-evaluates at draw time (hit vs miss based on current note state)
- If the note moves away from a frame, the frame stays in place and turns orange (miss/no note)

### Constants
- `HARD_TOL = 1` semitone (not user-controlled)
- Frame width derived from `VOCAL_TRACE_STEP_SEC * bpm / 15` (beat gap → pixels)
- Frame height = `noteHeight` (same as note rectangles)
- Opacity: pink ~0.45, orange ~0.45 (drawn under note text layer)

