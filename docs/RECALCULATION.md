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
  newStart    = round(oldStart    * newBpm / oldBpm)
  newDuration = round(oldDuration * newBpm / oldBpm)   (min 1)
  ```
- `timeMs` is NOT stored, NOT used for recalc.

### Breaks
- Source of truth: `startBeat` and `endBeat` (from `.txt`)
- On BPM change: same proportional formula as notes.
- `timeMs` / `endTimeMs` are NOT used for recalc.

### Flags
- Source of truth: `timeMs` in localStorage  
  (flags are waveform markers, not grid items — they have no beat in the `.txt`)
- On BPM change:
  ```
  newBeat = round((flag.timeMs - gapMs) * newBpm / 15000)
  ```
  (`normalizeFlag` already does this via `msToBeat`)

### GAP
- Source of truth: `gapMs` (ms value — it is an audio position, not a beat)
- On BPM change: snap `gapMs` to the nearest beat of the new grid:
  ```
  beatDur = 15000 / newBpm
  gapMs   = round_to_nearest_multiple_of(beatDur, relative_to_downbeatOffsetMs)
  ```

### Metronome anchor (`#METRONOMEANCHOR`)
- Source of truth: beat (stored in `.txt` as beat)
- On BPM change: same proportional formula as notes:
  ```
  newBeat = oldBeat × newBpm / oldBpm
  ```
  (no rounding — fractional beat position is fine for the metronome)

---

## What does NOT change on BPM change

- `downbeatOffsetMs` (`#DOWNBEATOFFSET`) — stays in ms (absolute audio position of beat 1).
- `flag.timeMs` — stays in ms (source of truth for flags).
- `gapMs` (`#GAP`) — snapped to new grid but remains an ms value.

---

## Implementation: `resyncAllToGrid(previousTimingRef)`

Called after every BPM or GAP change.  
`previousTimingRef = { bpm: oldBpm, gapMs: oldGapMs }`

```
1. Snap GAP       → gapMs snapped to nearest beat of new BPM
2. Scale notes    → newBeat = round(oldBeat * newBpm / oldBpm)
3. Scale breaks   → same formula
4. Recalc flags   → msToBeat(flag.timeMs)  via normalizeFlag()
5. Recalc downbeat→ (downbeatOffsetMs - gapMs) * newBpm / 15000
```

Steps 2 and 3 only run when `rawTimings` is absent (txt-loaded songs).  
When `rawTimings` is present, `requantizeFromMs` handles notes/breaks instead.
