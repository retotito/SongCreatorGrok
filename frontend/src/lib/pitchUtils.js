/**
 * Pure pitch/note utility functions used by the piano roll editor.
 * No Svelte reactive state — safe to import anywhere.
 */

export function medianPitch(values) {
  if (!values || values.length === 0) return 60;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

export function groupPitchFrames(frames, maxGapBeat = 0.45, maxPitchJump = 2) {
  if (!frames.length) return [];
  const sorted = [...frames].sort((a, b) => a.beat - b.beat);
  const groups = [];
  let current = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1];
    const cur = sorted[i];
    const beatGap = cur.beat - prev.beat;
    const pitchJump = Math.abs(cur.pitch - prev.pitch);
    if (beatGap <= maxGapBeat && pitchJump <= maxPitchJump) {
      current.push(cur);
    } else {
      groups.push(current);
      current = [cur];
    }
  }
  groups.push(current);
  return groups;
}

export function mergePlaceholderNotes(inputNotes, maxGapBeat = 0.35, maxPitchJump = 3) {
  if (!inputNotes.length) return [];
  const sorted = [...inputNotes].sort((a, b) => a.startBeat - b.startBeat);
  const merged = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const prev = merged[merged.length - 1];
    const cur = sorted[i];
    const prevEnd = prev.startBeat + prev.duration;
    const gap = cur.startBeat - prevEnd;
    const pitchJump = Math.abs(cur.pitch - prev.pitch);
    if (gap <= maxGapBeat && pitchJump <= maxPitchJump) {
      const newEnd = Math.max(prevEnd, cur.startBeat + cur.duration);
      prev.duration = newEnd - prev.startBeat;
      prev.pitch = Math.round((prev.pitch + cur.pitch) / 2);
    } else {
      merged.push(cur);
    }
  }
  return merged;
}

export function trimPlaceholdersAgainstNotes(placeholders, fixedNotes) {
  if (!placeholders.length) {
    return { trimmed: [], dropped: 0, split: 0 };
  }
  const blockers = fixedNotes
    .map(note => ({ startBeat: note.startBeat, endBeat: note.startBeat + note.duration }))
    .sort((a, b) => a.startBeat - b.startBeat);

  let dropped = 0;
  let split = 0;
  const trimmed = [];

  for (const note of placeholders) {
    let segments = [{ startBeat: note.startBeat, endBeat: note.startBeat + note.duration }];

    for (const blocker of blockers) {
      const nextSegments = [];
      for (const segment of segments) {
        if (blocker.endBeat <= segment.startBeat || blocker.startBeat >= segment.endBeat) {
          nextSegments.push(segment);
          continue;
        }
        if (blocker.startBeat > segment.startBeat) {
          nextSegments.push({ startBeat: segment.startBeat, endBeat: blocker.startBeat });
        }
        if (blocker.endBeat < segment.endBeat) {
          nextSegments.push({ startBeat: blocker.endBeat, endBeat: segment.endBeat });
        }
      }
      segments = nextSegments;
      if (!segments.length) break;
    }

    const keptSegments = segments
      .map(segment => ({
        startBeat: Math.floor(segment.startBeat),
        endBeat: Math.ceil(segment.endBeat),
      }))
      .filter(segment => segment.endBeat - segment.startBeat >= 1);

    if (keptSegments.length === 0) {
      dropped += 1;
      continue;
    }
    if (keptSegments.length > 1) split += 1;

    for (const segment of keptSegments) {
      trimmed.push({
        ...note,
        id: note.id,
        startBeat: segment.startBeat,
        duration: segment.endBeat - segment.startBeat,
      });
    }
  }

  return { trimmed, dropped, split };
}

