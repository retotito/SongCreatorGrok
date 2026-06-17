# BPM / GAP Change — Recalculation Rules

## Core formula

```
time_ms = GAP + beat × 15000 / BPM
```

**Beat 0 is at GAP ms.** GAP defines the origin of the beat grid.  
The inverse (ms → beat) is:

```
beat = round((time_ms - GAP) × BPM / 15000)
```

When BPM changes, beat positions scale proportionally:

```
newBeat = round(oldBeat × newBpm / oldBpm)
```

The beat value stored in the `.txt` file is the **source of truth** for notes and breaks.  
We scale beats directly — no conversion to ms and back.

---

## Per item type

### Notes (regular)
- Source of truth: `startBeat` and `duration` (from `.txt`)
- On BPM change:
  ```
  newStart    = snapToGrid(round(oldStart    × newBpm / oldBpm))
  newDuration = round(oldDuration × newBpm / oldBpm)   (min 1)
  ```

### Breaks
- Source of truth: `startBeat` and `endBeat` (from `.txt`)
- On BPM change: identical formula to notes — both use `snapToGrid(round(...))`.

### Flags
- Source of truth: `timeMs` in localStorage
  (flags are waveform markers — they have no beat in the `.txt`)
- On BPM change:
  ```
  newBeat = round((flag.timeMs - gapMs) × newBpm / 15000)
  ```
  (`normalizeFlag` does this via `msToBeat`)

### GAP
- Source of truth: `gapMs` (ms value — audio position, beat 0 origin)
- On BPM change: **unchanged** — GAP stays at its audio position.
  The grid snaps to GAP, not the other way around.

  GAP only changes when the user explicitly edits the GAP field.

### Metronome anchor (`#METRONOMEANCHOR`)
- Source of truth: beat (stored in `.txt` as beat)
- On BPM change:
  ```
  newBeat = oldBeat × newBpm / oldBpm
  ```
  (no rounding — fractional beat is fine for the metronome)

---

## What does NOT change on BPM change

- `downbeatOffsetMs` (`#DOWNBEATOFFSET`) — stays in ms (absolute audio position of beat 1).
- `flag.timeMs` — stays in ms (source of truth for flags).
- `gapMs` (`#GAP`) — stays fixed (it is the grid origin, beat 0).

---

## Implementation: `resyncAllToGrid(previousTimingRef)`

Called after every BPM or GAP change.
`previousTimingRef = { bpm: oldBpm, gapMs: oldGapMs }`

```
1. GAP                 → unchanged (it is beat 0, the grid origin)
2. Scale notes         → snapToGrid(round(oldBeat × newBpm / oldBpm))
3. Scale breaks        → same formula as notes
4. Recalc flags        → msToBeat(flag.timeMs) via normalizeFlag()
5. Recalc downbeat     → (downbeatOffsetMs - gapMs) × newBpm / 15000
6. Scale metro anchor  → oldBeat × newBpm / oldBpm
```

Steps 2 and 3 only run when `rawTimings` is absent (txt-loaded songs).
When `rawTimings` is present, `requantizeFromMs` handles notes/breaks instead.

---

## Grid Offset Change (diamond drag)

When the user drags the diamond to set `downbeatOffsetMs`, the beat grid phase shifts.
After the drag commits:

```
1. Snap GAP  → newGapMs = downbeatOffsetMs + round((gapMs - downbeatOffsetMs) / beatDur) × beatDur
               (max shift = ½ beat — keeps GAP on the new grid)
2. Notes     → unchanged (beat values stay; distance to GAP preserved)
3. Breaks    → unchanged
4. Flags     → recalc beat from timeMs via normalizeFlag() (< ½ beat drift, acceptable)
5. Downbeat  → (downbeatOffsetMs - newGapMs) × bpm / 15000
6. Metro     → unchanged
```

The slight audio misalignment of notes is intentional — offset calibration is done
before fine-tuning note positions.
