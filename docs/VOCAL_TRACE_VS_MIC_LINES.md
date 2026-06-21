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
**Triggered:** Fixed-grid deterministic loop in `updatePlayback()` — `sampleVocalTrace()` called once per `VOCAL_TRACE_STEP_SEC` interval  
**Operates:** Any time `vocalTraceEnabled` is true and playback is active (not restricted to note windows)

### Record pipeline (`sampleVocalTrace`, line ~8846)
1. Copy `channelData[sample window]` into `vocalTraceSampleBuf`
2. Pitch detect → frequency → MIDI
3. If unvoiced (low clarity or out of 60–2000 Hz): clear rolling window, skip frame
4. Rolling median over 5 samples (smoothing)
5. Clamp to MIDI 36–84
6. **No `pitchTolerance` applied — no `isHit` stored**
7. Append `{ beat, pitch }` to `vocalTraceFrames`

### Draw pipeline (canvas draw function, line ~2839)
- For each note: binary-search `vocalTraceFrames` for frames in `[startBeat, endBeat]`
- Per frame: octave-correct `framePitch` toward `note.pitch` (same ±12 loop)
- **Apply `pitchTolerance` at draw time** → recomputed every frame on every draw call
- **Hit:** draw at `pitchToY(note.pitch)` (pink/gold/orange) — snapped to note row
- **Miss:** draw at `pitchToY(frame.pitch)` ← BUG: using raw un-octave-corrected pitch

---

## Current Bugs in Vocal Trace

### Bug 1 — Pink always shows as hit inside notes
The octave correction during the hit/miss check normalises the pitch difference to within 6 semitones, so almost any pitch in the vocal file passes `abs(corrected - note.pitch) <= pitchTolerance`.  
**Root cause:** Octave correction collapses the distance to ≤ 6 semitones before comparing against a tolerance of 1–3. For a voice that is an octave off, the corrected diff is 0 — perfect hit.  
**Fix candidate:** Only octave-correct if the voice is clearly within one octave (diff ≤ 8); if the voice pitch is genuinely far from the note, record a miss. Or alternatively, clamp octave correction to a single ±12 shift max.

### Bug 2 — Orange miss blocks flicker
Miss blocks appear for very short durations (single frame) at note boundaries, are sometimes suppressed by the edge-flicker filter, sometimes not, causing visible flicker on repeated draws.  
**Root cause:** `vtBeatGap` is recomputed each draw call from `frames[1].beat - frames[0].beat`, which can be unreliable if frames[0]/[1] are not from the same contiguous run. The threshold `max(0.7, vtBeatGap)` is inconsistent.  
**Fix candidate:** Store `vtBeatGap` as a constant derived from `VOCAL_TRACE_STEP_SEC` converted to beats, not re-derived from frame data.

### Bug 3 — Miss blocks drawn at wrong pitch height
Miss blocks use `pitchToY(frame.pitch)` where `frame.pitch` is the raw un-octave-corrected MIDI value. This can place miss blocks an octave above or below the note row.  
**Fix candidate:** Use the same octave-corrected `framePitch` that was computed for the hit/miss check when drawing the miss block position.

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

## Final Proposed Architecture

### Record (sampleVocalTrace)
- Store: `{ beat, pitch }` only — raw smoothed MIDI, no octave correction, no isHit
- **Upsert by beat** — replace existing frame at same beat position instead of appending

### Draw
- For each note, get frames in `[startBeat, endBeat]`
- Per frame: octave-correct `pitch` toward **current** `note.pitch` → `correctedPitch`
- `isHit = Math.abs(correctedPitch - note.pitch) <= HARD_TOLERANCE` (1 semitone)
- Draw at `pitchToY(correctedPitch)` for BOTH hit and miss (no snap to noteY)
- Hit → pink/gold/orange; Miss → orange
- Group consecutive same-color frames for efficiency

### Constants
- `HARD_TOLERANCE = 1` semitone (not user-controlled; represents recording accuracy not singing skill)
- `vtBeatGap` from `VOCAL_TRACE_STEP_SEC * bpm / 15` (not from frame deltas)