export function buildPitchSplitGroups(frames, startBeat, endBeat, vtBeatGap) {
  const sortedFrames = [...frames].sort((a, b) => a.beat - b.beat);
  const runs = [];
  if (sortedFrames.length > 0) {
    let current = { pitch: sortedFrames[0].pitch, frames: [sortedFrames[0]] };
    for (let i = 1; i < sortedFrames.length; i++) {
      const prev = sortedFrames[i - 1];
      const cur = sortedFrames[i];
      const beatGap = cur.beat - prev.beat;
      // Frames are sampled on a coarse beat grid (~0.8 beat spacing at current BPM),
      // so allow bigger continuity gaps and +/-1 semitone jitter inside a run.
      const sameRun = Math.abs(cur.pitch - current.pitch) <= 1 && beatGap <= 1.35;
      if (sameRun) {
        current.frames.push(cur);
      } else {
        runs.push(current);
        current = { pitch: cur.pitch, frames: [cur] };
      }
    }
    runs.push(current);
  }

  const runStats = runs.map(run => {
    const runStart = Math.round(Math.max(startBeat, run.frames[0].beat));
    const runEnd = Math.round(Math.min(endBeat, run.frames[run.frames.length - 1].beat + vtBeatGap));
    return {
      pitch: medianPitch(run.frames.map(f => f.pitch)),
      frameCount: run.frames.length,
      startBeat: runStart,
      endBeat: runEnd,
      duration: Math.max(1, runEnd - runStart),
    };
  });

  const stableRuns = runStats.filter(run => run.frameCount >= 2 && run.duration >= 2);
  const splitGroups = [];
  for (const run of stableRuns) {
    const prev = splitGroups[splitGroups.length - 1];
    if (!prev) {
      splitGroups.push({ ...run });
      continue;
    }
    const pitchDiff = Math.abs(run.pitch - prev.pitch);
    const startDiff = run.startBeat - prev.startBeat;
    if (pitchDiff >= 2 && startDiff >= 3) {
      splitGroups.push({ ...run });
    } else {
      // Merge unstable continuation into previous run.
      prev.endBeat = Math.max(prev.endBeat, run.endBeat);
      prev.duration = Math.max(1, prev.endBeat - prev.startBeat);
    }
  }

  return { runStats, stableRuns, splitGroups };
}

export function splitPlaceholderNotesByPitchRuns(placeholders, framePool, startBeat, endBeat, vtBeatGap) {
  if (!placeholders.length) return { notes: [], split: 0 };

  const splitNotes = [];
  let split = 0;

  for (const note of [...placeholders].sort((a, b) => a.startBeat - b.startBeat)) {
    const noteStart = Math.round(Math.max(startBeat, note.startBeat));
    const noteEnd = Math.round(Math.min(endBeat, note.startBeat + note.duration));
    const noteFrames = framePool.filter(f => f.beat >= noteStart && f.beat <= noteEnd);
    const { runStats, stableRuns, splitGroups } = buildPitchSplitGroups(noteFrames, noteStart, noteEnd, vtBeatGap);

    if (splitGroups.length < 2) {
      splitNotes.push(note);
      continue;
    }

    split += 1;
    console.log('[Analyze5s] placeholder split', {
      startBeat: noteStart,
      endBeat: noteEnd,
      duration: noteEnd - noteStart,
      runs: runStats,
      stableRuns,
      splitGroups,
    });

    for (let gi = 0; gi < splitGroups.length; gi++) {
      const group = splitGroups[gi];
      const nextGroup = splitGroups[gi + 1] || null;
      const durationWithTail = Math.max(1, (group.endBeat - group.startBeat) + Math.max(0, Math.round(vtBeatGap)));
      const rawEnd = group.startBeat + durationWithTail;
      // Prevent overlap between split placeholder segments.
      const clippedEnd = nextGroup ? Math.min(rawEnd, nextGroup.startBeat) : rawEnd;
      splitNotes.push({
        ...note,
        startBeat: group.startBeat,
        duration: Math.max(1, clippedEnd - group.startBeat),
        pitch: group.pitch,
        syllable: '... ',
      });
    }
  }

  return { notes: splitNotes, split };
}
