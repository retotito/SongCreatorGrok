<script>
  import { onMount, onDestroy, tick } from 'svelte';
  import { sessionId, generationResult, editorState, errorMessage, lyricsData, currentStep, uploadData, recordingActive, storageManagerOpen } from '../stores/appStore.js';
  import { getEditorData, getAudioUrl, saveEditorState, generateCleanedAudio, suggestVibrato, getLiveWordsWindow } from '../services/api.js';
  import { SUPPORTED_LANGUAGES } from '../lib/languages';

  // In packaged Tauri there is no Vite proxy — call the backend directly.
  const API_BASE = (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window)
    ? 'http://localhost:8001/api'
    : '/api';
  import { showConfirm, showAlert } from '../stores/dialogStore.js';
  import { PitchDetector } from 'pitchy';

  // Canvas refs
  let canvasEl;
  let ctx;

  // Data
  let notes = [];
  let bpm = 272;
  let gapMs = 0;
  let audioDuration = 0;
  let vocalUrl = '';
  let currentAudioUrl = ''; // drives the <audio> src — updated by switchAudioSource

  // Raw ms timings for BPM re-quantization
  let rawTimings = [];   // syllable_timings from backend (start/end in seconds)
  let pitchMap = [];     // midi pitch per syllable (extracted from initial Ultrastar parse)
  let initialBpm = 0;    // original detected BPM
  let previousBpm = 0;   // BPM before the last user change (for break re-quantization)
  let initialGap = 0;    // original detected GAP
  let bpmChanged = false; // track if user modified BPM/GAP

  // Save state
  let isSaving = false;
  let lastSaveTime = null;
  let editCount = 0;
  let hasUnsavedChanges = false;
  let autosaveInterval = null;
  let toastMsg = '';        // brief status message shown as a toast
  let toastTimer = null;
  let toastCenter = false;
  let uiBusy = false;
  let editedAudioLoading = false;
  let waveformLoading = false;
  let waveformLoadToken = 0;

  // View state
  let scrollX = 0;
  let zoom = 20;          // pixels per beat (default zoomed in)
  // viewHeight grows with waveformHeight so the note grid stays a fixed size
  $: viewHeight = 533 + DOWNBEAT_HANDLE_H + (showWaveform ? waveformHeight : 0);
  let noteHeight = 11;

  // Pitch range (MIDI)
  let minPitch = 36;     // C2
  let maxPitch = 96;     // C7

  // Interaction
  let selectedNote = null;
  let selectedNotes = new Set(); // multi-select
  let dragMode = null;     // 'move' | 'resize-left' | 'resize-right' | 'resize-shared'
  let dragStart = { x: 0, y: 0 };
  let isDragging = false;
  // Custom scrollbar state (replaces native <input type="range">)
  let scrollTrackEl;           // ref to the track div
  let scrollHandleDragging = false;
  let scrollDragStartX = 0;    // clientX at drag start
  let scrollDragStartBeat = 0; // center beat at drag start
  let canvasW = 800;           // px width of canvas, updated in resizeCanvas()

  // Computed: fraction 0–1 for both handle and playhead tick.
  // Handle tracks CENTER beat so it stays fixed when zooming.
  $: scrollBeatRange = Math.max(1, totalBeats - getMinBeat());
  $: centerBeat      = scrollX / zoom + canvasW / (2 * zoom);
  $: scrollHandlePct = ((centerBeat   - getMinBeat()) / scrollBeatRange * 100).toFixed(3);
  $: playheadPct     = ((playbackBeat - getMinBeat()) / scrollBeatRange * 100).toFixed(3);

  // Clamp scrollX so the view never scrolls past the end of the song
  function clampScrollX(x) {
    const minX = getMinBeat() * zoom;
    const maxX = Math.max(minX, totalBeats * zoom - canvasW);
    return Math.min(maxX, Math.max(minX, x));
  }

  // Keep a target beat visible with a small edge padding while preserving current context.
  function ensureBeatVisible(beat, edgePaddingPx = 56) {
    const w = canvasEl?.width || canvasW || 800;
    const x = beatToX(beat);
    let next = scrollX;
    if (x < edgePaddingPx) {
      next = clampScrollX(scrollX - (edgePaddingPx - x));
    } else if (x > w - edgePaddingPx) {
      next = clampScrollX(scrollX + (x - (w - edgePaddingPx)));
    }
    if (next !== scrollX) scrollX = next;
  }

  function nudgeViewport(deltaPx) {
    scrollX = clampScrollX(scrollX + deltaPx);
    draw();
  }

  // While dragging loop boundaries, auto-scroll only when the pointer is outside the canvas.
  function autoScrollAtCanvasEdge(mx) {
    const w = canvasEl?.width || canvasW || 800;
    const maxStepPx = 28;
    let deltaPx = 0;

    if (mx < 0) {
      const overshoot = Math.abs(mx);
      const strength = Math.min(2, overshoot / 40);
      deltaPx = -Math.max(3, Math.round(maxStepPx * strength));
    } else if (mx > w) {
      const overshoot = mx - w;
      const strength = Math.min(2, overshoot / 40);
      deltaPx = Math.max(3, Math.round(maxStepPx * strength));
    }

    if (!deltaPx) return false;

    const next = clampScrollX(scrollX + deltaPx);
    if (next === scrollX) return false;
    scrollX = next;
    return true;
  }

  // While dragging cleanup segments, auto-scroll based on the segment edge that
  // is approaching the viewport edge, not only the mouse pointer position.
  function autoScrollCleanupSegment(seg, mode, msDelta = 0) {
    if (!seg) return false;
    const w = canvasEl?.width || canvasW || 800;
    const edgePaddingPx = 12;
    const startX = beatToX(timeToBeat(seg.startMs / 1000));
    const endX = beatToX(timeToBeat(seg.endMs / 1000));

    let deltaPx = 0;
    if (mode === 'start') {
      if (startX < edgePaddingPx) deltaPx = startX - edgePaddingPx;
      else if (startX > w - edgePaddingPx) deltaPx = startX - (w - edgePaddingPx);
    } else if (mode === 'end') {
      if (endX < edgePaddingPx) deltaPx = endX - edgePaddingPx;
      else if (endX > w - edgePaddingPx) deltaPx = endX - (w - edgePaddingPx);
    } else {
      if (msDelta >= 0 && endX > w - edgePaddingPx) {
        deltaPx = endX - (w - edgePaddingPx);
      } else if (msDelta < 0 && startX < edgePaddingPx) {
        deltaPx = startX - edgePaddingPx;
      } else if (startX < edgePaddingPx) {
        deltaPx = startX - edgePaddingPx;
      } else if (endX > w - edgePaddingPx) {
        deltaPx = endX - (w - edgePaddingPx);
      }
    }

    if (!deltaPx) return false;
    const next = clampScrollX(scrollX + deltaPx);
    if (next === scrollX) return false;
    scrollX = next;
    return true;
  }

  // Keep dragged loop boundaries visually inside the canvas while edge-scrolling.
  function clampDragXToCanvas(mx) {
    const w = canvasEl?.width || canvasW || 800;
    return Math.max(1, Math.min(w - 1, mx));
  }

  function getVisibleBeatBounds() {
    const w = canvasEl?.width || canvasW || 800;
    return {
      minBeat: xToBeat(0),
      maxBeat: xToBeat(w),
    };
  }

  function clampValue(value, min, max) {
    if (max < min) return min;
    return Math.max(min, Math.min(max, value));
  }

  function clampNoteStartToVisibleCanvas(startBeat, duration) {
    const { minBeat, maxBeat } = getVisibleBeatBounds();
    const minStart = Math.ceil(minBeat);
    const maxStart = Math.floor(maxBeat - duration);
    return clampValue(startBeat, minStart, maxStart);
  }

  function clampSelectedMoveDeltaToVisibleCanvas(moveDelta, selection) {
    if (!selection?.length) return moveDelta;
    const { minBeat, maxBeat } = getVisibleBeatBounds();
    const selectionStart = Math.min(...selection.map(note => note.startBeat));
    const selectionEnd = Math.max(...selection.map(note => note.startBeat + note.duration));
    const minDelta = Math.ceil(minBeat - selectionStart);
    const maxDelta = Math.floor(maxBeat - selectionEnd);
    return clampValue(moveDelta, minDelta, maxDelta);
  }

  function getSongBeatBounds() {
    const minBeat = timeToBeat(0);
    const durationSec = Math.max(0, audioEl?.duration || audioDuration || 0);
    return {
      minBeat,
      maxBeat: durationSec > 0 ? timeToBeat(durationSec) : Infinity,
    };
  }

  function clampSelectedMoveDeltaToSongBounds(moveDelta, selection) {
    if (!selection?.length) return moveDelta;
    const { minBeat, maxBeat } = getSongBeatBounds();
    const selectionStart = Math.min(...selection.map(note => note.startBeat));
    const selectionEnd = Math.max(...selection.map(note => note.startBeat + note.duration));
    const minDelta = Math.ceil(minBeat - selectionStart);
    const maxDelta = Number.isFinite(maxBeat) ? Math.floor(maxBeat - selectionEnd) : Infinity;
    return clampValue(moveDelta, minDelta, maxDelta);
  }

  // Rubber-band (box) selection
  let isBoxSelecting = false;
  let boxSelectStart = { x: 0, y: 0 };
  let boxSelectEnd = { x: 0, y: 0 };

  // Clipboard (cut/copy/paste)
  let clipboard = null;        // { notes: [...], mode: 'cut'|'copy', sourceBeat: number }
  let pasteMode = false;       // user is positioning the paste
  let pastePreviewBeat = null; // ghost preview beat position
  let cutNoteIds = new Set();  // ids of notes being cut (rendered semi-transparent)

  // Set GAP mode (Ctrl/Cmd+S)
  let setGapMode = false;       // user is picking a new GAP position
  let setGapHoverBeat = null;   // beat of the grid line currently hovered

  // Downbeat handle drag (replaces Grid Align mode)
  const DOWNBEAT_HANDLE_H = 14; // px height of the top strip reserved for diamonds
  let downbeatHandleDragging = false;
  let downbeatHandleDragStartX = 0;
  let downbeatHandleDragStartAnchorBeat = 0;
  let downbeatHandleHovered = false; // cursor is in the top strip over a diamond

  // Drag pitch preview (oscillator while dragging a note)
  let dragOsc = null;
  let dragGain = null;
  let dragAudioCtx = null;
  let dragLastPitch = null;
  let dragOscStopTimer = null;

  // Flags — green marker lines persisted per session
  let flags = [];
  let flagIdCounter = 1;
  let selectedFlag = null;

  /**
   * Unified formula: ms → beat using current bpm/gapMs.
   * This is the single source of truth for all positional recalculation.
   *   beat = round((timeMs - gapMs) * bpm / 15000)
   */
  function msToBeat(timeMs) {
    return Math.round((timeMs - gapMs) * bpm / 15000);
  }

  function normalizeFlag(flag) {
    const beat = Math.round(Number(flag?.beat) || 0);
    const parsedTimeMs = Number(flag?.timeMs);
    // timeMs is the source of truth — derive it from beat only as fallback
    const timeMs = Number.isFinite(parsedTimeMs)
      ? Math.max(0, parsedTimeMs)
      : Math.max(0, gapMs + beat * 15000 / bpm);
    return {
      id: Number(flag?.id),
      beat: snapBeatValue(msToBeat(timeMs)),
      timeMs,
    };
  }

  /**
   * Resync all beat-positioned items (breaks, flags) from their stored timeMs
   * using the current bpm/gapMs. Same formula for all.
   * Call after any BPM or GAP change.
   */
  function resyncAllToGrid(previousTimingRef = null) {
    // Recalc regular notes only when rawTimings is absent (txt-loaded songs).
    // When rawTimings is present, requantizeFromMs already handles notes.
    if ((!rawTimings || rawTimings.length === 0) && previousTimingRef) {
      const prevBpm = Math.max(1, Number(previousTimingRef.bpm) || bpm);
      const prevGapMs = Number.isFinite(previousTimingRef.gapMs) ? previousTimingRef.gapMs : gapMs;
      notes = notes.map(n => {
        if (n.type === 'break') return n; // breaks handled below
        const noteTimeMs = prevGapMs + n.startBeat * 15000 / prevBpm;
        const noteEndTimeMs = prevGapMs + (n.startBeat + n.duration) * 15000 / prevBpm;
        const newStart = msToBeat(noteTimeMs);
        const newEnd = Math.max(newStart + 1, msToBeat(noteEndTimeMs));
        return { ...n, startBeat: newStart, duration: newEnd - newStart };
      });
    }
    // Breaks
    notes = notes.map(n => {
      if (n.type !== 'break' || !Number.isFinite(n.timeMs)) return n;
      const newBeat = snapBeatValue(msToBeat(n.timeMs));
      const newEndBeat = Number.isFinite(n.endTimeMs)
        ? Math.max(newBeat + 1, snapBeatValue(msToBeat(n.endTimeMs)))
        : n.endBeat;
      return { ...n, startBeat: newBeat, endBeat: newEndBeat };
    });
    // Flags
    if (flags.length) {
      flags = flags.map(normalizeFlag);
      saveFlags();
    }
  }

  // Vocal cleanup segments (waveform-only helper ranges)
  let cleanupSegments = []; // { id, startMs, endMs }
  let cleanupSegmentIdCounter = 1;
  let selectedCleanupSegment = null;
  let cleanupDrag = null; // { id, mode, startMs, endMs, mouseStartMs }
  let cleanupKeyboardSaveTimer = null;
  const cleanupJoinMaxGapMs = 150;
  let cleanupSegmentsHavePatchedMetadata = false;

  function loadFlags() {
    if (!$sessionId) return;
    try {
      const raw = localStorage.getItem(`editor_flags_${$sessionId}`);
      if (raw) {
        const parsed = JSON.parse(raw);
        flags = (parsed.flags || []).map(normalizeFlag);
        flagIdCounter = (parsed.counter || 0) + 1;
      }
    } catch { /* ignore */ }
  }

  function saveFlags() {
    if (!$sessionId) return;
    localStorage.setItem(`editor_flags_${$sessionId}`, JSON.stringify({ flags, counter: flagIdCounter }));
  }

  function addFlagAt(beat) {
    const snappedBeat = snapBeatValue(Math.round(beat));
    const timeMs = Math.max(0, gapMs + snappedBeat * 15000 / bpm);
    const newFlagId = flagIdCounter++;
    flags = [...flags, { id: newFlagId, beat: snappedBeat, timeMs }];
    selectedFlag = newFlagId;
    selectedNote = null;
    selectedNotes = new Set();
    saveFlags();
    closeContextMenu();
    draw();
  }

  function deleteFlag(id) {
    flags = flags.filter(f => f.id !== id);
    if (selectedFlag === id) selectedFlag = null;
    saveFlags();
    closeContextMenu();
    draw();
  }

  function getGridNudgeStep() {
    return zoom >= 4 ? 1 : BEATS_PER_QUARTER / 2;
  }

  function clampBeatToSongBounds(beat) {
    const minBeat = snapBeatValue(timeToBeat(0));
    const maxBeat = snapBeatValue(timeToBeat(Math.max(0, audioDuration || 0)));
    return Math.max(minBeat, Math.min(maxBeat, beat));
  }

  function nudgeFlag(id, delta) {
    const flag = flags.find(f => f.id === id);
    if (!flag) return;
    const step = getGridNudgeStep();
    flag.beat = clampBeatToSongBounds(
      snapBeatValue(flag.beat + (delta < 0 ? -step : step))
    );
    flag.timeMs = Math.max(0, gapMs + flag.beat * 15000 / bpm);
    flags = [...flags];
    ensureBeatVisible(flag.beat);
    selectedFlag = flag.id;
    saveFlags();
    markUnsaved();
    closeContextMenu();
    draw();
  }

  function nudgeBreak(noteId, delta) {
    const step = getGridNudgeStep();
    let moved = false;
    let movedBeat = null;
    pushUndo();
    notes = notes.map(n => {
      if (n.id !== noteId || n.type !== 'break') return n;
      const newBeat = clampBeatToSongBounds(
        snapBeatValue(n.startBeat + (delta < 0 ? -step : step))
      );
      moved = true;
      movedBeat = newBeat;
      return {
        ...n,
        startBeat: newBeat,
        timeMs: Math.max(0, gapMs + newBeat * 15000 / bpm),
      };
    });
    if (!moved) return;
    if (movedBeat !== null) ensureBeatVisible(movedBeat);
    markUnsaved();
    closeContextMenu();
    draw();
  }

  function clearMarkerSelection() {
    selectedFlag = null;
    selectedNote = null;
    selectedNotes = new Set();
  }

  function clearCleanupKeyboardSaveTimer() {
    if (cleanupKeyboardSaveTimer) {
      clearTimeout(cleanupKeyboardSaveTimer);
      cleanupKeyboardSaveTimer = null;
    }
  }

  function scheduleCleanupKeyboardSave() {
    clearCleanupKeyboardSaveTimer();
    cleanupKeyboardSaveTimer = setTimeout(() => {
      cleanupKeyboardSaveTimer = null;
      if (hasUnsavedChanges || cleanedAudioDirty) handleSave();
    }, 500);
  }

  function getCleanupKeyboardStepMs(largeStep = false) {
    const beatStep = getGridNudgeStep() * (largeStep ? 4 : 1);
    return (15000 / bpm) * beatStep;
  }

  function adjustSelectedCleanupSegment(mode, direction, largeStep = false) {
    if (selectedCleanupSegment === null) return false;
    const seg = cleanupSegments.find(s => s.id === selectedCleanupSegment);
    if (!seg || segRecPatched.has(seg.id)) return false;

    const stepMs = getCleanupKeyboardStepMs(largeStep) * (direction > 0 ? 1 : -1);
    const CLAMP_GAP = 10;
    const sortedOthers = cleanupSegments.filter(s => s.id !== seg.id).sort((a, b) => a.startMs - b.startMs);
    const prevN = [...sortedOthers].reverse().find(s => s.endMs <= seg.startMs);
    const nextN = sortedOthers.find(s => s.startMs >= seg.endMs);
    const minStart = prevN ? prevN.endMs + CLAMP_GAP : 0;
    const songEndMs = Math.max(0, (audioEl?.duration || audioDuration || 0) * 1000);
    const maxEndBound = songEndMs > 0 ? songEndMs : Infinity;
    const maxEnd = nextN ? Math.min(nextN.startMs - CLAMP_GAP, maxEndBound) : maxEndBound;

    let changed = false;
    pushUndo();
    cleanupSegments = cleanupSegments.map(current => {
      if (current.id !== seg.id) return current;

      if (mode === 'move') {
        const duration = current.endMs - current.startMs;
        let newStart = current.startMs + stepMs;
        newStart = Math.max(minStart, newStart);
        if (isFinite(maxEnd)) newStart = Math.min(maxEnd - duration, newStart);
        if (newStart === current.startMs) return current;
        changed = true;
        return { ...current, startMs: newStart, endMs: newStart + duration };
      }

      if (mode === 'start') {
        let newStart = current.startMs + stepMs;
        newStart = Math.max(minStart, newStart);
        newStart = Math.min(current.endMs - 50, newStart);
        if (newStart === current.startMs) return current;
        changed = true;
        return { ...current, startMs: newStart };
      }

      if (mode === 'end') {
        let newEnd = current.endMs + stepMs;
        if (isFinite(maxEnd)) newEnd = Math.min(maxEnd, newEnd);
        newEnd = Math.max(current.startMs + 50, newEnd);
        if (newEnd === current.endMs) return current;
        changed = true;
        return { ...current, endMs: newEnd };
      }

      return current;
    }).map(normalizeCleanupSegment).sort((a, b) => a.startMs - b.startMs);

    if (!changed) return false;

    const updatedSeg = cleanupSegments.find(s => s.id === seg.id);
    if (updatedSeg) {
      let focusMs = updatedSeg.endMs;
      if (mode === 'move') {
        focusMs = direction > 0 ? updatedSeg.endMs : updatedSeg.startMs;
      } else if (mode === 'start') {
        focusMs = updatedSeg.startMs;
      } else if (mode === 'end') {
        focusMs = updatedSeg.endMs;
      }
      ensureBeatVisible(timeToBeat(focusMs / 1000), 12);
    }

    cleanedAudioDirty = true;
    markUnsaved();
    scheduleCleanupKeyboardSave();
    draw();
    return true;
  }

  function normalizeCleanupSegment(seg) {
    const a = Number(seg.startMs);
    const b = Number(seg.endMs);
    const startMs = Math.min(a, b);
    const endMs = Math.max(a, b);
    if (endMs - startMs < 50) {
      return { ...seg, startMs, endMs: startMs + 50 };
    }
    return { ...seg, startMs, endMs };
  }

  function findCleanupOverlap(startMs, endMs, excludeId = null) {
    const rangeStart = Math.min(startMs, endMs);
    const rangeEnd = Math.max(startMs, endMs);
    return cleanupSegments.find(seg => {
      if (excludeId !== null && seg.id === excludeId) return false;
      return rangeStart < seg.endMs && rangeEnd > seg.startMs;
    }) || null;
  }

  function serializeCleanupSegments() {
    return cleanupSegments
      .map(s => ({
        start_ms: s.startMs,
        end_ms: s.endMs,
        patched: segRecPatched.has(s.id),
      }))
      .sort((a, b) => a.start_ms - b.start_ms);
  }

  function serializeCleanupSegmentsForCleaning() {
    // Do not mute regions that were replaced by user recordings.
    return cleanupSegments
      .filter(s => !segRecPatched.has(s.id))
      .map(s => ({ start_ms: s.startMs, end_ms: s.endMs }))
      .sort((a, b) => a.start_ms - b.start_ms);
  }

  function setCleanupSegmentsFromApi(segments = []) {
    const parsed = [];
    let hasPatchedField = false;
    const patchedIds = new Set();
    for (const seg of segments) {
      // Support both ms format (new) and beat format (legacy)
      let startMs, endMs;
      if (seg?.start_ms != null) {
        startMs = Number(seg.start_ms);
        endMs = Number(seg.end_ms);
      } else if (seg?.start_beat != null) {
        // Legacy: convert beats to ms using current bpm/gapMs
        startMs = (beatToTime(Number(seg.start_beat))) * 1000;
        endMs = (beatToTime(Number(seg.end_beat))) * 1000;
      } else continue;
      if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) continue;
      const parsedSeg = normalizeCleanupSegment({
        id: cleanupSegmentIdCounter++,
        startMs,
        endMs,
      });
      parsed.push(parsedSeg);
      if (Object.prototype.hasOwnProperty.call(seg || {}, 'patched')) {
        hasPatchedField = true;
        if (seg?.patched) patchedIds.add(parsedSeg.id);
      }
    }
    cleanupSegments = parsed.sort((a, b) => a.startMs - b.startMs);
    cleanupSegmentsHavePatchedMetadata = hasPatchedField;
    segRecPatched = hasPatchedField ? patchedIds : new Set();
    selectedCleanupSegment = null;
    cleanupDrag = null;
  }

  function addCleanupSegmentAtMs(startMs) {
    pushUndo();
    let segStartMs = startMs;
    let segEndMs = startMs + Math.max(500, (15000 / bpm));

    // If user adds a cleanup segment from within an active loop,
    // default the segment to exactly match loop boundaries.
    if (loopEnabled && loopStartBeat !== null && loopEndBeat !== null) {
      const loopStartMs = beatToTime(Math.min(loopStartBeat, loopEndBeat)) * 1000;
      const loopEndMs = beatToTime(Math.max(loopStartBeat, loopEndBeat)) * 1000;
      if (startMs >= loopStartMs && startMs <= loopEndMs) {
        segStartMs = loopStartMs;
        segEndMs = loopEndMs;
      }
    }

    const seg = normalizeCleanupSegment({ id: cleanupSegmentIdCounter++, startMs: segStartMs, endMs: segEndMs });
    const overlap = findCleanupOverlap(seg.startMs, seg.endMs);
    if (overlap) {
      showToast('Cleanup segments cannot overlap', 4000, true);
      return;
    }
    console.log(`[CleanupSeg] Add segment id=${seg.id} startMs=${seg.startMs.toFixed(0)} endMs=${seg.endMs.toFixed(0)} | total=${cleanupSegments.length + 1}`);
    cleanupSegments = [...cleanupSegments, seg].sort((a, b) => a.startMs - b.startMs);
    selectedCleanupSegment = seg.id;
    cleanedAudioDirty = true;
    markUnsaved();
    closeContextMenu();
    draw();
    handleSave();
  }

  async function restoreSegmentRangeFromSource(startMs, endMs) {
    const resp = await fetch(`${API_BASE}/restore-segment/${$sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_ms: startMs, end_ms: endMs })
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data?.detail || data?.message || 'Restore request failed');
    }
    if (data?.note === 'no original vocal to restore from') {
      throw new Error('This session has no vocals source baseline to restore from.');
    }
    const cacheBust = `?v=${Date.now()}`;
    vocalUrl = (hasVocalsAudio ? getAudioUrl($sessionId, 'vocals') : '') + cacheBust;
    return data;
  }

  async function deleteCleanupSegment(id) {
    const seg = cleanupSegments.find(s => s.id === id);
    if (!seg) return;
    console.log(`[CleanupSeg] Delete segment id=${id} startMs=${seg?.startMs?.toFixed(0)} endMs=${seg?.endMs?.toFixed(0)} | remaining=${cleanupSegments.length - 1} | wasSpliced=${segRecPatched.has(id)}`);
    pushUndo();

    // Always restore source-truth audio for the deleted segment range.
    try {
      const data = await restoreSegmentRangeFromSource(seg.startMs, seg.endMs);
      console.log(`[CleanupSeg] Restored original audio for segment ${id}:`, data);
      segRecPatched = new Set([...segRecPatched].filter(x => x !== id));
      cleanedAudioAvailable = false;
      cleanedAudioCacheBust = '';
    } catch (e) {
      console.warn('[CleanupSeg] Restore failed:', e);
      const msg = String(e?.message || '');
      // Fresh/unspliced sessions may not have a saved demucs baseline.
      // For non-recorded segments, deleting is still safe because no splice was applied.
      if (!(segRecPatched.has(id) === false && msg.includes('no saved original demucs vocal'))) {
        showToast(e?.message || 'Failed to restore source audio for this segment');
        return;
      }
    }

    cleanupSegments = cleanupSegments.filter(s => s.id !== id);
    if (serializeCleanupSegmentsForCleaning().length === 0) {
      cleanedAudioAvailable = false;
      cleanedAudioCacheBust = '';
    }
    if (selectedCleanupSegment === id) selectedCleanupSegment = null;
    if (cleanupSegments.length === 0) {
      cleanedAudioAvailable = false;
      // Auto-switch back to vocals (uses updated vocalUrl after restore above)
      if (audioSource === 'edited') switchAudioSource('vocals');
    } else if (audioSource === 'edited') {
      // Refresh edited source using canonical resolver (cleaned preferred).
      switchAudioSource('edited');
    }
    cleanedAudioDirty = true;
    markUnsaved();
    closeContextMenu();
    draw();
    handleSave();
    if (pitchLineVisible) computeRecordedPitchLine();
  }

  async function emptyRecordedCleanupSegment(id) {
    const seg = cleanupSegments.find(s => s.id === id);
    if (!seg || !segRecPatched.has(id)) return;
    pushUndo();
    try {
      const data = await restoreSegmentRangeFromSource(seg.startMs, seg.endMs);
      console.log(`[CleanupSeg] Emptied recorded segment id=${id}:`, data);
      segRecPatched = new Set([...segRecPatched].filter(x => x !== id));
      cleanedAudioAvailable = false;
      cleanedAudioCacheBust = '';
      cleanedAudioDirty = true;
      markUnsaved();
      closeContextMenu();
      draw();
      handleSave();
      if (pitchLineVisible) computeRecordedPitchLine();
    } catch (e) {
      console.warn('[CleanupSeg] Empty recorded segment failed:', e);
      showToast(e?.message || 'Failed to empty recorded segment');
    }
  }

  function splitCleanupSegmentAtMs(id, splitMs) {
    const seg = cleanupSegments.find(s => s.id === id);
    if (!seg) return;
    if (splitMs <= seg.startMs + 50 || splitMs >= seg.endMs - 50) {
      showToast('Split point too close to segment edge');
      return;
    }

    pushUndo();
    const rightSeg = normalizeCleanupSegment({
      id: cleanupSegmentIdCounter++,
      startMs: splitMs,
      endMs: seg.endMs,
    });
    seg.endMs = splitMs;
    cleanupSegments = [...cleanupSegments, rightSeg]
      .map(normalizeCleanupSegment)
      .sort((a, b) => a.startMs - b.startMs);

    // Splitting a recorded segment means both resulting subranges remain recorded.
    if (segRecPatched.has(id)) {
      segRecPatched = new Set([...segRecPatched, rightSeg.id]);
    }

    selectedCleanupSegment = rightSeg.id;
    cleanedAudioDirty = true;
    markUnsaved();
    closeContextMenu();
    draw();
    handleSave();
  }

  function getJoinableCleanupPairAtMs(splitMs) {
    if (!Number.isFinite(splitMs)) return null;
    const sorted = [...cleanupSegments].sort((a, b) => a.startMs - b.startMs);
    for (let i = 0; i < sorted.length - 1; i++) {
      const left = sorted[i];
      const right = sorted[i + 1];
      if (splitMs <= left.endMs || splitMs >= right.startMs) continue;
      const gapMs = Math.max(0, right.startMs - left.endMs);
      if (gapMs > cleanupJoinMaxGapMs) continue;
      const leftPatched = segRecPatched.has(left.id);
      const rightPatched = segRecPatched.has(right.id);
      if (leftPatched !== rightPatched) return null;
      return { left, right, patched: leftPatched };
    }
    return null;
  }

  function getJoinableCleanupNeighborsForSegment(id) {
    const sorted = [...cleanupSegments].sort((a, b) => a.startMs - b.startMs);
    const idx = sorted.findIndex(s => s.id === id);
    if (idx === -1) return { left: null, right: null };

    const curr = sorted[idx];
    const leftSeg = idx > 0 ? sorted[idx - 1] : null;
    const rightSeg = idx < sorted.length - 1 ? sorted[idx + 1] : null;
    const leftGapMs = leftSeg ? Math.max(0, curr.startMs - leftSeg.endMs) : Infinity;
    const rightGapMs = rightSeg ? Math.max(0, rightSeg.startMs - curr.endMs) : Infinity;

    const left = leftSeg && leftGapMs <= cleanupJoinMaxGapMs && segRecPatched.has(leftSeg.id) === segRecPatched.has(curr.id)
      ? { left: leftSeg, right: curr }
      : null;
    const right = rightSeg && rightGapMs <= cleanupJoinMaxGapMs && segRecPatched.has(rightSeg.id) === segRecPatched.has(curr.id)
      ? { left: curr, right: rightSeg }
      : null;

    return { left, right };
  }

  function joinCleanupSegments(leftId, rightId) {
    const left = cleanupSegments.find(s => s.id === leftId);
    const right = cleanupSegments.find(s => s.id === rightId);
    if (!left || !right) return;
    const gapMs = Math.max(0, right.startMs - left.endMs);
    if (gapMs > cleanupJoinMaxGapMs) {
      showToast(`Segments must be touching or within ${cleanupJoinMaxGapMs}ms to join`);
      return;
    }
    const leftPatched = segRecPatched.has(left.id);
    const rightPatched = segRecPatched.has(right.id);
    if (leftPatched !== rightPatched) {
      showToast('Cannot join recorded and clean segments');
      return;
    }

    pushUndo();
    left.endMs = Math.max(left.endMs, right.endMs);
    cleanupSegments = cleanupSegments
      .filter(s => s.id !== right.id)
      .map(normalizeCleanupSegment)
      .sort((a, b) => a.startMs - b.startMs);
    if (rightPatched) {
      segRecPatched = new Set([...segRecPatched].filter(x => x !== right.id));
    }
    selectedCleanupSegment = left.id;
    cleanedAudioDirty = true;
    markUnsaved();
    closeContextMenu();
    draw();
    handleSave();
  }

  function getEditedAudioUrl() {
    // Prefer cleaned audio whenever it exists (it includes latest cleanup muting,
    // and on backend it is generated from the current vocal source, including splices).
    if (cleanedAudioAvailable) return getAudioUrl($sessionId, 'cleaned') + cleanedAudioCacheBust;
    // Fallback to patched vocal when no cleaned file exists yet.
    if (segRecPatched.size > 0) return vocalUrl;
    // Last fallback should still be a real vocals source, never /cleaned.
    return originalVocalUrl || vocalUrl;
  }

  function nudgeCleanupSegment(id, deltaMs) {
    const seg = cleanupSegments.find(s => s.id === id);
    if (!seg) return;
    pushUndo();
    const nextStartMs = seg.startMs + deltaMs;
    const nextEndMs = seg.endMs + deltaMs;
    if (findCleanupOverlap(nextStartMs, nextEndMs, id)) {
      showToast('Cleanup segments cannot overlap', 4000, true);
      return;
    }
    seg.startMs = nextStartMs;
    seg.endMs = nextEndMs;
    cleanupSegments = [...cleanupSegments].sort((a, b) => a.startMs - b.startMs);
    markUnsaved();
    draw();
    closeContextMenu();
  }

  function hitTestCleanupSegment(mx, my) {
    if (!showWaveform || my > waveTop()) return null;
    for (let i = cleanupSegments.length - 1; i >= 0; i--) {
      const seg = cleanupSegments[i];
      const sx = beatToX(timeToBeat(seg.startMs / 1000));
      const ex = beatToX(timeToBeat(seg.endMs / 1000));
      const left = Math.min(sx, ex);
      const right = Math.max(sx, ex);
      if (Math.abs(mx - left) <= 2) return { id: seg.id, mode: 'start' };
      if (Math.abs(mx - right) <= 2) return { id: seg.id, mode: 'end' };
      if (mx >= left && mx <= right) return { id: seg.id, mode: 'move' };
    }
    return null;
  }

  // Context menu
  let contextMenu = {
    visible: false,
    x: 0,
    y: 0,
    noteId: null,
    isBreak: false,
    isEmpty: false,
    isFlag: false,
    isPasteMenu: false,
    isCleanup: false,
    isWaveformEmpty: false,
    flagId: null,
    cleanupId: null,
    beat: 0,
    ms: null,
    pitch: 0,
    traceFrame: null,
  };
  let editingSyllable = '';
  let contextMenuEl;

  // Segment local regenerate modal (prototype)
  let segRegenModalOpen = false;
  let segRegenMode = 'lyrics_first'; // 'lyrics_first' | 'one_go'
  let segRegenRange = {
    sourceType: 'cleanup', // 'cleanup' | 'loop'
    cleanupId: null,
    startMs: 0,
    endMs: 0,
  };
  let segRegenModalX = 28;
  let segRegenModalY = 84;
  let segRegenModalDragging = false;
  let segRegenModalDragOffsetX = 0;
  let segRegenModalDragOffsetY = 0;
  let segRegenLanguage = 'auto';
  let segRegenPreset = 'balanced';
  let segRegenAudioSource = 'vocals'; // 'vocals' | 'edited'
  let segRegenCurrentEditorSource = 'vocals'; // 'vocals' | 'edited' (badge-only)
  let segRegenPreviewLoading = false;
  let segRegenPreviewError = '';
  let segRegenPreviewLines = [];
  let segRegenPreviewConfidence = null;
  let segRegenAutoHyphenate = true;
  let segRegenHyphenateLoading = false;
  let segRegenPreviewHyphenated = false;
  let segRegenGenerateLoading = false;
  $: segRegenModalBlocking = segRegenPreviewLoading || segRegenHyphenateLoading || segRegenGenerateLoading;
  $: segRegenModalBlockingLabel = segRegenGenerateLoading
    ? 'Generating notes...'
    : segRegenHyphenateLoading
      ? 'Applying hyphenation...'
      : 'Recognizing lyrics...';

  // Vibrato tool modal
  let vibratoModalOpen = false;
  let vibratoModalX = 40;
  let vibratoModalY = 96;
  let vibratoModalDragging = false;
  let vibratoModalDragOffsetX = 0;
  let vibratoModalDragOffsetY = 0;
  let vibratoNoteId = null;
  let vibratoAudioSource = 'vocals'; // 'vocals' | 'edited'
  let vibratoCurrentEditorSource = 'vocals';
  let vibratoLoading = false;
  let vibratoError = '';
  let vibratoSegments = []; // [{ start_sec, end_sec, pitch }]
  let vibratoSensitivity = 'balanced'; // 'subtle' | 'balanced' | 'strict'

  // Undo/Redo history
  let undoStack = [];
  let redoStack = [];
  const MAX_UNDO = 50;

  // Playback
  let audioEl;
  let isPlaying = false;
  let playbackBeat = 0;
  let animFrame;
  let currentTimeSec = 0;  // Reactive time display

  // Scroll mode: notes scroll, cursor stays fixed
  let scrollMode = false;

  // Playback speed
  let playbackRate = 1.0;

  function getEditorUiPrefsKey() {
    return $sessionId ? `editor_ui_prefs_${$sessionId}` : null;
  }

  function saveEditorUiPrefs(reason = 'unknown') {
    const key = getEditorUiPrefsKey();
    if (!key) return;
    const payload = {
      scrollMode,
      playbackRate,
      audioSource,
      audioVolume,
      midiPlayback,
      metronomeEnabled,
      waveformHeight,
      micDeviceId,
      vibratoModalX,
      vibratoModalY,
    };
    localStorage.setItem(key, JSON.stringify(payload));
    console.log('[Step4] Saved UI prefs', { reason, ...payload });
  }

  function restoreEditorUiPrefs() {
    const key = getEditorUiPrefsKey();
    if (!key) return null;
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    try {
      const prefs = JSON.parse(raw);
      console.log('[Step4] Loaded UI prefs', prefs);
      return prefs;
    } catch (err) {
      console.warn('[Step4] Failed to parse UI prefs, ignoring', err);
      return null;
    }
  }

  function resolvePreferredAudioSource(preferred) {
    const editedAvailable = hasVocalsAudio && (cleanedAudioAvailable || segRecPatched.size > 0);
    if (preferred === 'edited' && editedAvailable) return 'edited';
    if (preferred === 'vocals' && hasVocalsAudio) return 'vocals';
    if (preferred === 'original' && hasOriginalAudio) return 'original';
    // Fallback policy: prefer full mix when preferred source is unavailable.
    if (hasOriginalAudio) return 'original';
    if (hasVocalsAudio) return editedAvailable ? 'edited' : 'vocals';
    return 'original';
  }

  function toggleScrollMode() {
    scrollMode = !scrollMode;
    saveEditorUiPrefs('scroll-mode');
  }

  // Keep AI modal "(current)" source badge in sync with editor source,
  // but ignore full-mix/original while the modal is open.
  $: if (segRegenModalOpen) {
    if (audioSource === 'edited') segRegenCurrentEditorSource = 'edited';
    else if (audioSource === 'vocals') segRegenCurrentEditorSource = 'vocals';
  }

  $: if (vibratoModalOpen) {
    if (audioSource === 'edited') vibratoCurrentEditorSource = 'edited';
    else if (audioSource === 'vocals') vibratoCurrentEditorSource = 'vocals';
  }

  // MIDI pitch playback during track play
  let midiPlayback = false;
  let muteVocal = false;
  let audioVolume = 0.8; // 0..1 audio volume
  let midiAudioCtx = null;
  let midiActiveNotes = new Map(); // noteId -> { osc, gain }
  let midiVolume = 0.25;

  // Loop region
  let loopEnabled = false;
  let loopStartBeat = null;  // beat where loop begins
  let loopEndBeat = null;    // beat where loop ends
  let isSettingLoop = false; // dragging on time axis to set loop
  let loopDragStartBeat = null; // beat where loop drag began
  let loopHandleDrag = null; // 'start' or 'end' when dragging a loop handle
  let hoverPasteBeat = null; // live beat under mouse cursor (used by Cmd+V target)

  // Playhead drag / scrub
  let playheadDrag = false;
  let scrubAudio = true; // hear audio while dragging playhead
  let scrubAudioBuffer = null; // decoded AudioBuffer for grain scrub
  let scrubCtx = null;         // AudioContext for scrub grains
  let scrubSource = null;      // current BufferSourceNode
  let scrubGain = null;        // gain node for scrub

  // Metronome
  let metronomeEnabled = false;
  let metronomeCtx = null;
  let lastMetronomeBeat = -1; // tracks which quarter-note beat we last clicked
  let metronomeOffset = 0;    // fallback offset when no downbeat anchor is defined
  let metronomeToolOpen = false;
  let metronomeToolX = 36;
  let metronomeToolY = 180;
  let metronomeToolDragging = false;
  let metronomeToolDragOffsetX = 0;
  let metronomeToolDragOffsetY = 0;
  let metronomePickTarget = 0; // 0 = idle, 1 = set first downbeat, 2 = set second downbeat
  let metronomePickHoverBeat = null;
  let metronomeDownbeat1Beat = null;
  let metronomeDownbeat2Beat = null;
  let metronomeManualDownbeatAnchorBeat = null;
  let metronomeManualDownbeatInterval = null;
  let metronomeManualBeatUnitInterval = null;
  let metronomeSigNumerator = 4;
  let metronomeSigDenominator = 4;
  let metronomeSpeedFactor = 1;
  const METRONOME_SIGNATURE_NUM_OPTIONS = [2, 3, 4, 5, 6, 7, 9, 12];
  const METRONOME_SIGNATURE_DEN_OPTIONS = [2, 4, 5, 8, 16];
  // BEATS_PER_QUARTER: US-BPM / 30 = quarter note duration in US beats (Bohning ×4 convention)
  // e.g. BPM=480 → 16, BPM=400 → ~13.3, BPM=200 → ~6.7. Recalculated reactively when bpm changes.
  $: BEATS_PER_QUARTER = bpm > 0 ? Math.round(bpm / 30) : 8;
  $: BEATS_PER_MEASURE = BEATS_PER_QUARTER * 4;

  function getMetronomeSignatureIntervalBeats() {
    const numerator = Number(metronomeSigNumerator);
    const denominator = Number(metronomeSigDenominator);
    if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) return null;
    const unitBeats = (BEATS_PER_QUARTER * 4) / denominator;
    const interval = unitBeats * numerator;
    if (!Number.isFinite(interval) || interval <= 0) return null;
    return interval;
  }

  function getMetronomeBeatUnitInterval() {
    if (metronomeManualBeatUnitInterval && metronomeManualBeatUnitInterval > 0) return metronomeManualBeatUnitInterval;
    return BEATS_PER_QUARTER;
  }

  function getMetronomeClickInterval() {
    return getMetronomeBeatUnitInterval();
  }

  function getMetronomeDownbeatAnchorBeat() {
    if (metronomeManualDownbeatAnchorBeat !== null) return metronomeManualDownbeatAnchorBeat;
    if (downbeatFromHeader) {
      const exactBeat = (downbeatOffsetMs - gapMs) * bpm / 15000;
      return Math.round(exactBeat);
    }
    return metronomeOffset;
  }

  function getMetronomeDownbeatInterval() {
    return metronomeManualDownbeatInterval || BEATS_PER_MEASURE;
  }

  function getMetronomeClickOffset() {
    const clickInterval = getMetronomeClickInterval();
    const anchorBeat = getMetronomeDownbeatAnchorBeat();
    return ((anchorBeat % clickInterval) + clickInterval) % clickInterval;
  }

  function clearMetronomePickTarget() {
    metronomePickTarget = 0;
    metronomePickHoverBeat = null;
    if (canvasEl && !setGapMode) canvasEl.style.cursor = '';
  }

  function armMetronomeDownbeatPick(target) {
    console.log(`[MetronomeTool] Arm pick target=${target} bpm=${bpm} BEATS_PER_QUARTER=${BEATS_PER_QUARTER}`);
    if (!metronomeEnabled) return;
    if (isPlaying) {
      showToast('Pause playback before setting downbeats');
      return;
    }
    if (setGapMode) {
      showToast('Exit GAP mode first');
      return;
    }
    metronomePickTarget = target;
    metronomePickHoverBeat = null;
    if (canvasEl) canvasEl.style.cursor = 'crosshair';
    showToast(`Metronome: click grid line for downbeat ${target}`);
    draw();
  }

  function recalcMetronomeFromControls(reason = 'manual') {
    const isLoad = reason === 'load';
    if (metronomeDownbeat1Beat === null) {
      console.log('[MetronomeTool] Recalc skipped (DB1 not set)', { reason });
      return false;
    }
    const numerator = Number(metronomeSigNumerator);
    const denominator = Number(metronomeSigDenominator);
    if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) {
      showToast('Invalid time signature');
      return false;
    }
    // Compute BEATS_PER_QUARTER directly from current bpm — do NOT use the reactive
    // $: variable because inside an async load function Svelte may not have re-evaluated
    // it yet, producing stale (wrong) intervals.
    const currentBpq = bpm > 0 ? Math.round(bpm / 30) : 8;
    const beatUnitInterval = (currentBpq * 4) / denominator;
    const downbeatInterval = beatUnitInterval * numerator;
    if (!Number.isFinite(beatUnitInterval) || !Number.isFinite(downbeatInterval) || beatUnitInterval <= 0 || downbeatInterval <= 0) {
      showToast('Cannot calculate metronome intervals from current BPM/signature');
      return false;
    }
    const speed = Math.max(0.25, Number(metronomeSpeedFactor) || 1);
    const effectiveBeatUnitInterval = beatUnitInterval / speed;
    const effectiveDownbeatInterval = downbeatInterval / speed;
    metronomeManualDownbeatAnchorBeat = metronomeDownbeat1Beat;
    metronomeManualBeatUnitInterval = effectiveBeatUnitInterval;
    metronomeManualDownbeatInterval = effectiveDownbeatInterval;
    lastMetronomeBeat = -1;
    console.log('[MetronomeTool] Recalculated', {
      reason,
      db1: metronomeDownbeat1Beat,
      numerator,
      denominator,
      bpm,
      currentBpq,
      reactiveBpq: BEATS_PER_QUARTER,
      beatUnitInterval,
      downbeatInterval,
      effectiveBeatUnitInterval,
      effectiveDownbeatInterval,
      speedFactor: metronomeSpeedFactor,
    });
    if (!isLoad) markUnsaved();
    draw();
    return true;
  }

  function nudgeMetronomeSpeed(direction) {
    const prev = metronomeSpeedFactor;
    if (direction === 'faster') {
      metronomeSpeedFactor = Math.min(8, metronomeSpeedFactor * 2);
    } else {
      metronomeSpeedFactor = Math.max(0.25, metronomeSpeedFactor / 2);
    }
    if (prev !== metronomeSpeedFactor) {
      lastMetronomeBeat = -1;
      showToast(`Metronome speed x${metronomeSpeedFactor}`);
      recalcMetronomeFromControls('speed-change');
      markUnsaved();
    }
  }

  function clearMetronomeDownbeatReference() {
    metronomeDownbeat1Beat = null;
    metronomeDownbeat2Beat = null;
    metronomeManualDownbeatAnchorBeat = null;
    metronomeManualDownbeatInterval = null;
    metronomeManualBeatUnitInterval = null;
    metronomeSpeedFactor = 1;
    clearMetronomePickTarget();
    lastMetronomeBeat = -1;
    showToast('Metronome downbeats reset');
    draw();
  }

  function applyManualMetronomeDownbeats() {
    return recalcMetronomeFromControls('legacy-apply');
  }

  // Downbeat offset: ms from audio 0s to first downbeat
  let downbeatOffsetMs = 0;
  let downbeatFromHeader = false; // true if loaded from #DOWNBEATOFFSET header

  // Waveform
  let waveformPeaks = [];   // pre-computed peaks array
  let waveformDuration = 0; // actual decoded audio duration (seconds)
  let showWaveform = true;
  let waveformHeight = 60; // px reserved at top of canvas for waveform (adjustable)

  // BPM Tapper modal
  let tapperOpen = false;
  let tapTimes = [];       // Array of Date.now() timestamps
  let tapBpm = null;       // Computed BPM from taps
  let tapLastTime = 0;     // For detecting stale session

  function openTapper() {
    tapTimes = [];
    tapBpm = null;
    tapLastTime = 0;
    tapperOpen = true;
  }

  function closeTapper() {
    tapperOpen = false;
  }

  function recordTap() {
    const now = Date.now();
    // Reset if more than 3 seconds since last tap (new session)
    if (tapLastTime > 0 && now - tapLastTime > 3000) {
      tapTimes = [];
    }
    tapTimes = [...tapTimes, now];
    tapLastTime = now;
    if (tapTimes.length >= 2) {
      const span = tapTimes[tapTimes.length - 1] - tapTimes[0];
      const intervals = tapTimes.length - 1;
      tapBpm = Math.round((intervals / span) * 60000 * 10) / 10; // 1 decimal
    }
  }

  function resetTapper() {
    tapTimes = [];
    tapBpm = null;
    tapLastTime = 0;
  }

  function applyTappedBpm() {
    if (!tapBpm) return;
    bpm = tapBpm;
    handleBpmChange();
    closeTapper();
  }

  function handleTapperKeydown(e) {
    if (!tapperOpen) return;
    if (e.key === 'Enter') { e.preventDefault(); recordTap(); }
    if (e.key === 'Escape') { closeTapper(); }
  }

  // Beat Marker BPM Calibration
  // Each marker: { t: seconds, bar: integer (1-based bar number in the song) }
  let beatMarkers = [];
  let savedBeatMarkers = []; // persists across cal sessions as grey reference dots
  let beatMarkerMode = false;
  let bpmCalcResult = null; // { bpm, gapMs } computed from linear regression

  // Audio source toggle (vocals vs full mix)
  let audioSource = 'vocals'; // 'vocals' | 'edited' | 'original'
  let originalUrl = '';
  let originalVocalUrl = ''; // frozen at load — never changed by splices
  let cleanedAudioAvailable = false; // true when cleaned_vocal_path exists on backend
  let cleanedAudioCacheBust = ''; // appended to /cleaned URL to force browser reload
  let hasVocalsAudio = true;
  let hasOriginalAudio = true;

  // Mic sing-along
  let micEnabled = false;
  let micShowTrail = true;
  let micStream = null;
  let micAudioCtx = null;
  let micAnalyser = null;
  let micSourceNode = null;
  let micDetector = null;
  let micInputBuffer = null;
  let micPitchTrail = [];    // array of { time, pitch, rawPitch, clarity }
  let micDevices = [];       // available audio input devices
  let micDeviceId = '';      // selected device (empty = default)
  let micClarityThreshold = 0.8;
  let micStarting = false; // true while mic is initializing
  let micRecorder = null;   // MediaRecorder for voice capture
  let micRecordedChunks = []; // recorded audio chunks
  let micRecordingStartTime = 0; // playback time when recording started
  let micGain = 1.0;        // mic volume gain (0-2)
  let micGainNode = null;   // GainNode for mic volume control
  let pitchTolerance = 1;   // semitone hit tolerance: 1=hard, 2=medium, 3=easy
  let micLevel = 0;         // current mic input level (0-1) for indicator
  let micPeakLevel = 0;     // peak-hold level for visibility
  let micOversteering = false;
  let micOversteerTimer = null;
  let micLevelTimer = null; // interval for level polling
  let micDisconnectHandled = false;
  let micTrackEndedHandler = null;
  let micTrackMuteHandler = null;
  let mediaDeviceChangeHandler = null;
  const MIC_EVENT_TOAST_MS = 4000;
  // Sticky prediction state for smoothing
  let micLastPitch = -1;
  let micPitchConfidence = 0;
  let micRecentPitches = []; // rolling window for median smoothing

  // USDX-style sung note tracking: Map<noteId, [{beat, sungPitch, isHit}]>
  let micNoteHits = new Map();
  let micShowRawTrail = false; // optional raw pitch trail for debugging

  // ── Segment recording ──
  // phase: 'idle' | 'armed' | 'preroll' | 'recording' | 'review'
  let segRecPhase = 'idle';
  let uiModalGuardActive = false;
  // Guard selected toolbar controls while any blocking modal/tool is open.
  $: uiModalGuardActive = segRecPhase !== 'idle' || segRegenModalOpen || vibratoModalOpen || metronomeToolOpen;
  $: recordingActive.set(uiModalGuardActive);
  let segRecSegmentId = null;       // cleanup segment being recorded
  let segRecPrerollSec = 1.5;       // seconds of pre-roll before recording starts
  let segRecRecorder = null;        // dedicated MediaRecorder for segment capture
  let segRecChunks = [];
  let segRecBlob = null;            // recorded blob ready for review/upload
  let segRecObjectUrl = null;       // object URL for review playback
  let segRecCountdown = 0;          // countdown display during preroll
  let segRecCountdownTimer = null;
  let segRecStopTimer = null;       // auto-stop at end of segment
  let segRecUploading = false;
  let segRecPatched = new Set();    // segment ids that have been successfully spliced
  let segRecApplied = false;        // true after successful splice for current modal segment
  let segRecLyricsLoading = false;
  let segRecLyricsError = '';
  let segRecLyricsLines = [];
  let segRecLyricsHyphenated = false;
  $: segRecAudioSwitchLocked = segRecUploading || segRecPhase === 'preroll' || segRecPhase === 'recording' || segRecPhase === 'armed';

  // Auto-regenerate cleaned audio after cleanup changes
  let cleanedAudioDirty = false;    // true when segments or vocal changed since last generation
  let isRegeneratingCleaned = false; // blocking modal while regenerating
  $: uiBusy = isSaving || isRegeneratingCleaned || segRecUploading || editedAudioLoading || waveformLoading;

  // Vocal trace (simulated mic from vocal audio file)
  let vocalTraceEnabled = false;
  let vocalTraceLoading = false;
  let vocalTraceAbortController = null;
  let vocalTraceVisible = true;         // show/hide toggle (data kept when false)
  let vocalTraceDecodedBuffer = null;   // decoded AudioBuffer of the vocal file
  let vocalTraceSampleBuf = null;       // reused Float32Array(2048) for pitch detection
  let vocalTraceDetector = null;        // PitchDetector instance
  let vocalTraceFrames = [];            // [{beat, pitch}] — all voiced frames, flat
  // Smoothing state (parallel to mic)
  let vocalTraceLastPitch = -1;
  let vocalTracePitchConfidence = 0;
  let vocalTraceRecentPitches = [];
  let quickTraceActive = false;
  let quickTraceEndSec = null;
  const TRACE_SCOPE = 'song'; // 'window' | 'song'
  const QUICK_TRACE_DURATION_SEC = 5;
  const TRACE_SCOPE_LABEL = TRACE_SCOPE === 'song' ? 'song' : `${QUICK_TRACE_DURATION_SEC}s`;
  const ANALYZE_SCOPE = 'song'; // 'window' | 'song'
  const ANALYZE_WINDOW_SEC = 5;
  const ANALYZE_SCOPE_LABEL = ANALYZE_SCOPE === 'song' ? 'song' : `${ANALYZE_WINDOW_SEC}s`;
  let liveWordTokens = [];
  let liveWordsVisible = true;
  // Fixed-grid sampling: sample every fixed number of seconds of audio time so the
  // same audio bytes are always sampled at the same positions regardless of rAF timing.
  const VOCAL_TRACE_STEP_SEC = 0.025;   // sample every 25ms (~0.67 beats at BPM 120)
  let vocalTraceNextSampleSec = 0;

  // Pitch line — precomputed offline pitch analysis of the full vocal file
  let pitchLineFrames = [];         // [{beat, pitch}] — original/baseline vocal (blue)
  let recordedPitchFrames = [];     // [{beat, pitch}] — recorded patches only (green)
  let pitchLineVisible = false;     // default off
  let pitchLineLoading = false;
  let recordedPitchLoading = false;
  let pitchLineSourceUrl = null;    // URL used for last computation (for cache invalidation)

  // Text editor modal
  let showTextEditor = false;
  let textEditorContent = '';

  // Session notes modal
  let showNotesModal = false;
  let sessionNotes = '';

  function loadSessionNotes() {
    if ($sessionId) sessionNotes = localStorage.getItem(`editor_notes_${$sessionId}`) || '';
  }
  function saveSessionNotes() {
    if ($sessionId) localStorage.setItem(`editor_notes_${$sessionId}`, sessionNotes);
  }

  // Extra Ultrastar headers (e.g. #YOUTUBE, #COVER, etc.) — preserved across edits
  let extraHeaders = [];

  // Total beats for scrollbar
  let totalBeats = 0;

  // Guard: which session has already loaded data (prevent reactive re-load)
  let dataLoadedSession = null;

  // Parse Ultrastar content into notes array
  function parseUltrastar(content) {
    const lines = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
    const parsed = [];
    let id = 0;

    for (const line of lines) {
      const trimmed = line.trimStart(); // trimStart only — trailing space is part of the syllable
      if (trimmed.startsWith('*') || trimmed.startsWith(':') || trimmed.startsWith('F:')) {
        const isGolden = trimmed.startsWith('*');
        const isRap = trimmed.startsWith('F:');
        let prefix;
        if (isRap) prefix = 'F:';
        else if (isGolden) prefix = '*';
        else prefix = ':';
        // Parse 3 numeric fields, then preserve the rest as syllable text
        // (including any leading space which signals a word boundary in Ultrastar)
        const rest = trimmed.substring(prefix.length);
        const match = rest.match(/^\s+(-?\d+)\s+(\d+)\s+(-?\d+) (.*)$/);
        
        if (match) {
          const startBeat = parseInt(match[1]);
          const duration = parseInt(match[2]);
          const pitch = parseInt(match[3]);
          const syllable = match[4];

          parsed.push({
            id: id++,
            startBeat,
            duration,
            pitch,
            syllable,
            isRap,
            isGolden: isGolden || false,
            confidence: 1.0,
            original: { startBeat, duration, pitch },
          });
        }
      } else if (trimmed.startsWith('-')) {
        // Break line — store for rendering
        const parts = trimmed.substring(1).trim().split(/\s+/);
        parsed.push({
          id: id++,
          type: 'break',
          startBeat: parseInt(parts[0]) || 0,
          endBeat: parseInt(parts[1]) || null,
        });
      }
    }

    return parsed;
  }

  // Calculate pitch range from notes
  function updatePitchRange() {
    const pitchNotes = notes.filter(n => n.pitch !== undefined && n.type !== 'break');
    if (pitchNotes.length === 0) return;
    
    const pitches = pitchNotes.map(n => n.pitch);
    const notesMin = Math.min(...pitches);
    const notesMax = Math.max(...pitches);
    minPitch = Math.min(notesMin - 6, 36);
    maxPitch = Math.max(notesMax + 6, 84);
    // Ensure at least 12 semitones visible (one octave)
    if (maxPitch - minPitch < 12) {
      const mid = (minPitch + maxPitch) / 2;
      minPitch = Math.floor(mid - 6);
      maxPitch = Math.ceil(mid + 6);
    }
  }

  // Beat to X pixel
  function beatToX(beat) {
    return (beat * zoom) - scrollX;
  }

  // X pixel to beat
  function xToBeat(x) {
    return (x + scrollX) / zoom;
  }

  // Waveform top offset (only when waveform is visible)
  function waveTop() {
    return DOWNBEAT_HANDLE_H + (showWaveform ? waveformHeight : 0);
  }

  // Pitch to Y pixel (piano area only, excluding time axis at bottom and waveform at top)
  function pitchToY(pitch) {
    const range = maxPitch - minPitch;
    const wt = waveTop();
    const pianoH = viewHeight - 22 - wt; // exclude time axis and waveform
    const ratio = (maxPitch - pitch) / range;
    return wt + ratio * (pianoH - 40) + 20;
  }

  // Y pixel to pitch
  function yToPitch(y) {
    const range = maxPitch - minPitch;
    const wt = waveTop();
    const pianoH = viewHeight - 22 - wt;
    const ratio = (y - wt - 20) / (pianoH - 40);
    return Math.round(maxPitch - ratio * range);
  }

  // Note name helper
  function noteName(midi) {
    const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
    return `${names[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;
  }

  // Beat to time (seconds) conversion
  // Standard Ultrastar: beats are quarter-beats, so time = gap + beat * 15 / BPM
  function beatToTime(beat) {
    const gapSec = gapMs / 1000;
    return gapSec + (beat * 15) / bpm;
  }

  function xToAudioMs(x) {
    return Math.max(0, beatToTime(xToBeat(x)) * 1000);
  }

  // Time to beat conversion
  function timeToBeat(timeSec) {
    const gapSec = gapMs / 1000;
    return ((timeSec - gapSec) * bpm) / 15;
  }

  // Minimum beat (corresponds to audio time 0) — negative when GAP > 0
  // Pad by 2 beats so the playhead at time 0 is visible and not clipped at the edge
  function getMinBeat() {
    return timeToBeat(0) - 2;
  }

  // Format seconds as m:ss.mmm
  function formatTime(seconds) {
    if (seconds < 0) seconds = 0;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    const ms = Math.floor((seconds % 1) * 1000);
    return `${m}:${s.toString().padStart(2, '0')}.${ms.toString().padStart(3, '0')}`;
  }

  // Time axis height
  const timeAxisHeight = 22;

  // Compute total beats from notes for scrollbar range
  function computeTotalBeats() {
    const realNotes = notes.filter(n => n.type !== 'break');
    if (realNotes.length === 0) {
      totalBeats = Math.max(100, timeToBeat(audioDuration || 60));
    } else {
      const last = realNotes[realNotes.length - 1];
      totalBeats = Math.max(last.startBeat + last.duration + 50, timeToBeat(audioDuration || 60));
    }
  }

  // Update scrollbar from scrollX
  function syncScrollbar() {
    // nothing to imperitively set — handle position is driven by reactive scrollHandlePct
  }

  // ──── BPM Re-quantization ────────────────────
  // Rebuild notes from raw ms timings using the current bpm/gapMs,
  // preserving pitches from the original Ultrastar parse.
  function requantizeFromMs(bpmActuallyChanged = false, previousTimingRef = null) {
    if (!rawTimings || rawTimings.length === 0) {
      console.log('[Requantize] No rawTimings, skipping — breaks keep existing timeMs');
      return;
    }
    console.log(`[Requantize] bpm=${bpm} gap=${gapMs}ms, ${rawTimings.length} timings`);

    // Preserve user edits (syllable, pitch) by capturing current note values indexed by rawTimings position.
    // Notes and rawTimings are in the same order (one note per timing entry), so we match by index.
    const currentNotes = notes.filter(n => n.type !== 'break');
    const userEdits = {}; // index in rawTimings → { syllable, pitch, isRap }
    let noteIdx = 0;
    for (let i = 0; i < rawTimings.length; i++) {
      if (noteIdx < currentNotes.length) {
        userEdits[i] = { syllable: currentNotes[noteIdx].syllable, pitch: currentNotes[noteIdx].pitch, isRap: currentNotes[noteIdx].isRap };
        noteIdx++;
      }
    }

    const gapSec = gapMs / 1000;
    // Preserve existing break placements by anchoring them to the previous timing
    // reference (old GAP/BPM), then mapping them into the current grid.
    const prevBpm = Math.max(1, Number(previousTimingRef?.bpm) || bpm);
    const prevGapSec = (Number.isFinite(previousTimingRef?.gapMs)
      ? Number(previousTimingRef.gapMs)
      : gapMs) / 1000;
    const preservedBreaks = notes
      .filter(n => n.type === 'break')
      .map(n => {
        const startBeat = Number(n.startBeat) || 0;
        const endBeat = n.endBeat == null ? null : Number(n.endBeat);
        return {
          startSec: prevGapSec + (startBeat * 15) / prevBpm,
          endSec: endBeat == null ? null : prevGapSec + (endBeat * 15) / prevBpm,
        };
      });
    const preserveExistingBreaks = preservedBreaks.length > 0;
    let id = 0;
    let prevLineIndex = null;
    let lastEndBeat = 0;
    const newNotes = [];

    for (let i = 0; i < rawTimings.length; i++) {
      const timing = rawTimings[i];
      const startSec = timing.start;
      const endSec = timing.end;
      const lineIndex = timing.line_index ?? 0;

      // Insert break between phrases only when there are no existing break edits to preserve.
      if (!preserveExistingBreaks && prevLineIndex !== null && lineIndex !== prevLineIndex) {
        const breakStart = lastEndBeat + 2;
        const nextStartBeat = Math.round(((startSec - gapSec) * bpm) / 15);
        const breakEnd = Math.max(breakStart + 1, nextStartBeat - 2);
        if (breakEnd > breakStart) {
          newNotes.push({ id: id++, type: 'break', startBeat: breakStart, endBeat: breakEnd,
            timeMs: beatToTime(breakStart) * 1000, endTimeMs: beatToTime(breakEnd) * 1000 });
        } else {
          newNotes.push({ id: id++, type: 'break', startBeat: breakStart, endBeat: null,
            timeMs: beatToTime(breakStart) * 1000, endTimeMs: null });
        }
      }

      // Use stored beat positions for direct proportional scaling (single Math.round, no drift).
      // Only use this path on actual BPM changes; for GAP-only changes use seconds so notes
      // stay at their absolute audio positions.
      let startBeat, duration;
      if (bpmActuallyChanged && timing.syncedBpm != null && timing.beatAtBpm != null) {
        startBeat = Math.round(timing.beatAtBpm * bpm / timing.syncedBpm);
        duration  = Math.max(1, Math.round(timing.durationAtBpm * bpm / timing.syncedBpm));
      } else {
        startBeat = Math.round(((timing.start - gapSec) * bpm) / 15);
        const endBeat = Math.max(startBeat + 1, Math.round(((timing.end - gapSec) * bpm) / 15));
        duration  = Math.max(1, endBeat - startBeat);
      }
      let endBeat = startBeat + duration;

      // Prevent overlap with previous note
      if (startBeat < lastEndBeat && newNotes.some(n => n.type !== 'break')) {
        startBeat = lastEndBeat + 1;
        endBeat = Math.max(startBeat + 1, endBeat);
        duration = endBeat - startBeat;
      }

      // Use user-edited syllable/pitch if available, otherwise fall back to rawTimings/pitchMap
      const editedSyllable = userEdits[i]?.syllable ?? timing.syllable;
      const editedPitch    = userEdits[i]?.pitch    ?? pitchMap[i] ?? 60;
      const editedIsRap    = userEdits[i]?.isRap    ?? timing.is_rap ?? false;

      newNotes.push({
        id: id++,
        startBeat,
        duration,
        pitch: editedPitch,
        syllable: editedSyllable,
        isRap: editedIsRap,
        confidence: timing.confidence ?? 1.0,
        original: { startBeat, duration, pitch: editedPitch },
      });

      lastEndBeat = startBeat + duration;
      prevLineIndex = lineIndex;
    }

    if (preserveExistingBreaks) {
      for (const b of preservedBreaks) {
        const startBeat = snapBeatValue(Math.round(((b.startSec - gapSec) * bpm) / 15));
        let endBeat = null;
        if (b.endSec !== null) {
          endBeat = Math.max(startBeat + 1, snapBeatValue(Math.round(((b.endSec - gapSec) * bpm) / 15)));
        }
        newNotes.push({ id: id++, type: 'break', startBeat, endBeat,
          timeMs: b.startSec * 1000,
          endTimeMs: b.endSec != null ? b.endSec * 1000 : null });
      }
    }

    notes = newNotes.sort((a, b) => {
      const byBeat = (a.startBeat ?? 0) - (b.startBeat ?? 0);
      if (byBeat !== 0) return byBeat;
      const aBreak = a.type === 'break' ? 0 : 1;
      const bBreak = b.type === 'break' ? 0 : 1;
      if (aBreak !== bBreak) return aBreak - bBreak;
      return (a.id ?? 0) - (b.id ?? 0);
    });
    console.log(`[Requantize] Built ${newNotes.filter(n => n.type !== 'break').length} notes, ${newNotes.filter(n => n.type === 'break').length} breaks`);
    // Keep rawTimings in sync so the next BPM change requantizes from these beat positions
    syncRawTimingsFromNotes();
    updatePitchRange();
    computeTotalBeats();
    draw();
  }

  // Track unsaved changes on note edits
  function markUnsaved() {
    hasUnsavedChanges = true;
    editorState.update(s => ({ ...s, hasChanges: true }));
  }

  // ──── Undo / Redo ───────────────────────────
  function snapshot() {
    return {
      notes: JSON.parse(JSON.stringify(notes)),
      cleanupSegments: JSON.parse(JSON.stringify(cleanupSegments)),
      bpm,
      gapMs,
      downbeatOffsetMs,
      downbeatFromHeader,
      extraHeaders: JSON.parse(JSON.stringify(extraHeaders)),
      rawTimings: JSON.parse(JSON.stringify(rawTimings)),
      metronomeDownbeat1Beat,
      metronomeSigNumerator,
      metronomeSigDenominator,
      metronomeSpeedFactor,
      metronomeManualDownbeatAnchorBeat,
      metronomeManualDownbeatInterval,
      metronomeManualBeatUnitInterval,
    };
  }

  function restoreSnapshot(snap) {
    notes = snap.notes;
    if (snap.cleanupSegments !== undefined) {
      cleanupSegments = snap.cleanupSegments.map(seg => normalizeCleanupSegment(seg));
      selectedCleanupSegment = null;
      cleanupDrag = null;
    }
    if (snap.bpm !== undefined) bpm = snap.bpm;
    if (snap.gapMs !== undefined) gapMs = snap.gapMs;
    if (snap.downbeatOffsetMs !== undefined) downbeatOffsetMs = snap.downbeatOffsetMs;
    if (snap.downbeatFromHeader !== undefined) downbeatFromHeader = snap.downbeatFromHeader;
    if (snap.extraHeaders !== undefined) extraHeaders = snap.extraHeaders;
    if (snap.rawTimings !== undefined) rawTimings = snap.rawTimings;
    if (snap.metronomeDownbeat1Beat !== undefined) metronomeDownbeat1Beat = snap.metronomeDownbeat1Beat;
    if (snap.metronomeSigNumerator !== undefined) metronomeSigNumerator = snap.metronomeSigNumerator;
    if (snap.metronomeSigDenominator !== undefined) metronomeSigDenominator = snap.metronomeSigDenominator;
    if (snap.metronomeSpeedFactor !== undefined) metronomeSpeedFactor = snap.metronomeSpeedFactor;
    if (snap.metronomeManualDownbeatAnchorBeat !== undefined) metronomeManualDownbeatAnchorBeat = snap.metronomeManualDownbeatAnchorBeat;
    if (snap.metronomeManualDownbeatInterval !== undefined) metronomeManualDownbeatInterval = snap.metronomeManualDownbeatInterval;
    if (snap.metronomeManualBeatUnitInterval !== undefined) metronomeManualBeatUnitInterval = snap.metronomeManualBeatUnitInterval;

    selectedNote = null;
    selectedNotes = new Set();
    closeContextMenu();
    markUnsaved();
    updatePitchRange();
    computeTotalBeats();
    draw();
  }

  function pushUndo() {
    undoStack.push(snapshot());
    if (undoStack.length > MAX_UNDO) undoStack.shift();
    redoStack = []; // new action clears redo
  }

  function undo() {
    if (undoStack.length === 0) return;
    redoStack.push(snapshot());
    restoreSnapshot(undoStack.pop());
    console.log(`[Undo] Restored (${undoStack.length} left, ${redoStack.length} redo)`);
  }

  function redo() {
    if (redoStack.length === 0) return;
    undoStack.push(snapshot());
    restoreSnapshot(redoStack.pop());
    console.log(`[Redo] Restored (${undoStack.length} undo, ${redoStack.length} left)`);
  }

  // Save current editor state to backend
  async function handleSave() {
    if (!$sessionId || isSaving) return;
    isSaving = true;
    const cleanTargets = serializeCleanupSegmentsForCleaning();
    // Show spinner immediately if we know regeneration will follow
    const willRegenerate = cleanedAudioDirty && cleanTargets.length > 0;
    if (willRegenerate) isRegeneratingCleaned = true;
    try {
      // Serialize notes for the API
      const noteData = notes.map(n => {
        if (n.type === 'break') {
          return { type: 'break', startBeat: n.startBeat, endBeat: n.endBeat || null };
        }
        return {
          startBeat: n.startBeat,
          duration: n.duration,
          pitch: n.pitch,
          syllable: n.syllable,
          isRap: n.isRap || false,
          isGolden: n.isGolden || false,
        };
      });

      // Include downbeat offset in extra headers for persistence
      const headersToSave = [...extraHeaders];
      if (downbeatOffsetMs !== 0) {
        headersToSave.push({ key: 'DOWNBEATOFFSET', value: String(Math.round(downbeatOffsetMs)) });
      }
      if (metronomeManualDownbeatAnchorBeat !== null) {
        const anchorMs = gapMs + (metronomeManualDownbeatAnchorBeat * 15000 / bpm);
        headersToSave.push({ key: 'METRONOMEANCHOR', value: String(Math.round(anchorMs)) });
        headersToSave.push({ key: 'METRONOMEIG', value: `${metronomeSigNumerator}/${metronomeSigDenominator}` });
        headersToSave.push({ key: 'METRONOMESPEED', value: String(metronomeSpeedFactor) });
        console.log(`%c[MetronomeTool] Saving headers: ANCHOR=${Math.round(anchorMs)} IG=${metronomeSigNumerator}/${metronomeSigDenominator} SPEED=${metronomeSpeedFactor} anchor_beat=${metronomeManualDownbeatAnchorBeat?.toFixed(3)}`, 'color:#7dd3fc');
      } else {
        console.log('[MetronomeTool] No metronome anchor set — not saving METRONOME* headers');
      }
      const result = await saveEditorState(
        $sessionId,
        noteData,
        bpm,
        gapMs,
        headersToSave,
        serializeCleanupSegments()
      );
      editCount = result.edit_count || editCount + 1;
      lastSaveTime = new Date();
      hasUnsavedChanges = false;
      editorState.update(s => ({ ...s, hasChanges: false }));
      console.log(`[Step4] Saved: ${result.note_count} notes, save #${editCount}`);

      // Auto-regenerate cleaned audio only for non-recorded cleanup segments.
      if (cleanedAudioDirty && cleanTargets.length > 0) {
        cleanedAudioDirty = false;
        isRegeneratingCleaned = true;
        try {
          await generateCleanedAudio($sessionId, cleanTargets);
          cleanedAudioAvailable = true;
          cleanedAudioCacheBust = `?v=${Date.now()}`;
          console.log('[Step4] Cleaned audio regenerated');
          // If currently listening to edited source, refresh it.
          if (audioSource === 'edited') {
            const newUrl = getEditedAudioUrl();
            currentAudioUrl = newUrl;
            await tick();
            if (audioEl) {
              editedAudioLoading = true;
              if (audioEl.src !== newUrl) audioEl.src = newUrl;
              audioEl.load();
            }
            loadWaveform(newUrl);
          }
        } catch (e) {
          cleanedAudioAvailable = false;
          cleanedAudioCacheBust = '';
          if (audioSource === 'edited') {
            switchAudioSource('vocals');
          }
          console.warn('[Step4] Cleaned audio regeneration failed:', e);
        } finally {
          isRegeneratingCleaned = false;
        }
      } else if (cleanedAudioDirty) {
        cleanedAudioDirty = false;
        cleanedAudioAvailable = false;
        cleanedAudioCacheBust = '';
        if (audioSource === 'edited') {
          const newUrl = getEditedAudioUrl();
          currentAudioUrl = newUrl;
          await tick();
          if (audioEl) {
            editedAudioLoading = true;
            if (audioEl.src !== newUrl) audioEl.src = newUrl;
            audioEl.load();
          }
          loadWaveform(newUrl);
        }
      }
    } catch (err) {
      console.error('[Step4] Save error:', err);
      errorMessage.set('Save failed: ' + err.message);
    } finally {
      isSaving = false;
    }
  }

  // Reload editor data from backend (discard unsaved changes)
  async function handleReload() {
    if (hasUnsavedChanges) {
      const ok = await showConfirm('Discard unsaved changes and reload from last save?', { confirmLabel: 'Discard', danger: true });
      if (!ok) return;
    }
    dataLoadedSession = null; // Force re-load
    await loadData();
    hasUnsavedChanges = false;
    editorState.update(s => ({ ...s, hasChanges: false }));
  }

  // Keyboard shortcut: Ctrl/Cmd+S to force save
  function handleKeydownSave(e) {
    if (showTextEditor) return; // skip when text editor is open
    if (showNotesModal) return; // skip when notes modal is open
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault();
      handleSave();
    }
  }

  function enterSetGapMode() {
    if (isPlaying) return;
    clearMetronomePickTarget();
    setGapMode = true;
    setGapHoverBeat = null;
    if (canvasEl) canvasEl.style.cursor = 'crosshair';
    // Scroll so the gap (beat 0) sits at 30% from the left edge
    const cw = canvasEl?.width || 800;
    const minScrollX = getMinBeat() * zoom;
    scrollX = Math.max(minScrollX, -cw * 0.3);
    draw();
  }

  function cancelSetGapMode() {
    setGapMode = false;
    setGapHoverBeat = null;
    if (canvasEl) canvasEl.style.cursor = '';
    draw();
  }

  /** Convert a ms offset to a pixel offset at the current zoom/bpm */
  function msToPixels(ms) {
    // 1 beat = zoom pixels, 1 beat = 15000/bpm ms
    // so 1ms = zoom * bpm / 15000 pixels
    return (ms / 1000) * (bpm / 15) * zoom;
  }

  /** Convert a pixel offset to ms at the current zoom/bpm */
  function pixelsToMs(px) {
    return (px / zoom) * (15000 / bpm);
  }

  /** Max allowed GAP time (seconds) = earliest raw timing start */
  function getMaxGapSec() {
    if (rawTimings && rawTimings.length > 0) {
      return Math.min(...rawTimings.map(t => t.start));
    }
    // Fallback: earliest note's time
    if (notes.length > 0) {
      const firstBeat = Math.min(...notes.filter(n => n.type !== 'break').map(n => n.startBeat));
      return beatToTime(firstBeat);
    }
    return Infinity;
  }

  /** Find the nearest visible grid line beat to a given x pixel */
  function nearestGridBeat(mx) {
    const beat = xToBeat(mx);
    // Snap to the nearest integer beat (every ultrastar beat is a grid line)
    // But only to lines that are actually visible at the current zoom level
    const beatsPerMeasure = BEATS_PER_QUARTER * 4;
    const beatsPerEighth = BEATS_PER_QUARTER / 2;
    let snapResolution;
    if (zoom >= 4) {
      snapResolution = 1;  // every beat line visible
    } else {
      snapResolution = beatsPerEighth; // only eighth-note and above lines visible
    }
    const snapped = Math.round(beat / snapResolution) * snapResolution;
    return snapped;
  }

  /** Snap a beat value to the nearest visible grid line */
  function snapBeatValue(beat) {
    const snapResolution = zoom >= 4 ? 1 : BEATS_PER_QUARTER / 2;
    return Math.round(beat / snapResolution) * snapResolution;
  }

  // Handle BPM or GAP adjustment — re-quantize visually
  /**
   * Snap the current GAP to the nearest beat of the current BPM grid.
   * Uses downbeatOffsetMs as the phase reference, same as confirmGridAlign.
   */
  function snapGapToGrid() {
    const beatDuration = 15000 / bpm; // ms per 1/8-note beat
    const gapRelToDownbeat = gapMs - downbeatOffsetMs;
    const beatsFromDownbeat = gapRelToDownbeat / beatDuration;
    const beatBefore = Math.floor(beatsFromDownbeat);
    const beatAfter  = Math.ceil(beatsFromDownbeat);
    const msBefore = downbeatOffsetMs + beatBefore * beatDuration;
    const msAfter  = downbeatOffsetMs + beatAfter  * beatDuration;
    const snapped = Math.round(
      Math.abs(gapMs - msBefore) <= Math.abs(gapMs - msAfter) ? msBefore : msAfter
    );
    console.log(`[BpmChange] snapGapToGrid: gap ${gapMs}ms → ${snapped}ms (beatDur=${beatDuration.toFixed(2)}ms)`);
    gapMs = snapped;
  }

  /** Called when only BPM changes — snaps GAP to nearest beat first, then requantizes. */
  function handleBpmChange() {
    // Clear undo/redo history — old snapshots reference beat positions at the previous BPM
    // and would produce corrupted notes if restored after a BPM change.
    undoStack = [];
    redoStack = [];
    // Clear pitch line — its beat positions were computed at the old BPM and would be misaligned
    pitchLineFrames = [];
    recordedPitchFrames = [];
    pitchLineVisible = false;
    pitchLineSourceUrl = null;
    // Clear vocal trace — beat positions are BPM-dependent
    vocalTraceFrames = [];
    vocalTraceEnabled = false;
    // Stop mic and clear trail — hit positions are beat-based at old BPM
    if (micEnabled) { micEnabled = false; stopMic(); }
    clearMicTrail();
    const previousTimingRef = {
      gapMs,
      bpm: previousBpm > 0 ? previousBpm : (Number(rawTimings?.[0]?.syncedBpm) || bpm),
    };
    previousBpm = bpm;
    snapGapToGrid();
    handleBpmGapChange(true, previousTimingRef);
    markUnsaved();
  }

  function handleBpmGapChange(bpmActuallyChanged = false, previousTimingRef = null) {
    bpmChanged = (bpm !== initialBpm || gapMs !== initialGap);
    // Recalculate playback cursor position with new BPM/GAP
    if (currentTimeSec > 0) {
      const gapSec = gapMs / 1000;
      playbackBeat = ((currentTimeSec - gapSec) * bpm) / 15;
    }
    requantizeFromMs(bpmActuallyChanged, previousTimingRef);
    resyncAllToGrid(previousTimingRef);
  }

  // ──── Beat Marker / BPM Calibration ────────────────────────────────
  function enterBeatMarkerMode() {
    // Reload markers from last session so user can verify / continue
    beatMarkers = savedBeatMarkers.length > 0 ? [...savedBeatMarkers] : [];
    bpmCalcResult = calcBpmFromMarkers(beatMarkers);
    beatMarkerMode = true;
    console.log(`[BpmCal] Entered calibration mode (loaded ${beatMarkers.length} saved markers)`);
  }

  function exitBeatMarkerMode() {
    // Save markers as grey reference before clearing
    if (beatMarkers.length > 0) savedBeatMarkers = [...beatMarkers];
    beatMarkerMode = false;
    beatMarkers = [];
    bpmCalcResult = null;
    console.log('[BpmCal] Exited calibration mode');
  }

  // Linear regression: time = a + b * (barNumber - 1)
  // b = seconds/bar → BPM = 480/b
  // a = time of bar 1 = Ultrastar GAP (first beat of the song)
  function calcBpmFromMarkers(markers) {
    if (markers.length < 2) return null;
    const n = markers.length;
    let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
    for (const m of markers) {
      const xi = m.bar - 1; // bar 1 → x=0, bar 2 → x=1, etc.
      sumX += xi; sumY += m.t;
      sumXY += xi * m.t; sumX2 += xi * xi;
    }
    const denom = n * sumX2 - sumX * sumX;
    if (denom === 0) return null;
    const b = (n * sumXY - sumX * sumY) / denom; // seconds per bar
    const a = (sumY - b * sumX) / n;             // time of bar 1 = GAP
    if (b <= 0) return null;
    const result = { bpm: Math.round(480 / b * 1000) / 1000, gapMs: Math.round(a * 1000) };
    console.log(`[BpmCal] Regression (${n} markers): slope=${b.toFixed(6)}s/bar → BPM=${result.bpm}, GAP=${result.gapMs}ms`);
    console.log('[BpmCal] Markers:', markers.map(m => `bar${m.bar}@${m.t.toFixed(3)}s`).join(', '));
    return result;
  }

  function applyBpmCalibration() {
    if (!bpmCalcResult) return;
    console.log(`[BpmCal] Applying: BPM ${bpm} → ${bpmCalcResult.bpm} (GAP stays at ${gapMs}ms, will snap to grid)`);
    pushUndo();
    bpm = bpmCalcResult.bpm;
    // Do NOT apply regression gapMs — GAP position is independent of beat detection.
    // Instead snap existing GAP to the nearest beat of the new BPM.
    handleBpmChange();
    markUnsaved();
    exitBeatMarkerMode();
  }

  // ──── Drawing ────────────────────────────────
  function draw() {
    if (!ctx || !canvasEl) return;

    const w = canvasEl.width;
    const h = canvasEl.height;
    const wt = waveTop();

    // Clear
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, w, h);

    // ── Draw waveform at top ──
    // Waveform uses CURRENT BPM/GAP so it always aligns with the beat grid.
    // pixel → beat (current) → time → audio sample
    if (showWaveform && waveformPeaks.length > 0 && bpm > 0) {
      ctx.fillStyle = '#0a0a18';
      ctx.fillRect(0, 0, w, wt);

      const sampleRate = waveformPeaks.length / (waveformDuration || audioDuration || 1);
      const midY = wt / 2;

      // Build per-pixel amplitude: max of all peaks that fall in each pixel's time range.
      // When zoomed in (pixel spans sub-sample), interpolate between neighbours.
      const amps = new Float32Array(w);
      for (let px = 0; px < w; px++) {
        const tL = beatToTime(xToBeat(px - 0.5));
        const tR = beatToTime(xToBeat(px + 0.5));
        const iL = Math.max(0, Math.floor(tL * sampleRate));
        const iR = Math.min(waveformPeaks.length - 1, Math.floor(tR * sampleRate));
        if (iR < 0 || iL >= waveformPeaks.length) { amps[px] = 0; continue; }
        if (iL === iR) {
          // zoomed in — interpolate
          const frac = (tL + tR) * 0.5 * sampleRate - iL;
          const next = Math.min(iR + 1, waveformPeaks.length - 1);
          amps[px] = waveformPeaks[iL] * (1 - frac) + waveformPeaks[next] * frac;
        } else {
          // zoomed out — take max of range
          let max = 0;
          for (let i = iL; i <= iR; i++) if (waveformPeaks[i] > max) max = waveformPeaks[i];
          amps[px] = max;
        }
      }

      // Draw as a single smooth filled path
      const scale = wt / 2;
      const waveformColor = audioSource === 'edited' ? '#68d4b0' : '#4fc3f7';
      ctx.fillStyle = waveformColor;
      ctx.globalAlpha = 0.35;
      ctx.beginPath();
      ctx.moveTo(0, midY);
      for (let px = 0; px < w; px++) ctx.lineTo(px, midY - amps[px] * scale);
      ctx.lineTo(w - 1, midY);
      for (let px = w - 1; px >= 0; px--) ctx.lineTo(px, midY + amps[px] * scale);
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 1;

      // Separator line
      ctx.strokeStyle = '#333';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, wt);
      ctx.lineTo(w, wt);
      ctx.stroke();

      // Cleanup segments overlay (multiple loop-like ranges for noisy vocal areas)
      for (const seg of cleanupSegments) {
        const sx = beatToX(timeToBeat(seg.startMs / 1000));
        const ex = beatToX(timeToBeat(seg.endMs / 1000));
        const left = Math.min(sx, ex);
        const right = Math.max(sx, ex);
        const width = right - left;
        if (right < -8 || left > w + 8) continue;
        const selected = selectedCleanupSegment === seg.id;
        const patched = segRecPatched.has(seg.id);
        const isRecordTarget = segRecSegmentId === seg.id && segRecPhase !== 'idle';

        if (isRecordTarget) {
          ctx.fillStyle = segRecPhase === 'recording'
            ? 'rgba(255, 60, 60, 0.45)'   // bright red while recording
            : 'rgba(255, 200, 0, 0.28)';  // yellow while armed/review
        } else if (patched) {
          ctx.fillStyle = selected ? 'rgba(100, 220, 100, 0.38)' : 'rgba(100, 220, 100, 0.22)';
        } else {
          ctx.fillStyle = selected ? 'rgba(255, 107, 107, 0.52)' : 'rgba(255, 107, 107, 0.4)';
        }
        ctx.fillRect(left, 0, width, wt);

        const handleW = 2;
        ctx.fillStyle = patched ? '#80e080' : '#ff6b6b';
        ctx.fillRect(left - handleW / 2, 0, handleW, wt);
        ctx.fillRect(right - handleW / 2, 0, handleW, wt);
      }

      // Predicted downbeats (red dots) — shown in calibration mode so user can verify grid alignment
      if (beatMarkerMode && bpm > 0) {
        const firstDownbeatSec = (downbeatOffsetMs || gapMs) / 1000;
        const secPerMeasure = 480 / bpm;
        // Find first visible bar index
        const leftTimeSec = beatToTime(xToBeat(0));
        const startN = Math.max(0, Math.floor((leftTimeSec - firstDownbeatSec) / secPerMeasure));
        const rightTimeSec = beatToTime(xToBeat(w));
        const endN = Math.ceil((rightTimeSec - firstDownbeatSec) / secPerMeasure);
        for (let n = startN; n <= endN; n++) {
          const t = firstDownbeatSec + n * secPerMeasure;
          const x = beatToX(timeToBeat(t));
          if (x < -6 || x > w + 6) continue;
          // Red tick at the bottom edge of the waveform
          ctx.fillStyle = '#ff4444';
          ctx.beginPath();
          ctx.arc(x, wt - 5, 3.5, 0, Math.PI * 2);
          ctx.fill();
          // Bar number label
          ctx.fillStyle = '#ff8888';
          ctx.font = '8px monospace';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'alphabetic';
          ctx.fillText(String(n + 1), x, wt - 10);
        }
      }

      // Beat calibration markers (orange clicks, active session)
      if (beatMarkers.length > 0) {
        beatMarkers.forEach((m, i) => {
          const x = beatToX(timeToBeat(m.t));
          if (x < -10 || x > w + 10) return;
          // Vertical line
          ctx.strokeStyle = i === 0 ? '#ff9f43' : '#ff6b35';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, wt - 1);
          ctx.stroke();
          // Diamond handle at top
          ctx.fillStyle = i === 0 ? '#ff9f43' : '#ff6b35';
          ctx.beginPath();
          ctx.moveTo(x, 1);
          ctx.lineTo(x + 7, 9);
          ctx.lineTo(x, 17);
          ctx.lineTo(x - 7, 9);
          ctx.closePath();
          ctx.fill();
          // Bar number label
          ctx.fillStyle = '#fff';
          ctx.font = 'bold 9px monospace';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(String(m.bar), x, 9);
          ctx.textBaseline = 'alphabetic';
        });
      }

      // Saved beat markers (grey reference) — always visible outside cal mode
      if (!beatMarkerMode && savedBeatMarkers.length > 0) {
        savedBeatMarkers.forEach(m => {
          const x = beatToX(timeToBeat(m.t));
          if (x < -10 || x > w + 10) return;
          ctx.strokeStyle = 'rgba(180,180,180,0.35)';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, wt - 1);
          ctx.stroke();
          ctx.fillStyle = 'rgba(180,180,180,0.5)';
          ctx.beginPath();
          ctx.moveTo(x, 1);
          ctx.lineTo(x + 5, 7);
          ctx.lineTo(x, 13);
          ctx.lineTo(x - 5, 7);
          ctx.closePath();
          ctx.fill();
          ctx.fillStyle = 'rgba(255,255,255,0.6)';
          ctx.font = 'bold 8px monospace';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(String(m.bar), x, 7);
          ctx.textBaseline = 'alphabetic';
        });
      }
    }

    // Reserve bottom strip for time axis
    const pianoH = h - timeAxisHeight;

    // Grid lines (pitch)
    const pitchRange = maxPitch - minPitch;
    for (let p = minPitch; p <= maxPitch; p++) {
      const y = pitchToY(p);
      if (y > pianoH) continue; // don't draw into time axis
      const isC = ((p % 12) + 12) % 12 === 0;
      
      ctx.strokeStyle = isC ? '#333' : '#1a1a2e';
      ctx.lineWidth = isC ? 1 : 0.5;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();

      // Pitch labels (only C notes)
      if (isC) {
        ctx.fillStyle = '#555';
        ctx.font = '10px monospace';
        ctx.fillText(noteName(p), 2, y - 2);
      }
    }

    // Grid lines (beats) — musical subdivisions
    // BEATS_PER_QUARTER = 8 ultrastar beats per real quarter note
    // Quarter note lines (thickest), 8th note lines (medium), fine lines (thinnest)
    const startBeat = xToBeat(0);
    const endBeat = xToBeat(w);
    const beatsPerMeasure = Math.max(0.0001, getMetronomeDownbeatInterval());
    const beatsPerBeatUnit = Math.max(0.0001, getMetronomeBeatUnitInterval());
    const beatsPerSubUnit = Math.max(0.0001, beatsPerBeatUnit / 2);
    // Finest grid step: 1 US beat if zoomed in enough, else beatsPerSubUnit
    const gridStep = zoom >= 4 ? 1 : beatsPerSubUnit;

    // Find the downbeat gridline beat — use fractional offset for sub-beat precision
    let downbeatBeat = getMetronomeDownbeatAnchorBeat();
    if (metronomeManualDownbeatAnchorBeat === null && downbeatFromHeader) {
      const exactBeat = (downbeatOffsetMs - gapMs) * bpm / 15000;
      downbeatBeat = exactBeat;
    }

    const nearMultiple = (value, step) => {
      const ratio = value / step;
      return Math.abs(ratio - Math.round(ratio)) < 1e-4;
    };
    const firstSubIndex = Math.floor((startBeat - downbeatBeat) / gridStep) - 2;
    const lastSubIndex = Math.ceil((endBeat - downbeatBeat) / gridStep) + 2;

    for (let i = firstSubIndex; i <= lastSubIndex; i++) {
      const b = downbeatBeat + i * gridStep;
      const x = beatToX(b);
      if (x < -1 || x > w + 1) continue;
      const rel = b - downbeatBeat;
      const isMeasure = nearMultiple(rel, beatsPerMeasure);
      const isBeatUnit = nearMultiple(rel, beatsPerBeatUnit);
      const isSubUnit = nearMultiple(rel, beatsPerSubUnit);
      // Highlight the beat level that the metronome is currently clicking on
      const metronomeInterval = getMetronomeClickInterval();
      const isMetronomeBeat = metronomeEnabled && nearMultiple(rel, metronomeInterval);

      if (isMeasure) {
        ctx.strokeStyle = isMetronomeBeat ? '#a0a0ff' : '#7070cc';
        ctx.lineWidth = 2;
      } else if (isBeatUnit) {
        ctx.strokeStyle = isMetronomeBeat ? '#8888ee' : '#404078';
        ctx.lineWidth = 1;
      } else if (isSubUnit) {
        ctx.strokeStyle = isMetronomeBeat ? '#6666cc' : '#30305a';
        ctx.lineWidth = 0.5;
      } else {
        // Individual US beat level — only visible when zoomed in
        ctx.strokeStyle = '#252545';
        ctx.lineWidth = 0.3;
      }
      ctx.beginPath();
      ctx.moveTo(x, wt);
      ctx.lineTo(x, pianoH);
      ctx.stroke();
    }

    // ── GAP marker (beat 0) — yellow dashed line ──
    {
      const gapX = beatToX(0); // No gridOffsetPx — GAP stays fixed during grid align
      if (gapX >= -1 && gapX <= w + 1) {
        ctx.save();
        ctx.strokeStyle = '#ffd700';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        ctx.moveTo(gapX, 0);
        ctx.lineTo(gapX, pianoH);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();
      }
    }

    // ── Set GAP mode: yellow hover highlight on nearest grid line ──
    if (setGapMode && setGapHoverBeat !== null) {
      const hoverX = beatToX(setGapHoverBeat);
      if (hoverX >= 0 && hoverX <= w) {
        ctx.save();
        ctx.strokeStyle = '#ffd700';
        ctx.lineWidth = 2.5;
        ctx.globalAlpha = 0.85;
        ctx.beginPath();
        ctx.moveTo(hoverX, 0);
        ctx.lineTo(hoverX, pianoH);
        ctx.stroke();
        // Small label
        ctx.globalAlpha = 1;
        ctx.fillStyle = '#ffd700';
        ctx.font = 'bold 11px monospace';
        ctx.textAlign = 'center';
        const hoverTimeSec = beatToTime(setGapHoverBeat);
        // Show where GAP would be set (the time at this beat position converted to new GAP)
        // New GAP = time of this grid line (since this line becomes beat 0)
        const newGapMs = Math.round(hoverTimeSec * 1000);
        ctx.fillText(`GAP ${newGapMs}ms`, hoverX, 12);
        ctx.restore();
      }
    }

    // ── Downbeat handle strip (top DOWNBEAT_HANDLE_H px) ──
    // Draw repeating diamonds at each downbeat position (only when metronome enabled + downbeat set)
    if (metronomeEnabled && metronomeManualDownbeatAnchorBeat !== null) {
      const anchorBeat = metronomeManualDownbeatAnchorBeat;
      const interval = getMetronomeDownbeatInterval();
      const diamondSize = 5;
      const cy = DOWNBEAT_HANDLE_H / 2;
      // Find first diamond in view
      const visStart = xToBeat(0);
      const visEnd = xToBeat(w);
      const firstIndex = Math.floor((visStart - anchorBeat) / interval) - 1;
      const lastIndex = Math.ceil((visEnd - anchorBeat) / interval) + 1;
      for (let i = firstIndex; i <= lastIndex; i++) {
        const beat = anchorBeat + i * interval;
        const x = beatToX(beat);
        if (x < -diamondSize || x > w + diamondSize) continue;
        const isHovered = downbeatHandleHovered || downbeatHandleDragging;
        ctx.save();
        ctx.fillStyle = isHovered ? '#a78bfa' : '#6d5ff0';
        ctx.strokeStyle = isHovered ? '#c4b5fd' : '#9b8ef8';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, cy - diamondSize);
        ctx.lineTo(x + diamondSize, cy);
        ctx.lineTo(x, cy + diamondSize);
        ctx.lineTo(x - diamondSize, cy);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        ctx.restore();
      }
    }

    // ── Metronome downbeat guide lines ──
    if (metronomeDownbeat1Beat !== null || metronomeDownbeat2Beat !== null) {
      const drawDownbeatGuide = (beat, label, color) => {
        if (beat === null) return;
        const x = beatToX(beat);
        if (x < 0 || x > w) return;
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, pianoH);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();
      };
      drawDownbeatGuide(metronomeDownbeat1Beat, 'DB1', '#7dd3fc');
      drawDownbeatGuide(metronomeDownbeat2Beat, 'DB2', '#22d3ee');
    }

    // ── Metronome pick hover: snapped dotted preview line ──
    if ((metronomePickTarget === 1 || metronomePickTarget === 2) && metronomePickHoverBeat !== null) {
      const hoverX = beatToX(metronomePickHoverBeat);
      if (hoverX >= 0 && hoverX <= w) {
        const label = metronomePickTarget === 1 ? 'Set DB1' : 'Set DB2';
        ctx.save();
        ctx.strokeStyle = '#7dd3fc';
        ctx.lineWidth = 2;
        ctx.setLineDash([3, 3]);
        ctx.globalAlpha = 0.95;
        ctx.beginPath();
        ctx.moveTo(hoverX, 0);
        ctx.lineTo(hoverX, pianoH);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = '#7dd3fc';
        ctx.font = 'bold 11px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(label, hoverX, 12);
        ctx.restore();
      }
    }

    // ── Time axis (bottom strip) ──
    ctx.fillStyle = '#12121e';
    ctx.fillRect(0, pianoH, w, timeAxisHeight);
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, pianoH);
    ctx.lineTo(w, pianoH);
    ctx.stroke();

    // Determine a nice time interval based on zoom
    // At low zoom we want bigger intervals, at high zoom smaller ones
    const pixelsPerSecond = (bpm / 15) * zoom;
    let timeStep; // seconds between labels
    if (pixelsPerSecond > 40) timeStep = 1;
    else if (pixelsPerSecond > 15) timeStep = 5;
    else if (pixelsPerSecond > 5) timeStep = 10;
    else if (pixelsPerSecond > 2) timeStep = 30;
    else timeStep = 60;

    const startTimeSec = beatToTime(xToBeat(0));
    const endTimeSec = beatToTime(xToBeat(w));
    const firstTick = Math.ceil(startTimeSec / timeStep) * timeStep;

    ctx.fillStyle = '#888';
    ctx.font = '10px monospace';
    ctx.textAlign = 'center';

    for (let t = firstTick; t <= endTimeSec; t += timeStep) {
      const beat = timeToBeat(t);
      const x = beatToX(beat);

      // Tick mark
      ctx.strokeStyle = '#555';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, pianoH);
      ctx.lineTo(x, pianoH + 5);
      ctx.stroke();

      // Label
      ctx.fillText(formatTime(t), x, pianoH + 16);
    }

    ctx.textAlign = 'left'; // Reset

    // Draw notes
    for (const note of notes) {
      if (note.type === 'break') {
        // Draw break line
        const x = beatToX(note.startBeat);
        const isBreakSelected = selectedNote === note.id;
        ctx.strokeStyle = isBreakSelected ? '#ff3b30' : '#ef5350';
        ctx.lineWidth = isBreakSelected ? 4 : 3;
        ctx.setLineDash(isBreakSelected ? [6, 4] : [5, 3]);
        ctx.beginPath();
        ctx.moveTo(x, wt);
        ctx.lineTo(x, pianoH);
        ctx.stroke();
        ctx.setLineDash([]);

        // Draw drag handle (diamond shape at center)
        const handleY = (wt + pianoH) / 2;
        const hs = isBreakSelected ? 8 : 6;
        ctx.fillStyle = isBreakSelected ? '#ff3b30' : '#ef5350';
        ctx.beginPath();
        ctx.moveTo(x, handleY - hs);
        ctx.lineTo(x + hs, handleY);
        ctx.lineTo(x, handleY + hs);
        ctx.lineTo(x - hs, handleY);
        ctx.closePath();
        ctx.fill();

        // Show beat label when selected
        if (isBreakSelected) {
          ctx.fillStyle = '#ef5350';
          ctx.font = '9px monospace';
          ctx.textAlign = 'center';
          ctx.fillText(`break @${note.startBeat}`, x, handleY - hs - 4);
          ctx.textAlign = 'left';
        }
        continue;
      }

      const x = beatToX(note.startBeat);
      const y = pitchToY(note.pitch);
      const width = note.duration * zoom;

      // Note rectangle
      const isSelected = selectedNote === note.id || selectedNotes.has(note.id);
      const isCut = cutNoteIds.has(note.id);
      const hasChanged = note.original && (
        note.startBeat !== note.original.startBeat ||
        note.duration !== note.original.duration ||
        note.pitch !== note.original.pitch
      );

      // Cut notes are semi-transparent
      const cutAlpha = isCut ? '44' : '';

      // When mic or vocal trace is enabled, notes become hollow (faint fill + clear border) — USDX style
      const micHollow = (micEnabled && micShowTrail) || vocalTraceEnabled;

      if (note.isGolden) {
        ctx.fillStyle = micHollow ? (isSelected ? '#ffd70033' : '#ffd70012') : (isSelected ? '#ffd70088' : (isCut ? '#ffd70022' : '#ffd70044'));
        ctx.strokeStyle = isCut ? '#ffd70066' : '#ffd700';
      } else if (note.isRap) {
        ctx.fillStyle = micHollow ? (isSelected ? '#ff980033' : '#ff980012') : (isSelected ? '#ff980088' : (isCut ? '#ff980022' : '#ff980044'));
        ctx.strokeStyle = isCut ? '#ff980066' : '#ff9800';
      } else if (hasChanged) {
        ctx.fillStyle = micHollow ? (isSelected ? '#fdd83533' : '#fdd83512') : (isSelected ? '#fdd83588' : (isCut ? '#fdd83522' : '#fdd83544'));
        ctx.strokeStyle = isCut ? '#fdd83566' : '#fdd835';
      } else {
        ctx.fillStyle = micHollow ? (isSelected ? '#4fc3f733' : '#4fc3f712') : (isSelected ? '#4fc3f788' : (isCut ? '#4fc3f722' : '#4fc3f744'));
        ctx.strokeStyle = isCut ? '#4fc3f766' : '#4fc3f7';
      }

      ctx.lineWidth = isSelected ? 2 : 1;
      ctx.fillRect(x, y - noteHeight / 2, width, noteHeight);
      ctx.strokeRect(x, y - noteHeight / 2, width, noteHeight);

      // Golden star indicator
      if (note.isGolden && width > 14) {
        ctx.fillStyle = '#ffd700';
        ctx.font = 'bold 9px sans-serif';
        ctx.fillText('★', x + width - 12, y + 3);
      }

      // Syllable text — show · after syllable if it has a trailing space (space visualisation)
      if (zoom > 1 && width > 10) {
        const hasTrailingSpace = note.syllable.endsWith(' ');
        const displayText = note.syllable.trim() + (hasTrailingSpace ? '·' : '');
        ctx.fillStyle = hasTrailingSpace ? '#7ecbf7' : '#eee';
        ctx.font = '10px sans-serif';
        ctx.fillText(displayText, x + 2, y + 3);
      }

      // Red dot indicator: this note has no trailing space (word continues into next note)
      if (!note.syllable.endsWith(' ')) {
        const dotR = 3;
        ctx.fillStyle = '#ef5350';
        ctx.beginPath();
        ctx.arc(x + width - dotR - 1, y - noteHeight / 2 + dotR + 1, dotR, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // ── Pitch line: precomputed full-song pitch drawn as thin continuous dots (behind everything) ──
    if (pitchLineVisible && pitchLineFrames.length > 0) {
      const visibleStartBeat = xToBeat(0);
      const visibleEndBeat = xToBeat(w);
      ctx.fillStyle = 'rgba(0, 220, 255, 0.55)';
      const dotH = Math.max(2, noteHeight * 0.3);
      for (let i = 0; i < pitchLineFrames.length; i++) {
        const { beat, pitch } = pitchLineFrames[i];
        if (beat < visibleStartBeat - 1 || beat > visibleEndBeat + 1) continue;
        const x = beatToX(beat);
        const y = pitchToY(pitch);
        ctx.fillRect(x, y - dotH / 2, 2, dotH);
      }
    }

    // Green pitch line — recorded patch regions
    if (pitchLineVisible && recordedPitchFrames.length > 0) {
      const visibleStartBeat = xToBeat(0);
      const visibleEndBeat = xToBeat(w);
      ctx.fillStyle = 'rgba(80, 240, 120, 0.70)';
      const dotH = Math.max(2, noteHeight * 0.3);
      for (let i = 0; i < recordedPitchFrames.length; i++) {
        const { beat, pitch } = recordedPitchFrames[i];
        if (beat < visibleStartBeat - 1 || beat > visibleEndBeat + 1) continue;
        const x = beatToX(beat);
        const y = pitchToY(pitch);
        ctx.fillRect(x, y - dotH / 2, 2, dotH);
      }
    }

    // ── Vocal trace: note-bound hit/miss blocks (mirrors mic sing-along) ──
    if (vocalTraceVisible && vocalTraceFrames.length > 0) {
      const visibleStartBeat = xToBeat(0);
      const visibleEndBeat = xToBeat(w);
      const hasDrawableNotes = notes.some(n => n.type !== 'break');
      // Estimate beat gap between frames (for visual continuity)
      const vtBeatGap = vocalTraceFrames.length > 1
        ? Math.abs(vocalTraceFrames[1].beat - vocalTraceFrames[0].beat) * 1.5
        : 0.15;

      // Lyrics-only / empty-note mode fallback: draw raw trace so users can still
      // validate short trace runs before any notes exist.
      if (!hasDrawableNotes) {
        ctx.fillStyle = 'rgba(255, 80, 180, 0.7)';
        const dotH = Math.max(2, noteHeight * 0.55);
        for (let i = 0; i < vocalTraceFrames.length; i++) {
          const frame = vocalTraceFrames[i];
          if (frame.beat < visibleStartBeat - 1 || frame.beat > visibleEndBeat + 1) continue;
          if (isPlaying && vocalTraceEnabled && frame.beat > playbackBeat) break;
          const x = beatToX(frame.beat);
          const y = pitchToY(frame.pitch);
          ctx.fillRect(x, y - dotH / 2, 2, dotH);
        }
      }

      for (const note of notes) {
        if (note.type === 'break') continue;
        const noteEndBeat = note.startBeat + note.duration;
        if (noteEndBeat < visibleStartBeat - 1 || note.startBeat > visibleEndBeat + 1) continue;

        // Binary search: first frame at or after note.startBeat
        let lo = 0, hi = vocalTraceFrames.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (vocalTraceFrames[mid].beat < note.startBeat) lo = mid + 1;
          else hi = mid;
        }

        const noteY = pitchToY(note.pitch);
        const hitColor = note.isGolden ? 'rgba(255, 215, 0, 0.65)'
                       : note.isRap    ? 'rgba(255, 152, 0, 0.65)'
                       :                 'rgba(255, 80, 180, 0.65)';
        const missColor = 'rgba(255, 140, 50, 0.5)';

        let i = lo;
        let firstHitDrawn = false; // track if we've drawn the first hit block for this note
        while (i < vocalTraceFrames.length && vocalTraceFrames[i].beat <= noteEndBeat) {
          const frame = vocalTraceFrames[i];
          // Don't draw frames ahead of the playhead during active recording.
          // When vocal trace is off (view-only) or paused, show all recorded frames.
          if (isPlaying && vocalTraceEnabled && frame.beat > playbackBeat) break;
          // Octave-correct the frame pitch toward the note (same as mic sing-along)
          let framePitch = frame.pitch;
          while (framePitch - note.pitch > 6)  framePitch -= 12;
          while (framePitch - note.pitch < -6) framePitch += 12;
          const isHit = Math.abs(framePitch - note.pitch) <= pitchTolerance;

          if (isHit) {
            let endBeat = frame.beat;
            while (i + 1 < vocalTraceFrames.length
                && vocalTraceFrames[i + 1].beat <= noteEndBeat) {
              let fp = vocalTraceFrames[i + 1].pitch;
              while (fp - note.pitch > 6)  fp -= 12;
              while (fp - note.pitch < -6) fp += 12;
              if (Math.abs(fp - note.pitch) > pitchTolerance) break;
              i++; endBeat = vocalTraceFrames[i].beat;
            }
            // Extend first hit block back to note start if it began within 2 beats
            // (compensates for rAF warmup jitter — the sampler may miss the first few frames)
            const hitStart = !firstHitDrawn && (frame.beat - note.startBeat) <= 2.0
              ? note.startBeat
              : frame.beat;
            firstHitDrawn = true;
            const xStart = beatToX(Math.max(hitStart, note.startBeat));
            const xEnd   = beatToX(Math.min(endBeat + vtBeatGap, noteEndBeat));
            ctx.fillStyle = hitColor;
            ctx.fillRect(xStart, noteY - noteHeight / 2, Math.max(xEnd - xStart, 2), noteHeight);
          } else {
            const missPitch = frame.pitch;
            let endBeat = frame.beat;
            while (i + 1 < vocalTraceFrames.length
                && vocalTraceFrames[i + 1].beat <= noteEndBeat
                && vocalTraceFrames[i + 1].pitch === missPitch) {
              i++; endBeat = vocalTraceFrames[i].beat;
            }
            const missDurationBeat = Math.max(0, endBeat - frame.beat);
            const nearStartEdge = (frame.beat - note.startBeat) <= Math.max(0.5, vtBeatGap);
            const nearEndEdge = (noteEndBeat - endBeat) <= Math.max(0.5, vtBeatGap);
            // Ignore tiny edge flicker misses to avoid noisy overlap on otherwise-correct notes.
            if (missDurationBeat < Math.max(0.7, vtBeatGap) && (nearStartEdge || nearEndEdge)) {
              i++;
              continue;
            }
            const missY  = pitchToY(missPitch);
            const xStart = beatToX(frame.beat);
            const xEnd   = beatToX(endBeat);
            ctx.fillStyle = missColor;
            ctx.fillRect(xStart, missY - noteHeight / 2, Math.max(xEnd - xStart, 2), noteHeight);
          }
          i++;
        }
      }
    }

    // ── Live word tokens (from analyze-at-cursor) ──
    if (liveWordsVisible && liveWordTokens.length > 0) {
      ctx.save();
      ctx.font = '11px sans-serif';
      ctx.textBaseline = 'middle';
      for (const tok of liveWordTokens) {
        const beat = timeToBeat(tok.start);
        const x = beatToX(beat);
        if (x < -60 || x > w + 60) continue;
        const label = tok.word || '';
        if (!label) continue;
        const tw = ctx.measureText(label).width;
        const padX = 6;
        const boxW = tw + padX * 2;
        const boxH = 18;
        const y = showWaveform ? waveformHeight + 10 : 12;
        ctx.fillStyle = 'rgba(38, 166, 154, 0.78)';
        ctx.fillRect(x - boxW / 2, y - boxH / 2, boxW, boxH);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(label, x - tw / 2, y);
      }
      ctx.restore();
    }

    // ── USDX-style sung note blocks ──
    if (micShowTrail && micNoteHits.size > 0) {
      const visibleStartBeat = xToBeat(0);
      const visibleEndBeat = xToBeat(w);

      for (const note of notes) {
        if (note.type === 'break') continue;
        const hits = micNoteHits.get(note.id);
        if (!hits || hits.length === 0) continue;

        const noteEndBeat = note.startBeat + note.duration;
        // Skip notes fully outside visible area
        if (noteEndBeat < visibleStartBeat - 1 || note.startBeat > visibleEndBeat + 1) continue;

        const noteY = pitchToY(note.pitch);
        // Choose hit color based on note type
        const hitColor = note.isGolden ? 'rgba(255, 215, 0, 0.65)'
                       : note.isRap ? 'rgba(255, 152, 0, 0.65)'
                       : 'rgba(102, 187, 106, 0.7)';
        const missColor = 'rgba(255, 100, 100, 0.45)';

        // Estimate beat gap per frame for extending segments
        const beatGap = hits.length > 1 ? Math.abs(hits[1].beat - hits[0].beat) * 1.5 : 0.3;

        const noteXStart = beatToX(note.startBeat);
        const noteXEnd   = beatToX(noteEndBeat);

        let i = 0;
        while (i < hits.length) {
          const sample = hits[i];
          if (sample.isHit) {
            // Group consecutive hits into one filled segment inside the target note
            let endBeat = sample.beat;
            while (i + 1 < hits.length && hits[i + 1].isHit) {
              i++;
              endBeat = hits[i].beat;
            }
            // Extend slightly so consecutive frames connect visually
            const xStart = beatToX(Math.max(sample.beat, note.startBeat));
            const xEnd = beatToX(Math.min(endBeat + beatGap, noteEndBeat));
            ctx.fillStyle = hitColor;
            ctx.fillRect(xStart, noteY - noteHeight / 2, Math.max(xEnd - xStart, 2), noteHeight);
          } else {
            // Group consecutive misses at the same pitch
            const missPitch = sample.sungPitch;
            let endBeat = sample.beat;
            while (i + 1 < hits.length && !hits[i + 1].isHit && hits[i + 1].sungPitch === missPitch) {
              i++;
              endBeat = hits[i].beat;
            }
            const missY = pitchToY(missPitch);
            const xStart = beatToX(sample.beat);
            const xEnd = beatToX(endBeat);
            ctx.fillStyle = missColor;
            ctx.fillRect(xStart, missY - noteHeight / 2, Math.max(xEnd - xStart, 2), noteHeight);
          }
          i++;
        }
      }
    }

    // ── Optional raw pitch trail (debug) ──
    if (micShowRawTrail && micPitchTrail.length > 0) {
      const visibleStartBeat = xToBeat(0);
      const visibleEndBeat = xToBeat(w);
      ctx.fillStyle = 'rgba(255, 200, 50, 0.4)';
      for (let i = 0; i < micPitchTrail.length; i++) {
        const s = micPitchTrail[i];
        const beat = timeToBeat(s.time);
        if (beat < visibleStartBeat - 1 || beat > visibleEndBeat + 1) continue;
        const x = beatToX(beat);
        const y = pitchToY(s.pitch);
        if (y < wt || y > h - timeAxisHeight) continue;
        ctx.beginPath();
        ctx.arc(x, y, 1.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // ── Loop region overlay ──
    if (loopEnabled && loopStartBeat !== null && loopEndBeat !== null) {
      const lx1 = beatToX(Math.min(loopStartBeat, loopEndBeat));
      const lx2 = beatToX(Math.max(loopStartBeat, loopEndBeat));
      const pianoBottom = h - timeAxisHeight;

      // Translucent blue fill
      ctx.fillStyle = '#42a5f522';
      ctx.fillRect(lx1, 0, lx2 - lx1, pianoBottom);

      // Loop boundary lines
      ctx.strokeStyle = '#42a5f5';
      ctx.lineWidth = 2;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(lx1, 0); ctx.lineTo(lx1, pianoBottom);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(lx2, 0); ctx.lineTo(lx2, pianoBottom);
      ctx.stroke();

      // Loop region on time axis
      ctx.fillStyle = '#42a5f544';
      ctx.fillRect(lx1, pianoBottom, lx2 - lx1, timeAxisHeight);

      // Loop drag handles (triangles at top of boundary lines)
      const handleSize = 8;
      // Left handle (▶ pointing right)
      ctx.fillStyle = '#42a5f5';
      ctx.beginPath();
      ctx.moveTo(lx1, 0);
      ctx.lineTo(lx1 + handleSize, handleSize);
      ctx.lineTo(lx1, handleSize * 2);
      ctx.closePath();
      ctx.fill();
      // Right handle (◀ pointing left)
      ctx.beginPath();
      ctx.moveTo(lx2, 0);
      ctx.lineTo(lx2 - handleSize, handleSize);
      ctx.lineTo(lx2, handleSize * 2);
      ctx.closePath();
      ctx.fill();

      // Bottom handles on time axis
      ctx.beginPath();
      ctx.moveTo(lx1, pianoBottom + timeAxisHeight);
      ctx.lineTo(lx1 + handleSize, pianoBottom + timeAxisHeight - handleSize);
      ctx.lineTo(lx1, pianoBottom + timeAxisHeight - handleSize * 2);
      ctx.closePath();
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(lx2, pianoBottom + timeAxisHeight);
      ctx.lineTo(lx2 - handleSize, pianoBottom + timeAxisHeight - handleSize);
      ctx.lineTo(lx2, pianoBottom + timeAxisHeight - handleSize * 2);
      ctx.closePath();
      ctx.fill();

      // Loop label
      ctx.fillStyle = '#42a5f5';
      ctx.font = 'bold 9px monospace';
      ctx.textAlign = 'center';
      ctx.fillText('LOOP', (lx1 + lx2) / 2, pianoBottom + 16);
      ctx.textAlign = 'left';
    }

    // Playback cursor — always visible so user can see position at time 0
    {
      const cx = beatToX(playbackBeat);
      const pianoBottom = h - timeAxisHeight;
      ctx.strokeStyle = isPlaying ? '#ff5252' : '#ff8a80';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx, 0);
      ctx.lineTo(cx, pianoBottom);
      ctx.stroke();

      // Playhead drag handle (inverted triangle ▼ at top)
      if (!isPlaying) {
        const hs = 7;
        ctx.fillStyle = '#ff8a80';
        ctx.beginPath();
        ctx.moveTo(cx - hs, 0);
        ctx.lineTo(cx + hs, 0);
        ctx.lineTo(cx, hs * 1.5);
        ctx.closePath();
        ctx.fill();
        // Small triangle at bottom (on time axis)
        ctx.beginPath();
        ctx.moveTo(cx - hs, pianoBottom + timeAxisHeight);
        ctx.lineTo(cx + hs, pianoBottom + timeAxisHeight);
        ctx.lineTo(cx, pianoBottom + timeAxisHeight - hs * 1.5);
        ctx.closePath();
        ctx.fill();
      }
    }

    // ── Paste ghost preview ──
    if (pasteMode && clipboard && pastePreviewBeat !== null) {
      ctx.globalAlpha = 0.4;
      for (const cn of clipboard.notes) {
        const gx = beatToX(pastePreviewBeat + cn.startBeat);
        const gy = pitchToY(cn.pitch);
        const gw = cn.duration * zoom;
        ctx.fillStyle = '#69f0ae';
        ctx.strokeStyle = '#69f0ae';
        ctx.lineWidth = 1;
        ctx.fillRect(gx, gy - noteHeight / 2, gw, noteHeight);
        ctx.strokeRect(gx, gy - noteHeight / 2, gw, noteHeight);
        if (zoom > 1 && gw > 10) {
          ctx.fillStyle = '#fff';
          ctx.font = '10px sans-serif';
          ctx.fillText(cn.syllable.trim(), gx + 2, gy + 3);
        }
      }
      ctx.globalAlpha = 1.0;

      // Paste insertion line
      const px = beatToX(pastePreviewBeat);
      const pianoBottom = h - timeAxisHeight;
      ctx.strokeStyle = '#69f0ae';
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(px, 0);
      ctx.lineTo(px, pianoBottom);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // ── Rubber-band selection box ──
    if (isBoxSelecting) {
      const bx = Math.min(boxSelectStart.x, boxSelectEnd.x);
      const by = Math.min(boxSelectStart.y, boxSelectEnd.y);
      const bw = Math.abs(boxSelectEnd.x - boxSelectStart.x);
      const bh = Math.abs(boxSelectEnd.y - boxSelectStart.y);
      ctx.fillStyle = '#42a5f518';
      ctx.strokeStyle = '#42a5f5';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.fillRect(bx, by, bw, bh);
      ctx.strokeRect(bx, by, bw, bh);
      ctx.setLineDash([]);
    }

    // ── Draw flags ──────────────────────────────────────────────────────
    for (const flag of flags) {
      const fx = beatToX(flag.beat);
      if (fx < -10 || fx > w + 10) continue;
      const isFlagSelected = selectedFlag === flag.id;
      ctx.strokeStyle = isFlagSelected ? '#22c55e' : '#4ade80';
      ctx.lineWidth = isFlagSelected ? 3.5 : 2.5;
      ctx.setLineDash(isFlagSelected ? [6, 4] : [5, 3]);
      ctx.beginPath();
      ctx.moveTo(fx, wt);
      ctx.lineTo(fx, pianoH);
      ctx.stroke();
      ctx.setLineDash([]);
      // Diamond handle at vertical center
      const ths = isFlagSelected ? 8 : 6;
      const thy = (wt + pianoH) / 2;
      ctx.fillStyle = isFlagSelected ? '#22c55e' : '#4ade80';
      ctx.beginPath();
      ctx.moveTo(fx, thy - ths);
      ctx.lineTo(fx + ths, thy);
      ctx.lineTo(fx, thy + ths);
      ctx.lineTo(fx - ths, thy);
      ctx.closePath();
      ctx.fill();
      if (isFlagSelected) {
        ctx.fillStyle = '#22c55e';
        ctx.font = '9px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`flag @${flag.beat}`, fx, thy - ths - 4);
        ctx.textAlign = 'left';
      }
    }

    syncScrollbar();
  }

  // ──── Interaction ────────────────────────────
  function handleMouseDown(event) {
    const rect = canvasEl.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;

    const beat = xToBeat(mx);
    const pitch = yToPitch(my);
    console.log(`[Mouse] mouseDown at px(${mx.toFixed(0)}, ${my.toFixed(0)}) beat=${beat.toFixed(1)} pitch=${pitch}`);

    // Ignore right-click — let contextmenu handler deal with it
    if (event.button === 2) return;

    // Waveform cleanup segment drag
    if (showWaveform && my < waveTop()) {
      const hit = hitTestCleanupSegment(mx, my);
      if (hit) {
        const seg = cleanupSegments.find(s => s.id === hit.id);
        if (seg) {
          clearMarkerSelection();
          // Don't allow dragging segments with recordings
          if (segRecPatched.has(seg.id)) {
            selectedCleanupSegment = seg.id;
            draw();
            return;
          }
          pushUndo();
          selectedCleanupSegment = seg.id;
          cleanupDrag = {
            id: seg.id,
            mode: hit.mode,
            startMs: seg.startMs,
            endMs: seg.endMs,
            mouseStartMs: xToAudioMs(mx),
          };
          draw();
          return;
        }
      }
      selectedCleanupSegment = null;
    }

    // ── Beat Marker mode: left-click on waveform places a marker ──
    if (beatMarkerMode && showWaveform && my < waveTop()) {
      const t = beatToTime(xToBeat(mx));
      if (t >= 0) {
        // Auto-guess bar number from regression line (most accurate) or fallback to closest anchor
        let guessedBar;
        if (beatMarkers.length === 0) {
          guessedBar = 1;
        } else if (bpmCalcResult) {
          // Use full regression line: bar = 1 + (t - gapSec) / secPerBar
          const secPerBar = 480 / bpmCalcResult.bpm;
          const gapSec = bpmCalcResult.gapMs / 1000;
          const rawBar = (t - gapSec) / secPerBar + 1;
          guessedBar = Math.round(rawBar);
          console.log(`[BpmCal] Bar guess via regression: rawBar=${rawBar.toFixed(3)} → ${guessedBar}`);
        } else {
          // Only 1 marker so far — anchor off it using current BPM
          const secPerBar = 480 / bpm;
          const anchor = beatMarkers[0];
          const rawBar = (t - anchor.t) / secPerBar + anchor.bar;
          guessedBar = Math.round(rawBar);
          console.log(`[BpmCal] Bar guess via anchor bar${anchor.bar}@${anchor.t.toFixed(3)}s: rawBar=${rawBar.toFixed(3)} → ${guessedBar}`);
        }
        beatMarkers = [...beatMarkers, { t, bar: guessedBar }].sort((a, b) => a.t - b.t);
        bpmCalcResult = calcBpmFromMarkers(beatMarkers);
        console.log(`[BpmCal] Placed marker: bar=${guessedBar} t=${t.toFixed(3)}s (total: ${beatMarkers.length})`);
        draw();
      }
      return;
    }

    // ── Downbeat handle strip: start drag ──
    if (metronomeEnabled && metronomeManualDownbeatAnchorBeat !== null && my < DOWNBEAT_HANDLE_H) {
      downbeatHandleDragging = true;
      downbeatHandleDragStartX = mx;
      downbeatHandleDragStartAnchorBeat = metronomeManualDownbeatAnchorBeat;
      return;
    }

    // ── Metronome downbeat pick mode ──
    if (metronomePickTarget === 1 || metronomePickTarget === 2) {
      const target = metronomePickTarget;
      const pickedBeat = nearestGridBeat(mx);
      console.log(`[MetronomeTool] Pick target=${target} beat=${pickedBeat} (mx=${mx.toFixed(1)})`);
      if (target === 1) {
        metronomeDownbeat1Beat = pickedBeat;
        metronomeDownbeat2Beat = null;
        recalcMetronomeFromControls('pick-db1');
        showToast('Downbeat set and grid recalculated');
      } else {
        showToast('Use Set Downbeat only in simplified mode');
      }
      clearMetronomePickTarget();
      draw();
      return;
    }

    // ── Set GAP mode click ──
    if (setGapMode && setGapHoverBeat !== null) {
      // Compute the new GAP: the absolute time of the hovered grid line becomes the new GAP
      const newGapSec = beatToTime(setGapHoverBeat);
      const newGapMs = Math.round(newGapSec * 1000);
      console.log(`[SetGAP] Setting GAP to ${newGapMs}ms (beat ${setGapHoverBeat} → time ${newGapSec.toFixed(3)}s)`);
      pushUndo();
      const previousGapMs = gapMs;
      gapMs = newGapMs;
      cancelSetGapMode();
      handleBpmGapChange(false, { gapMs: previousGapMs, bpm });
      markUnsaved();
      return;
    }

    // Close context menu on left-click
    if (contextMenu.visible) closeContextMenu();

    // Click on time axis (bottom strip)
    const pianoH = viewHeight - timeAxisHeight;

    // Check playhead handle hit (when paused, 10px zone near playhead line)
    if (!isPlaying && currentTimeSec > 0) {
      const cx = beatToX(playbackBeat);
      if (Math.abs(mx - cx) <= 10) {
        playheadDrag = true;
        console.log('[Playhead] Start drag');
        // Start grain scrub
        if (scrubAudio && scrubAudioBuffer) {
          startScrubGrain(currentTimeSec);
        }
        return;
      }
    }

    // Check loop handle hit zones first (8px hit zone near boundary lines, full height)
    if (loopEnabled && loopStartBeat !== null && loopEndBeat !== null && segRecPhase === 'idle') {
      const lsX = beatToX(loopStartBeat);
      const leX = beatToX(loopEndBeat);
      if (Math.abs(mx - lsX) <= 8) {
        loopHandleDrag = 'start';
        console.log('[Loop] Dragging start handle');
        draw();
        return;
      }
      if (Math.abs(mx - leX) <= 8) {
        loopHandleDrag = 'end';
        console.log('[Loop] Dragging end handle');
        draw();
        return;
      }
    }

    if (my >= pianoH) {
      if (event.metaKey || event.ctrlKey) {
        // Ctrl/Cmd+click on ruler → start loop drag
        isSettingLoop = true;
        loopDragStartBeat = Math.round(beat);
        loopStartBeat = loopDragStartBeat;
        loopEndBeat = loopDragStartBeat;
        loopEnabled = true;
        console.log(`[Loop] Start drag at beat ${loopDragStartBeat} | ms ${(beatToTime(loopDragStartBeat) * 1000).toFixed(1)} | sec ${beatToTime(loopDragStartBeat).toFixed(3)}`);
        draw();
        return;
      }
      clearMarkerSelection();
      seekToTime(beatToTime(beat));
      return;
    }

    // Alt+click anywhere → seek playhead
    if (event.altKey) {
      clearMarkerSelection();
      seekToTime(beatToTime(beat));
      return;
    }

    // ── No note editing during playback ──
    if (isPlaying) {
      // Only allow seeking (handled above) — block note selection/dragging
      clearMarkerSelection();
      seekToTime(beatToTime(beat));
      return;
    }

    selectedCleanupSegment = null;

    // ── Paste mode: left click seeks normally (use Ctrl+V or right-click to paste) ──

    const isMultiKey = event.metaKey || event.ctrlKey;

    if (!isMultiKey) {
      const sharedBoundaryHit = hitTestTouchingSelectedPairBoundary(mx, my);
      if (sharedBoundaryHit) {
        selectedFlag = null;
        selectedNote = sharedBoundaryHit.left.id;
        isDragging = true;
        dragMode = 'resize-shared';
        dragStart = {
          x: mx,
          y: my,
          beat: sharedBoundaryHit.left.startBeat,
          scrollX,
          sharedLeftId: sharedBoundaryHit.left.id,
          sharedRightId: sharedBoundaryHit.right.id,
          sharedBeat: sharedBoundaryHit.sharedBeat,
          sharedLeftStart: sharedBoundaryHit.leftStart,
          sharedRightEnd: sharedBoundaryHit.rightEnd,
        };
        pushUndo();
        draw();
        return;
      }
    }

    // Check flag hit first so flags are selectable even if a note overlaps the same x-position.
    if (!isMultiKey) {
      for (const flag of flags) {
        const fx = beatToX(flag.beat);
        if (Math.abs(mx - fx) <= 8) {
          selectedFlag = flag.id;
          selectedNote = null;
          selectedNotes = new Set();
          isDragging = true;
          dragStart = { x: mx, y: my, beat: flag.beat, scrollX };
          draw();
          return;
        }
      }
    }

    // Find clicked note (check regular notes first, then breaks)
    let found = null;
    for (const note of notes) {
      if (note.type === 'break') continue;
      
      const nx = beatToX(note.startBeat);
      const ny = pitchToY(note.pitch);
      const nw = note.duration * zoom;

      if (mx >= nx && mx <= nx + nw && my >= ny - noteHeight / 2 && my <= ny + noteHeight / 2) {
        found = note;

        // Detect resize zones (edges) — only for single note drag, not multi-select toggle
        if (!isMultiKey) {
          if (mx - nx < 5) dragMode = 'resize-left';
          else if (nx + nw - mx < 5) dragMode = 'resize-right';
          else dragMode = 'move';
        }

        break;
      }
    }

    // If no regular note found, check break lines (10px hit zone)
    if (!found) {
      for (const note of notes) {
        if (note.type !== 'break') continue;
        const bx = beatToX(note.startBeat);
        if (Math.abs(mx - bx) <= 6) {
          found = note;
          dragMode = 'move-break';
          break;
        }
      }
    }

    if (found) {
      if (isMultiKey && found.type !== 'break') {
        // Ctrl/Cmd click: toggle note in multi-selection
        if (selectedNotes.has(found.id)) {
          selectedNotes.delete(found.id);
          if (selectedNote === found.id) {
            // keep selectedNote pointing at the remaining note if only one left
            selectedNote = selectedNotes.size === 1 ? [...selectedNotes][0] : null;
          }
        } else {
          selectedNotes.add(found.id);
          selectedNote = found.id;
        }
        selectedNotes = new Set(selectedNotes); // trigger reactivity
        dragMode = null;
        console.log(`[Mouse] Multi-select toggle id=${found.id}, count=${selectedNotes.size}`);
      } else {
        // Regular click: single select (clear multi-select unless clicking within it)
        if (selectedNotes.size > 0 && selectedNotes.has(found.id)) {
          // Clicking a note in the multi-selection
          selectedNote = found.id;
          if (found.type !== 'break') {
            // Only force move when multiple notes selected; single note keeps its detected dragMode (resize/move)
            if (selectedNotes.size > 1) dragMode = 'move';
          }
        } else {
          // Clear multi-select, single select
          selectedNotes = new Set([found.id]);
          selectedNote = found.id;
        }
        selectedFlag = null;
        isDragging = true;
        dragStart = { x: mx, y: my, beat: found.startBeat, pitch: found.pitch, duration: found.duration, endBeat: found.endBeat, scrollX };
        if (found.type !== 'break') {
          pushUndo();
          if (dragMode === 'move') {
            startDragOsc(found.pitch);
          }
          console.log(`[Mouse] Selected note id=${found.id} '${found.syllable}' mode=${dragMode}`);
        } else {
          pushUndo();
          console.log(`[Mouse] Selected break id=${found.id} at beat ${found.startBeat} mode=${dragMode}`);
        }
      }
    } else {
      // No note hit — check if a vocal trace frame near the click falls within a rendered note
      // (frames between notes are not visible and must not block seeking)
      if (vocalTraceVisible && vocalTraceFrames.length > 0 && !isMultiKey) {
        const clickBeat = xToBeat(mx);
        const clickPitch = yToPitch(my);
        let closest = null;
        let closestDist = Infinity;
        for (const frame of vocalTraceFrames) {
          const db = Math.abs(frame.beat - clickBeat);
          const dp = Math.abs(frame.pitch - clickPitch);
          if (db <= 1 && dp <= 1) {
            // Only match if frame falls within an actual note's beat range
            const inNote = notes.some(n => n.type !== 'break' && frame.beat >= n.startBeat && frame.beat <= n.endBeat);
            if (inNote) {
              const dist = db + dp * 0.5;
              if (dist < closestDist) { closestDist = dist; closest = frame; }
            }
          }
        }
        if (closest) {
          startDragOsc(closest.pitch);
          draw();
          return;
        }
      }
      // No trace hit either — start rubber-band selection or seek
      if (isMultiKey) {
        // Ctrl/Cmd + drag empty space → rubber-band box selection
        isBoxSelecting = true;
        boxSelectStart = { x: mx, y: my };
        boxSelectEnd = { x: mx, y: my };
        console.log('[Mouse] Start box selection');
      } else {
        clearMarkerSelection();
        seekToTime(beatToTime(beat));
        console.log(`[Mouse] No note — seek to beat ${beat.toFixed(1)}`);
      }
    }

    draw();
  }

  function handleMouseMove(event) {
    const rect = canvasEl.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;

    // Track live hover beat for keyboard paste (no click needed).
    const insideCanvas = mx >= 0 && mx <= rect.width && my >= 0 && my <= rect.height;
    hoverPasteBeat = insideCanvas ? Math.round(xToBeat(mx)) : null;

    if (metronomePickTarget === 1 || metronomePickTarget === 2) {
      metronomePickHoverBeat = insideCanvas ? nearestGridBeat(mx) : null;
      canvasEl.style.cursor = 'crosshair';
      draw();
      return;
    }

    if (cleanupDrag) {
      const seg = cleanupSegments.find(s => s.id === cleanupDrag.id);
      if (!seg) {
        cleanupDrag = null;
        return;
      }
      const mouseMs = xToAudioMs(mx);
      const msDelta = mouseMs - cleanupDrag.mouseStartMs;
      // Non-overlap clamping: find sorted neighbours once, based on original drag positions
      const CLAMP_GAP = 10; // ms minimum gap between segments
      const sortedOthers = cleanupSegments.filter(s => s.id !== seg.id).sort((a, b) => a.startMs - b.startMs);
      // Neighbour immediately before (ends before drag origin start) and after (starts after drag origin end)
      const prevN = [...sortedOthers].reverse().find(s => s.endMs <= cleanupDrag.startMs);
      const nextN = sortedOthers.find(s => s.startMs >= cleanupDrag.endMs);
      const minStart = prevN ? prevN.endMs + CLAMP_GAP : 0;
      const songEndMs = Math.max(0, (audioEl?.duration || audioDuration || 0) * 1000);
      const maxEndBound = songEndMs > 0 ? songEndMs : Infinity;
      const maxEnd = nextN ? Math.min(nextN.startMs - CLAMP_GAP, maxEndBound) : maxEndBound;

      if (cleanupDrag.mode === 'move') {
        const duration = cleanupDrag.endMs - cleanupDrag.startMs;
        let newStart = cleanupDrag.startMs + msDelta;
        newStart = Math.max(minStart, newStart);
        if (isFinite(maxEnd)) newStart = Math.min(maxEnd - duration, newStart);
        seg.startMs = newStart;
        seg.endMs = newStart + duration;
      } else if (cleanupDrag.mode === 'start') {
        let newStart = mouseMs;
        newStart = Math.max(minStart, newStart);          // can't cross prev neighbour
        newStart = Math.min(cleanupDrag.endMs - 50, newStart); // can't cross own end
        seg.startMs = newStart;
        seg.endMs = cleanupDrag.endMs;
      } else if (cleanupDrag.mode === 'end') {
        let newEnd = mouseMs;
        if (isFinite(maxEnd)) newEnd = Math.min(maxEnd, newEnd); // can't cross next neighbour
        newEnd = Math.max(cleanupDrag.startMs + 50, newEnd);     // can't cross own start
        seg.startMs = cleanupDrag.startMs;
        seg.endMs = newEnd;
      }
      cleanupSegments = [...cleanupSegments].map(normalizeCleanupSegment).sort((a, b) => a.startMs - b.startMs);
      autoScrollCleanupSegment(seg, cleanupDrag.mode, msDelta);
      cleanedAudioDirty = true;
      markUnsaved();
      draw();
      return;
    }

    // Flag drag
    if (isDragging && selectedFlag !== null) {
      const flag = flags.find(f => f.id === selectedFlag);
      if (flag) {
        flag.beat = Math.round(xToBeat(mx));
        flag.timeMs = Math.max(0, beatToTime(flag.beat) * 1000);
        flags = [...flags];
        draw();
      }
      return;
    }

    // Playhead drag (scrub)
    if (playheadDrag) {
      const beat = xToBeat(mx);
      const timeSec = beatToTime(beat);
      const maxTime = audioEl?.duration || audioDuration || 300;
      const clampedTime = Math.max(0, Math.min(maxTime, timeSec));
      currentTimeSec = clampedTime;
      playbackBeat = timeToBeat(clampedTime);
      if (audioEl) audioEl.currentTime = clampedTime;

      // Grain scrub: play tiny loop at current position
      if (scrubAudio && scrubAudioBuffer && !muteVocal) {
        startScrubGrain(clampedTime);
      }

      // Scrub: play MIDI pitch of note under playhead
      if (midiPlayback) {
        ensureMidiCtx();
        updateMidiPlayback(playbackBeat);
      }

      draw();
      return;
    }

    // Loop handle drag
    if (loopHandleDrag) {
      autoScrollAtCanvasEdge(mx);
      const beat = Math.round(xToBeat(clampDragXToCanvas(mx)));
      const minLoopBeats = 2;
      if (loopHandleDrag === 'start') {
        loopStartBeat = Math.min(beat, loopEndBeat - minLoopBeats);
      } else {
        loopEndBeat = Math.max(beat, loopStartBeat + minLoopBeats);
      }
      draw();
      return;
    }

    // Loop region drag on time axis
    if (isSettingLoop) {
      autoScrollAtCanvasEdge(mx);
      loopEndBeat = Math.round(xToBeat(clampDragXToCanvas(mx)));
      draw();
      return;
    }

    // ── Downbeat handle drag: live-slide all downbeat diamonds ──
    if (downbeatHandleDragging) {
      const dx = mx - downbeatHandleDragStartX;
      const deltaBeat = pixelsToMs(dx) * bpm / 15000;
      metronomeManualDownbeatAnchorBeat = downbeatHandleDragStartAnchorBeat + deltaBeat;
      // Keep metronomeDownbeat1Beat in sync for display
      metronomeDownbeat1Beat = metronomeManualDownbeatAnchorBeat;
      // Also update downbeatOffsetMs so confirmGridAlign-style logic stays in sync
      downbeatOffsetMs = gapMs + metronomeManualDownbeatAnchorBeat * 15000 / bpm;
      draw();
      return;
    }

    // ── Downbeat handle hover detection ──
    if (metronomeEnabled && metronomeManualDownbeatAnchorBeat !== null && my < DOWNBEAT_HANDLE_H) {
      downbeatHandleHovered = true;
      canvasEl.style.cursor = 'ew-resize';
      draw();
      return;
    } else if (downbeatHandleHovered) {
      downbeatHandleHovered = false;
      draw();
    }

    // ── Set GAP mode hover: highlight nearest grid line ──
    if (setGapMode) {
      const hoverBeat = nearestGridBeat(mx);
      const hoverTimeSec = beatToTime(hoverBeat);
      // GAP can be placed anywhere — including after the first note (negative beats)
      if (hoverTimeSec >= 0) {
        setGapHoverBeat = hoverBeat;
      } else {
        setGapHoverBeat = null; // can't set GAP before audio start
      }
      canvasEl.style.cursor = setGapHoverBeat !== null ? 'crosshair' : 'not-allowed';
      draw();
      return;
    }

    if (metronomePickTarget === 1 || metronomePickTarget === 2) {
      canvasEl.style.cursor = 'crosshair';
      return;
    }

    // Cursor style based on hover target
    if (!isDragging && !isSettingLoop && !loopHandleDrag && !playheadDrag) {
      let cursor = '';

      if (showWaveform && my < waveTop()) {
        const cleanupHit = hitTestCleanupSegment(mx, my);
        if (cleanupHit && !segRecPatched.has(cleanupHit.id)) {
          if (cleanupHit.mode === 'start' || cleanupHit.mode === 'end') {
            cursor = 'col-resize';
          } else if (cleanupHit.mode === 'move') {
            cursor = 'move';
          }
        }
      }

      // Check playhead handle (when paused)
      if (!cursor && !isPlaying && currentTimeSec > 0) {
        const cx = beatToX(playbackBeat);
        if (Math.abs(mx - cx) <= 10) {
          cursor = 'col-resize';
        }
      }

      // Check loop handles
      if (!cursor && loopEnabled && loopStartBeat !== null && loopEndBeat !== null) {
        const lsX = beatToX(loopStartBeat);
        const leX = beatToX(loopEndBeat);
        if (Math.abs(mx - lsX) <= 8 || Math.abs(mx - leX) <= 8) {
          cursor = 'col-resize';
        }
      }

      // Check note hover (if no loop handle matched)
      if (!cursor) {
        // Check flag hover
        for (const flag of flags) {
          if (Math.abs(beatToX(flag.beat) - mx) <= 8) {
            cursor = 'col-resize';
            break;
          }
        }
      }

      // Check note hover (if no flag matched)
      if (!cursor) {
        const sharedBoundaryHit = hitTestTouchingSelectedPairBoundary(mx, my);
        if (sharedBoundaryHit) {
          cursor = 'ew-resize';
        }
      }

      if (!cursor) {
        for (const note of notes) {
          if (note.type === 'break') {
            const bx = beatToX(note.startBeat);
            if (Math.abs(mx - bx) <= 6) {
              cursor = 'col-resize';
              break;
            }
            continue;
          }
          const nx = beatToX(note.startBeat);
          const ny = pitchToY(note.pitch);
          const nw = note.duration * zoom;
          if (mx >= nx && mx <= nx + nw && my >= ny - noteHeight / 2 && my <= ny + noteHeight / 2) {
            if (mx - nx < 5 || nx + nw - mx < 5) {
              cursor = 'col-resize';
            } else {
              cursor = 'move';
            }
            break;
          }
        }
      }

      canvasEl.style.cursor = pasteMode ? 'crosshair' : cursor;
    }

    // ── Box selection tracking ──
    if (isBoxSelecting) {
      boxSelectEnd = { x: mx, y: my };
      draw();
      return;
    }

    // ── Paste preview tracking ──
    if (pasteMode && clipboard) {
      pastePreviewBeat = Math.round(xToBeat(mx));
      draw();
      return;
    }

    if (!isDragging || selectedNote === null) return;

    autoScrollAtCanvasEdge(mx);

    const note = notes.find(n => n.id === selectedNote);
    if (!note) return;

    const dx = mx - dragStart.x;
    const scrollBeatDelta = (scrollX - (dragStart.scrollX ?? scrollX)) / zoom;
    const dy = my - dragStart.y;

    // Break drag: horizontal only
    if (note.type === 'break' && dragMode === 'move-break') {
      const { minBeat } = getSongBeatBounds();
      note.startBeat = Math.max(Math.ceil(minBeat), Math.round(dragStart.beat + (dx / zoom) + scrollBeatDelta));
      if (note.endBeat !== null && note.endBeat !== undefined) {
        const origDiff = (dragStart.endBeat || note.endBeat) - dragStart.beat;
        note.endBeat = note.startBeat + origDiff;
      }
      editorState.update(s => ({ ...s, hasChanges: true }));
      hasUnsavedChanges = true;
      notes = [...notes];
      draw();
      return;
    }

    if (note.type === 'break') return;

    if (dragMode === 'resize-shared') {
      const left = notes.find(n => n.id === dragStart.sharedLeftId && n.type !== 'break');
      const right = notes.find(n => n.id === dragStart.sharedRightId && n.type !== 'break');
      if (!left || !right) return;

      const minDur = 1;
      const proposedBoundary = Math.round((dragStart.sharedBeat ?? left.startBeat + left.duration) + (dx / zoom) + scrollBeatDelta);
      const minBoundary = (dragStart.sharedLeftStart ?? left.startBeat) + minDur;
      const maxBoundary = (dragStart.sharedRightEnd ?? (right.startBeat + right.duration)) - minDur;
      const newBoundary = clampValue(proposedBoundary, minBoundary, maxBoundary);

      left.startBeat = dragStart.sharedLeftStart ?? left.startBeat;
      left.duration = Math.max(minDur, newBoundary - left.startBeat);
      right.startBeat = newBoundary;
      right.duration = Math.max(minDur, (dragStart.sharedRightEnd ?? (right.startBeat + right.duration)) - newBoundary);

      editorState.update(s => ({ ...s, hasChanges: true }));
      hasUnsavedChanges = true;
      notes = [...notes];
      updatePitchRange();
      draw();
      return;
    }

    // ── Multi-note drag ──
    if (dragMode === 'move' && selectedNotes.size > 1 && selectedNotes.has(note.id)) {
      const rawBeatDelta = Math.round((dx / zoom) + scrollBeatDelta);
      const pitchDelta = event.shiftKey ? 0 : (yToPitch(dragStart.y + dy) - dragStart.pitch);
      
      if (!dragStart.groupOffsets) {
        // Capture initial positions of all selected notes
        dragStart.groupOffsets = [];
        for (const n of notes) {
          if (selectedNotes.has(n.id) && n.type !== 'break') {
            dragStart.groupOffsets.push({ id: n.id, beat: n.startBeat, pitch: n.pitch });
          }
        }
      }

      const groupSelection = dragStart.groupOffsets.map(offset => ({
        startBeat: offset.beat,
        duration: notes.find(nn => nn.id === offset.id)?.duration ?? 1,
      }));
      const beatDelta = clampSelectedMoveDeltaToSongBounds(rawBeatDelta, groupSelection);
      
      for (const offset of dragStart.groupOffsets) {
        const n = notes.find(nn => nn.id === offset.id);
        if (n) {
          n.startBeat = offset.beat + beatDelta;
          n.pitch = Math.max(minPitch, Math.min(maxPitch, offset.pitch + pitchDelta));
        }
      }
      
      if (note.pitch !== dragLastPitch) {
        if (dragStart.groupOffsets.length <= 1) updateDragOsc(note.pitch);
      }
    } else if (dragMode === 'move') {
      const desiredStartBeat = Math.round(dragStart.beat + (dx / zoom) + scrollBeatDelta);
      const { minBeat, maxBeat } = getSongBeatBounds();
      const maxStart = Number.isFinite(maxBeat) ? Math.floor(maxBeat - note.duration) : desiredStartBeat;
      note.startBeat = clampValue(desiredStartBeat, Math.ceil(minBeat), maxStart);
      const nextPitch = event.shiftKey ? dragStart.pitch : yToPitch(dragStart.y + dy);
      note.pitch = Math.max(minPitch, Math.min(maxPitch, nextPitch));
      // Update pitch preview if pitch changed — only for single note
      if (note.pitch !== dragLastPitch) {
        if (selectedNotes.size <= 1) updateDragOsc(note.pitch);
      }
    } else if (dragMode === 'resize-right') {
      const { maxBeat } = getSongBeatBounds();
      const maxDuration = Number.isFinite(maxBeat)
        ? Math.max(1, Math.floor(maxBeat - note.startBeat))
        : Math.max(1, Math.round(dragStart.duration + (dx / zoom) + scrollBeatDelta));
      note.duration = clampValue(Math.round(dragStart.duration + (dx / zoom) + scrollBeatDelta), 1, maxDuration);
    } else if (dragMode === 'resize-left') {
      const { minBeat } = getSongBeatBounds();
      const originalEnd = dragStart.beat + dragStart.duration;
      const newStart = clampValue(
        Math.round(dragStart.beat + (dx / zoom) + scrollBeatDelta),
        Math.ceil(minBeat),
        originalEnd - 1,
      );
      note.startBeat = newStart;
      note.duration = Math.max(1, originalEnd - newStart);
    }

    editorState.update(s => ({ ...s, hasChanges: true }));
    hasUnsavedChanges = true;
    notes = [...notes]; // trigger reactivity
    updatePitchRange();
    draw();
  }

  /** After a manual note drag/resize, write current beat positions back to rawTimings (as seconds)
   *  so that a subsequent BPM change will requantize from the user's adjusted positions. */
  function syncRawTimingsFromNotes() {
    if (!rawTimings || rawTimings.length === 0) return;
    const gapSec = gapMs / 1000;
    const nonBreakNotes = notes.filter(n => n.type !== 'break');
    for (let i = 0; i < rawTimings.length && i < nonBreakNotes.length; i++) {
      const note = nonBreakNotes[i];
      rawTimings[i].start = gapSec + (note.startBeat * 15) / bpm;
      rawTimings[i].end   = gapSec + ((note.startBeat + note.duration) * 15) / bpm;
      // Also store beat positions + BPM so requantize can scale directly (avoids double-rounding)
      rawTimings[i].beatAtBpm       = note.startBeat;
      rawTimings[i].durationAtBpm   = note.duration;
      rawTimings[i].syncedBpm       = bpm;
    }
  }

  function handleMouseUp() {
    if (cleanupDrag) {
      const drag = { ...cleanupDrag };
      cleanupDrag = null;
      const seg = cleanupSegments.find(s => s.id === drag.id);
      if (seg && segRecPatched.has(drag.id)) {
        const movedSegment = drag.mode === 'move' && (
          Math.abs(seg.startMs - drag.startMs) > 10 ||
          Math.abs(seg.endMs - drag.endMs) > 10
        );
        const expandedStartOutward = drag.mode === 'start' && seg.startMs < drag.startMs - 10;
        const expandedEndOutward = drag.mode === 'end' && seg.endMs > drag.endMs + 10;

        if (movedSegment || expandedStartOutward || expandedEndOutward) {
          console.warn('[SegResize] Reverting unsupported recorded segment transform', {
            id: drag.id,
            mode: drag.mode,
            from: { startMs: drag.startMs, endMs: drag.endMs },
            to: { startMs: seg.startMs, endMs: seg.endMs },
          });
          seg.startMs = drag.startMs;
          seg.endMs = drag.endMs;
          cleanupSegments = [...cleanupSegments].map(normalizeCleanupSegment).sort((a, b) => a.startMs - b.startMs);
          draw();
          showToast('Recorded segments can only be shrunk inward. Use re-record or make empty to change position.');
          return;
        }

        draw();
        // If the dragged segment was spliced and a handle moved inward, restore
        // the freed audio region from the original demucs vocal before saving.
        let freedStart = null, freedEnd = null;
        if (drag.mode === 'start' && seg.startMs > drag.startMs + 10) {
          freedStart = drag.startMs;
          freedEnd = seg.startMs;
        } else if (drag.mode === 'end' && seg.endMs < drag.endMs - 10) {
          freedStart = seg.endMs;
          freedEnd = drag.endMs;
        }
        if (freedStart !== null) {
          console.log(`[SegResize] Spliced segment shrunk — restoring freed region ${freedStart.toFixed(0)}–${freedEnd.toFixed(0)}ms`);
          fetch(`${API_BASE}/restore-segment/${$sessionId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ start_ms: freedStart, end_ms: freedEnd }),
          }).then(r => r.json()).then(result => {
            console.log('[SegResize] Restore OK:', result);
            const cacheBust = `?v=${Date.now()}`;
            vocalUrl = (hasVocalsAudio ? getAudioUrl($sessionId, 'vocals') : '') + cacheBust;
            if (audioSource === 'edited') {
              currentAudioUrl = vocalUrl;
              if (audioEl) { audioEl.src = currentAudioUrl; audioEl.load(); }
              loadWaveform(currentAudioUrl);
            }
            handleSave();
          }).catch(err => {
            console.error('[SegResize] Restore failed:', err);
            handleSave();
          });
          return;
        }
      }
      draw();
      if (cleanedAudioDirty) handleSave();
      return;
    }

    // Finish flag drag
    if (isDragging && selectedFlag !== null) {
      isDragging = false;
      saveFlags();
      draw();
      return;
    }

    // Finish downbeat handle drag — commit: recalc metronome with new anchor
    if (downbeatHandleDragging) {
      downbeatHandleDragging = false;
      downbeatHandleHovered = false;
      if (canvasEl) canvasEl.style.cursor = '';
      // Commit: recalculate metronome intervals from the new anchor beat
      recalcMetronomeFromControls('handle-drag');
      markUnsaved();
      lastMetronomeBeat = -1;
      console.log(`[DownbeatHandle] Committed anchor=${metronomeManualDownbeatAnchorBeat?.toFixed(3)}`);
      draw();
      return;
    }

    // Finish playhead drag
    if (playheadDrag) {
      playheadDrag = false;
      stopScrubGrain();
      if (midiPlayback) stopAllMidiNotes();
      canvasEl.style.cursor = '';
      console.log(`[Playhead] Drag done at ${currentTimeSec.toFixed(2)}s`);
      draw();
      return;
    }

    // Finish loop handle drag
    if (loopHandleDrag) {
      const a = Math.min(loopStartBeat, loopEndBeat);
      const b = Math.max(loopStartBeat, loopEndBeat);
      console.log(`[Loop] Handle drag done: beat ${a} → ${b} | ms ${(beatToTime(a) * 1000).toFixed(1)} → ${(beatToTime(b) * 1000).toFixed(1)} | sec ${beatToTime(a).toFixed(3)} → ${beatToTime(b).toFixed(3)}`);
      loopHandleDrag = null;
      canvasEl.style.cursor = '';
      draw();
      return;
    }

    // Finish loop drag
    if (isSettingLoop) {
      isSettingLoop = false;
      // Normalize so start < end
      if (loopStartBeat !== null && loopEndBeat !== null) {
        const a = Math.min(loopStartBeat, loopEndBeat);
        const b = Math.max(loopStartBeat, loopEndBeat);
        if (b - a < 2) {
          // Too small → clear loop
          loopStartBeat = null;
          loopEndBeat = null;
          loopEnabled = false;
          console.log('[Loop] Cleared (too small)');
        } else {
          loopStartBeat = a;
          loopEndBeat = b;
          console.log(`[Loop] Set region: beat ${a} → ${b} | ms ${(beatToTime(a) * 1000).toFixed(1)} → ${(beatToTime(b) * 1000).toFixed(1)} | sec ${beatToTime(a).toFixed(3)} → ${beatToTime(b).toFixed(3)}`);
        }
      }
      loopDragStartBeat = null;
      draw();
      return;
    }

    // ── Finish box selection ──
    if (isBoxSelecting) {
      isBoxSelecting = false;
      const x1 = Math.min(boxSelectStart.x, boxSelectEnd.x);
      const x2 = Math.max(boxSelectStart.x, boxSelectEnd.x);
      const y1 = Math.min(boxSelectStart.y, boxSelectEnd.y);
      const y2 = Math.max(boxSelectStart.y, boxSelectEnd.y);

      // Find all notes within the box
      let count = 0;
      for (const note of notes) {
        if (note.type === 'break') continue;
        const nx = beatToX(note.startBeat);
        const ny = pitchToY(note.pitch);
        const nw = note.duration * zoom;
        // Note intersects box if its rectangle overlaps
        if (nx + nw >= x1 && nx <= x2 && ny + noteHeight / 2 >= y1 && ny - noteHeight / 2 <= y2) {
          selectedNotes.add(note.id);
          count++;
        }
      }
      selectedNotes = new Set(selectedNotes); // trigger reactivity
      if (count > 0) selectedNote = [...selectedNotes][0];
      console.log(`[BoxSelect] Selected ${count} notes (total ${selectedNotes.size})`);
      boxSelectStart = null;
      boxSelectEnd = null;
      draw();
      return;
    }

    if (isDragging) {
      console.log('[Mouse] mouseUp, drag ended');
      // Sync manual note positions back into rawTimings so requantize preserves them
      syncRawTimingsFromNotes();
    }
    stopDragOsc();
    isDragging = false;
    dragMode = null;
  }

  // ──── Drag Pitch Preview ─────────────────────
  function startDragOsc(pitch) {
    stopDragOsc(); // clean up any previous
    dragAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
    dragOsc = dragAudioCtx.createOscillator();
    dragGain = dragAudioCtx.createGain();
    dragOsc.connect(dragGain);
    dragGain.connect(dragAudioCtx.destination);
    dragOsc.type = 'triangle';
    const freq = 440 * Math.pow(2, (pitch - 69) / 12);
    dragOsc.frequency.value = freq;
    dragGain.gain.value = 0.25;
    dragOsc.start();
    dragLastPitch = pitch;
  }

  function updateDragOsc(pitch) {
    if (!dragOsc || !dragAudioCtx) return;
    const freq = 440 * Math.pow(2, (pitch - 69) / 12);
    dragOsc.frequency.setValueAtTime(freq, dragAudioCtx.currentTime);
    dragLastPitch = pitch;
  }

  function stopDragOsc() {
    if (dragOsc) {
      try {
        dragGain.gain.linearRampToValueAtTime(0, dragAudioCtx.currentTime + 0.05);
        dragOsc.stop(dragAudioCtx.currentTime + 0.06);
      } catch (e) { /* already stopped */ }
      dragOsc = null;
      dragGain = null;
    }
    if (dragAudioCtx) {
      const ctxToClose = dragAudioCtx;
      dragAudioCtx = null;
      setTimeout(() => { try { ctxToClose.close().catch(() => {}); } catch(e) {} }, 100);
    }
    dragLastPitch = null;
  }

  // ──── Clipboard: Cut / Copy / Paste ──────────
  function getSelectedNoteObjects() {
    if (selectedNotes.size > 0) {
      return notes.filter(n => selectedNotes.has(n.id) && n.type !== 'break');
    } else if (selectedNote !== null) {
      const n = notes.find(nn => nn.id === selectedNote && nn.type !== 'break');
      return n ? [n] : [];
    }
    return [];
  }

  function getTouchingSelectedPair() {
    const sel = getSelectedNoteObjects().slice().sort((a, b) => (a.startBeat - b.startBeat) || (a.id - b.id));
    if (sel.length !== 2) return null;
    const [left, right] = sel;
    const leftEnd = left.startBeat + left.duration;
    if (leftEnd !== right.startBeat) return null;
    return { left, right, sharedBeat: leftEnd, leftStart: left.startBeat, rightEnd: right.startBeat + right.duration };
  }

  function hitTestTouchingSelectedPairBoundary(mx, my) {
    const pair = getTouchingSelectedPair();
    if (!pair) return null;
    const boundaryX = beatToX(pair.sharedBeat);
    if (Math.abs(mx - boundaryX) > 6) return null;

    const leftY = pitchToY(pair.left.pitch);
    const rightY = pitchToY(pair.right.pitch);
    const leftHit = my >= leftY - noteHeight / 2 && my <= leftY + noteHeight / 2;
    const rightHit = my >= rightY - noteHeight / 2 && my <= rightY + noteHeight / 2;
    if (!leftHit && !rightHit) return null;

    return { ...pair, boundaryX };
  }

  function clipboardCut() {
    const sel = getSelectedNoteObjects();
    if (sel.length === 0) return;

    const minBeat = Math.min(...sel.map(n => n.startBeat));
    clipboard = {
      notes: sel.map(n => ({
        startBeat: n.startBeat - minBeat,
        duration: n.duration,
        pitch: n.pitch,
        syllable: n.syllable,
        type: n.type || ':',
        isRap: n.isRap || false,
        isGolden: n.isGolden || false,
      })),
      mode: 'cut',
      sourceBeat: minBeat,
    };
    cutNoteIds = new Set(sel.map(n => n.id));
    pasteMode = true;
    pastePreviewBeat = null;
    console.log(`[Clipboard] Cut ${sel.length} notes from beat ${minBeat}`);
    closeContextMenu();
    draw();
  }

  function clipboardCopy() {
    const sel = getSelectedNoteObjects();
    if (sel.length === 0) return;

    const minBeat = Math.min(...sel.map(n => n.startBeat));
    clipboard = {
      notes: sel.map(n => ({
        startBeat: n.startBeat - minBeat,
        duration: n.duration,
        pitch: n.pitch,
        syllable: n.syllable,
        type: n.type || ':',
        isRap: n.isRap || false,
        isGolden: n.isGolden || false,
      })),
      mode: 'copy',
      sourceBeat: minBeat,
    };
    cutNoteIds = new Set();
    pasteMode = true;
    pastePreviewBeat = null;
    console.log(`[Clipboard] Copied ${sel.length} notes from beat ${minBeat}`);
    closeContextMenu();
    draw();
  }

  function finalizePaste(targetBeat) {
    if (!clipboard || !pasteMode) return;
    pushUndo();

    // If cut mode, remove original notes
    if (clipboard.mode === 'cut' && cutNoteIds.size > 0) {
      notes = notes.filter(n => !cutNoteIds.has(n.id));
      cutNoteIds = new Set();
    }

    // Generate new note IDs and insert copied notes
    let maxId = Math.max(0, ...notes.map(n => typeof n.id === 'number' ? n.id : 0));
    const newNotes = clipboard.notes.map(cn => ({
      id: ++maxId,
      startBeat: targetBeat + cn.startBeat,
      duration: cn.duration,
      pitch: cn.pitch,
      syllable: cn.syllable,
      type: cn.type,
      isRap: cn.isRap,
      isGolden: cn.isGolden,
    }));

    notes = [...notes, ...newNotes].sort((a, b) => a.startBeat - b.startBeat);

    // Select the newly pasted notes
    selectedNotes = new Set(newNotes.map(n => n.id));
    selectedNote = newNotes[0]?.id || null;

    // If copy mode, stay in paste mode for repeated pastes
    if (clipboard.mode === 'copy') {
      pastePreviewBeat = null;
    } else {
      // Cut mode: exit paste mode after placing
      pasteMode = false;
      clipboard = null;
      pastePreviewBeat = null;
    }

    editorState.update(s => ({ ...s, hasChanges: true }));
    hasUnsavedChanges = true;
    console.log(`[Clipboard] Pasted ${newNotes.length} notes at beat ${targetBeat}`);
    draw();
  }

  function cancelPaste() {
    // Restore cut notes (make them visible again)
    cutNoteIds = new Set();
    pasteMode = false;
    clipboard = null;
    pastePreviewBeat = null;
    console.log('[Clipboard] Paste cancelled');
    draw();
  }

  function getKeyboardPasteBeat() {
    if (hoverPasteBeat !== null) {
      return hoverPasteBeat;
    }
    if (selectedFlag !== null) {
      const flag = flags.find(f => f.id === selectedFlag);
      if (flag) return Math.round(flag.beat);
    }
    return Math.round(playbackBeat || 0);
  }

  function openContextMenu(nextMenu) {
    contextMenu = { ...nextMenu, visible: true };
    tick().then(() => {
      if (!contextMenu.visible || !contextMenuEl) return;
      const margin = 10;
      const rect = contextMenuEl.getBoundingClientRect();
      const maxX = Math.max(margin, window.innerWidth - rect.width - margin);
      const maxY = Math.max(margin, window.innerHeight - rect.height - margin);
      const x = Math.max(margin, Math.min(contextMenu.x, maxX));
      const y = Math.max(margin, Math.min(contextMenu.y, maxY));
      if (x !== contextMenu.x || y !== contextMenu.y) {
        contextMenu = { ...contextMenu, x, y };
      }
    });
  }

  // ──── Context Menu ──────────────────────────
  function handleContextMenu(event) {
    event.preventDefault();
    // No context menu during playback, grid align, segment recording, or vibrato modal.
    if (isPlaying || segRecPhase !== 'idle' || vibratoModalOpen) return;

    // Paste mode: show minimal paste/cancel menu
    if (pasteMode && clipboard) {
      const rect = canvasEl.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const beat = Math.round(xToBeat(mx));
      const menuW = 220, menuH = 110;
      const posX = Math.min(event.clientX, window.innerWidth - menuW - 10);
      const posY = Math.min(event.clientY, window.innerHeight - menuH - 10);
      openContextMenu({
        x: posX,
        y: posY,
        noteId: null,
        isBreak: false,
        isEmpty: false,
        isFlag: false,
        isPasteMenu: true,
        isCleanup: false,
        isWaveformEmpty: false,
        flagId: null,
        cleanupId: null,
        beat,
        ms: null,
        pitch: 0,
        traceFrame: null,
      });
      return;
    }
    const rect = canvasEl.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;

    // Beat marker mode: right-click on waveform removes nearest marker
    if (beatMarkerMode && showWaveform && my < waveTop()) {
      if (beatMarkers.length > 0) {
        const t = beatToTime(xToBeat(mx));
        const nearest = beatMarkers.reduce((a, b) => Math.abs(a.t - t) < Math.abs(b.t - t) ? a : b);
        console.log(`[BpmCal] Removed marker: bar=${nearest.bar} t=${nearest.t.toFixed(3)}s`);
        beatMarkers = beatMarkers.filter(m => m !== nearest);
        bpmCalcResult = calcBpmFromMarkers(beatMarkers);
        draw();
      }
      return;
    }

    if (showWaveform && my < waveTop()) {
      const beat = xToBeat(mx);
      const clickMs = xToAudioMs(mx);
      const hit = hitTestCleanupSegment(mx, my);
      const menuW = 260;
      const menuH = hit ? 180 : 130;
      const posX = Math.min(event.clientX, window.innerWidth - menuW - 10);
      const posY = Math.min(event.clientY, window.innerHeight - menuH - 10);
      if (hit) {
        selectedCleanupSegment = hit.id;
        openContextMenu({
          x: posX,
          y: posY,
          noteId: null,
          isBreak: false,
          isEmpty: false,
          isFlag: false,
          isPasteMenu: false,
          isCleanup: true,
          isWaveformEmpty: false,
          flagId: null,
          cleanupId: hit.id,
          beat,
          ms: clickMs,
          pitch: 0,
          traceFrame: null,
        });
      } else {
        openContextMenu({
          x: posX,
          y: posY,
          noteId: null,
          isBreak: false,
          isEmpty: false,
          isFlag: false,
          isPasteMenu: false,
          isCleanup: false,
          isWaveformEmpty: true,
          flagId: null,
          cleanupId: null,
          beat,
          ms: clickMs,
          pitch: 0,
          traceFrame: null,
        });
      }
      draw();
      return;
    }

    // Find note under cursor (regular notes first, then breaks)
    let found = null;
    let isBreak = false;
    for (const note of notes) {
      if (note.type === 'break') continue;
      const nx = beatToX(note.startBeat);
      const ny = pitchToY(note.pitch);
      const nw = note.duration * zoom;
      if (mx >= nx && mx <= nx + nw && my >= ny - noteHeight / 2 && my <= ny + noteHeight / 2) {
        found = note;
        break;
      }
    }

    // Check breaks if no regular note hit
    if (!found) {
      for (const note of notes) {
        if (note.type !== 'break') continue;
        const bx = beatToX(note.startBeat);
        if (Math.abs(mx - bx) <= 6) {
          found = note;
          isBreak = true;
          break;
        }
      }
    }

    if (found) {
      selectedNote = found.id;
      // If right-clicking on a note in the multi-selection, keep it; otherwise select just this note
      if (!selectedNotes.has(found.id)) {
        selectedNotes = new Set();
      }
      syllableUndoPushed = false;
      if (!isBreak) editingSyllable = found.syllable;
      // Store the exact beat where user right-clicked (for split-at-cursor)
      const clickBeat = xToBeat(mx);
      // Position menu, clamping to viewport
      const menuW = 220, menuH = isBreak ? 160 : 280;
      const posX = Math.min(event.clientX, window.innerWidth - menuW - 10);
      const posY = Math.min(event.clientY, window.innerHeight - menuH - 10);
      openContextMenu({
        x: posX,
        y: posY,
        noteId: found.id,
        isBreak,
        isEmpty: false,
        isFlag: false,
        isPasteMenu: false,
        isCleanup: false,
        isWaveformEmpty: false,
        flagId: null,
        cleanupId: null,
        beat: clickBeat,
        ms: null,
        pitch: 0,
        traceFrame: null,
      });
      draw();
    } else {
      // Empty space — show canvas context menu
      const beat = Math.round(xToBeat(mx));
      const pitch = yToPitch(my);
      const menuW = 220, menuH = 180;
      const posX = Math.min(event.clientX, window.innerWidth - menuW - 10);
      const posY = Math.min(event.clientY, window.innerHeight - menuH - 10);
      selectedNote = null;
      // Find nearest vocal trace frame to the right-click position
      let traceFrame = null;
      if (vocalTraceVisible && vocalTraceFrames.length > 0) {
        let closestDist = Infinity;
        let closestIdx = -1;
        for (let fi = 0; fi < vocalTraceFrames.length; fi++) {
          const frame = vocalTraceFrames[fi];
          const db = Math.abs(frame.beat - beat);
          const dp = Math.abs(frame.pitch - yToPitch(my));
          if (db <= 2 && dp <= 1.5) {
            const dist = db + dp * 0.5;
            if (dist < closestDist) { closestDist = dist; traceFrame = frame; closestIdx = fi; }
          }
        }
        if (closestIdx !== -1) {
          // Expand to full segment (consecutive same-pitch frames)
          const segPitch = traceFrame.pitch;
          let segStart = closestIdx;
          let segEnd = closestIdx;
          while (segStart > 0 && vocalTraceFrames[segStart - 1].pitch === segPitch) segStart--;
          while (segEnd + 1 < vocalTraceFrames.length && vocalTraceFrames[segEnd + 1].pitch === segPitch) segEnd++;
          const beatGap = 0.3;
          const segStartBeat = snapBeatValue(vocalTraceFrames[segStart].beat);
          const segEndBeat = snapBeatValue(vocalTraceFrames[segEnd].beat + beatGap);
          traceFrame = { beat: segStartBeat, pitch: segPitch, duration: Math.max(1, segEndBeat - segStartBeat) };
        }
      }
      // Check flag right-click
      let flagHit = null;
      for (const flag of flags) {
        const fx = beatToX(xToBeat(mx));
        if (Math.abs(beatToX(flag.beat) - mx) <= 8) { flagHit = flag; break; }
      }
      if (flagHit) {
        openContextMenu({
          x: posX,
          y: posY,
          noteId: null,
          isBreak: false,
          isEmpty: false,
          isFlag: true,
          isPasteMenu: false,
          isCleanup: false,
          isWaveformEmpty: false,
          flagId: flagHit.id,
          cleanupId: null,
          beat: flagHit.beat,
          ms: null,
          pitch: 0,
          traceFrame: null,
        });
      } else {
        openContextMenu({
          x: posX,
          y: posY,
          noteId: null,
          isBreak: false,
          isEmpty: true,
          isFlag: false,
          isPasteMenu: false,
          isCleanup: false,
          isWaveformEmpty: false,
          flagId: null,
          cleanupId: null,
          beat,
          ms: null,
          pitch,
          traceFrame,
        });
      }
    }
  }

  function closeContextMenu() {
    contextMenu = {
      visible: false,
      x: 0,
      y: 0,
      noteId: null,
      isBreak: false,
      isEmpty: false,
      isFlag: false,
      isPasteMenu: false,
      isCleanup: false,
      isWaveformEmpty: false,
      flagId: null,
      cleanupId: null,
      beat: 0,
      ms: null,
      pitch: 0,
      traceFrame: null,
    };
  }

  function openSegmentRegenerateModal(range) {
    segRegenRange = {
      sourceType: range.sourceType,
      cleanupId: range.cleanupId ?? null,
      startMs: Math.max(0, Math.round(range.startMs || 0)),
      endMs: Math.max(0, Math.round(range.endMs || 0)),
    };
    if (segRegenRange.endMs <= segRegenRange.startMs) {
      showToast('Invalid range for segment regenerate');
      return;
    }
    segRegenPreviewLoading = false;
    segRegenPreviewError = '';
    segRegenPreviewLines = [];
    segRegenPreviewConfidence = null;
    segRegenPreviewHyphenated = false;
    segRegenLanguage = SUPPORTED_LANGUAGES.some(l => l.code === $lyricsData?.language)
      ? $lyricsData.language
      : 'auto';
    segRegenCurrentEditorSource = audioSource === 'edited' ? 'edited' : 'vocals';
    segRegenAudioSource = (segRegenCurrentEditorSource === 'edited' && (cleanedAudioAvailable || segRecPatched.size > 0))
      ? 'edited'
      : 'vocals';

    // Loop policy for Segment AI modal entry:
    // - loop source: preserve user's existing loop as-is
    // - cleanup source: force loop to selected cleanup segment range
    if (segRegenRange.sourceType === 'cleanup') {
      const segStartBeat = timeToBeat(segRegenRange.startMs / 1000);
      const segEndBeat = timeToBeat(segRegenRange.endMs / 1000);
      loopStartBeat = Math.min(segStartBeat, segEndBeat);
      loopEndBeat = Math.max(segStartBeat, segEndBeat);
      loopEnabled = true;
    }

    console.log('[SegmentAI] Modal range applied:', {
      sourceType: segRegenRange.sourceType,
      cleanupId: segRegenRange.cleanupId,
      startMs: segRegenRange.startMs,
      endMs: segRegenRange.endMs,
      durationMs: segRegenRange.endMs - segRegenRange.startMs,
      startSec: Number((segRegenRange.startMs / 1000).toFixed(3)),
      endSec: Number((segRegenRange.endMs / 1000).toFixed(3)),
      audioSourceInEditor: audioSource,
      analysisSourceSetting: segRegenAudioSource,
    });
    segRegenModalOpen = true;
    closeContextMenu();
  }

  function openSegmentRegenerateFromCleanup(seg) {
    if (!seg) return;
    openSegmentRegenerateModal({
      sourceType: 'cleanup',
      cleanupId: seg.id,
      startMs: seg.startMs,
      endMs: seg.endMs,
    });
  }

  function openSegmentRegenerateFromLoop() {
    if (loopStartBeat === null || loopEndBeat === null || !loopEnabled) {
      showToast('Create a loop range first');
      return;
    }
    const range = getActiveLoopRangeMs();
    if (!range) {
      showToast('Create a loop range first');
      return;
    }
    console.log('[SegmentAI] Loop source before modal:', {
      loopEnabled,
      loopStartBeat,
      loopEndBeat,
      loopStartMs: range.startMs,
      loopEndMs: range.endMs,
      loopDurationMs: range.endMs - range.startMs,
      loopStartSec: Number((range.startMs / 1000).toFixed(3)),
      loopEndSec: Number((range.endMs / 1000).toFixed(3)),
    });
    openSegmentRegenerateModal({
      sourceType: 'loop',
      cleanupId: null,
      startMs: range.startMs,
      endMs: range.endMs,
    });
  }

  function hasActiveLoopRange() {
    return loopEnabled && loopStartBeat !== null && loopEndBeat !== null;
  }

  function isBeatInsideActiveLoop(beat) {
    if (!hasActiveLoopRange()) return false;
    const a = Math.min(loopStartBeat, loopEndBeat);
    const b = Math.max(loopStartBeat, loopEndBeat);
    return beat >= a && beat <= b;
  }

  function getActiveLoopRangeMs() {
    if (!hasActiveLoopRange()) return null;
    const a = Math.min(loopStartBeat, loopEndBeat);
    const b = Math.max(loopStartBeat, loopEndBeat);
    return {
      startMs: Math.max(0, beatToTime(a) * 1000),
      endMs: Math.max(0, beatToTime(b) * 1000),
    };
  }

  function openSegmentRegenerateFromLoopContext() {
    openSegmentRegenerateFromLoop();
  }

  function closeSegmentRegenerateModal() {
    segRegenModalOpen = false;
  }

  function closeVibratoModal() {
    if (vibratoModalDragging) {
      vibratoModalMouseUp();
    }
    vibratoModalOpen = false;
    vibratoLoading = false;
  }

  function openVibratoModal(noteId) {
    const note = notes.find(n => n.id === noteId);
    if (!note || note.type === 'break') return;
    if (note.isRap) {
      showToast('Vibrato tool is not available for rap notes');
      return;
    }
    if (note.duration < 2) {
      showToast('Select a longer note for vibrato');
      return;
    }

    vibratoNoteId = noteId;
    vibratoError = '';
    vibratoSegments = [];
    vibratoCurrentEditorSource = audioSource === 'edited' ? 'edited' : 'vocals';
    vibratoAudioSource = (vibratoCurrentEditorSource === 'edited' && (cleanedAudioAvailable || segRecPatched.size > 0))
      ? 'edited'
      : 'vocals';
    vibratoModalOpen = true;
    closeContextMenu();
  }

  function getVibratoAudioSourceForApi() {
    return vibratoAudioSource === 'edited' ? 'edited' : 'vocals';
  }

  function logDisplayedPitchToolFramesForRange(note, startSec, endSec) {
    const startBeat = note.startBeat;
    const endBeat = note.startBeat + note.duration;
    const rangeFrames = pitchLineFrames
      .filter(f => Number.isFinite(f?.beat) && Number.isFinite(f?.pitch) && f.beat >= startBeat && f.beat <= endBeat)
      .sort((a, b) => a.beat - b.beat);

    console.group('[VibratoUI] Displayed pitch-tool frames');
    console.log('[VibratoUI] note', {
      id: note.id,
      startBeat,
      endBeat,
      startSec,
      endSec,
      durationSec: Math.max(0, endSec - startSec),
      pitch: note.pitch,
      pitchName: noteName(note.pitch),
      vibratoAudioSource,
      analysisSource: getVibratoAudioSourceForApi(),
      editorAudioSource: audioSource,
      pitchLineVisible,
      pitchLineFramesCount: pitchLineFrames.length,
      pitchLineSourceUrl,
    });

    if (rangeFrames.length === 0) {
      console.warn('[VibratoUI] No displayed pitch-line frames found in selected note range');
      console.groupEnd();
      return {
        frameCount: 0,
        spanSemitones: 0,
        uniquePitchCount: 0,
        runCount: 0,
        minPitch: null,
        maxPitch: null,
      };
    }

    const pitches = rangeFrames.map(f => f.pitch);
    const minPitch = Math.min(...pitches);
    const maxPitch = Math.max(...pitches);
    const uniquePitches = [...new Set(pitches)];
    console.log('[VibratoUI] frame_stats', {
      count: rangeFrames.length,
      minPitch,
      minPitchName: noteName(minPitch),
      maxPitch,
      maxPitchName: noteName(maxPitch),
      spanSemitones: maxPitch - minPitch,
      uniquePitchCount: uniquePitches.length,
      uniquePitchNames: uniquePitches.slice(0, 16).map(p => `${p}(${noteName(p)})`),
    });

    // Log compact pitch-change runs so subtle movements are easy to see.
    const runs = [];
    let runStart = rangeFrames[0].beat;
    let runPitch = rangeFrames[0].pitch;
    let runCount = 1;
    for (let i = 1; i < rangeFrames.length; i++) {
      const f = rangeFrames[i];
      if (f.pitch === runPitch) {
        runCount += 1;
        continue;
      }
      runs.push({ startBeat: runStart, endBeat: rangeFrames[i - 1].beat, pitch: runPitch, count: runCount });
      runStart = f.beat;
      runPitch = f.pitch;
      runCount = 1;
    }
    runs.push({
      startBeat: runStart,
      endBeat: rangeFrames[rangeFrames.length - 1].beat,
      pitch: runPitch,
      count: runCount,
    });

    console.log(
      '[VibratoUI] pitch_runs',
      runs.map(r => `${r.startBeat.toFixed(2)}-${r.endBeat.toFixed(2)}b:${r.pitch}(${noteName(r.pitch)}) x${r.count}`).join(' | ')
    );

    // Full run table (all dotted pitch changes grouped into contiguous runs).
    const runTable = runs.map((r, idx) => ({
      idx: idx + 1,
      startBeat: Number(r.startBeat.toFixed(3)),
      endBeat: Number(r.endBeat.toFixed(3)),
      pitch: r.pitch,
      note: noteName(r.pitch),
      frameCount: r.count,
    }));
    console.log('[VibratoUI] all_pitch_change_runs', runTable);
    console.table(runTable);

    // Full dotted-frame sequence (exactly what the user sees in pitch tool).
    const fullFrameTable = rangeFrames.map((f, idx) => ({
      idx: idx + 1,
      beat: Number(f.beat.toFixed(3)),
      timeSec: Number(beatToTime(f.beat).toFixed(3)),
      pitch: f.pitch,
      note: noteName(f.pitch),
    }));
    console.log('[VibratoUI] all_dotted_frames_count', fullFrameTable.length);
    console.log('[VibratoUI] all_dotted_frames', fullFrameTable);

    const preview = rangeFrames.slice(0, 24).map(f => ({ beat: Number(f.beat.toFixed(3)), pitch: f.pitch, note: noteName(f.pitch) }));
    console.table(preview);
    if (rangeFrames.length > preview.length) {
      const tail = rangeFrames.slice(-12).map(f => ({ beat: Number(f.beat.toFixed(3)), pitch: f.pitch, note: noteName(f.pitch) }));
      console.log('[VibratoUI] tail preview');
      console.table(tail);
    }
    console.groupEnd();

    return {
      frameCount: rangeFrames.length,
      spanSemitones: maxPitch - minPitch,
      uniquePitchCount: uniquePitches.length,
      runCount: runs.length,
      minPitch,
      maxPitch,
    };
  }

  function getVibratoSensitivityParams() {
    if (vibratoSensitivity === 'subtle') {
      return {
        min_duration_sec: 0.55,
        target_slice_sec: 0.10,
        max_segments: 12,
        min_pitch_span: 1,
        min_run_frames: 1,
      };
    }
    if (vibratoSensitivity === 'strict') {
      return {
        min_duration_sec: 1.0,
        target_slice_sec: 0.22,
        max_segments: 6,
        min_pitch_span: 3,
        min_run_frames: 3,
      };
    }
    return {
      min_duration_sec: 0.8,
      target_slice_sec: 0.16,
      max_segments: 8,
      min_pitch_span: 2,
      min_run_frames: 2,
    };
  }

  async function analyzeVibratoForSelectedNote() {
    if (!$sessionId || vibratoNoteId === null || vibratoLoading) return;
    const note = notes.find(n => n.id === vibratoNoteId && n.type !== 'break');
    if (!note) {
      vibratoError = 'Selected note no longer exists';
      return;
    }

    const startSec = beatToTime(note.startBeat);
    const endSec = beatToTime(note.startBeat + note.duration);
    const startMs = Math.max(0, startSec * 1000);
    const endMs = Math.max(startMs + 20, endSec * 1000);

    const uiStats = logDisplayedPitchToolFramesForRange(note, startSec, endSec);

    vibratoLoading = true;
    vibratoError = '';
    vibratoSegments = [];
    try {
      const data = await suggestVibrato($sessionId, {
        start_ms: startMs,
        end_ms: endMs,
        audio_source: getVibratoAudioSourceForApi(),
        ...getVibratoSensitivityParams(),
      });

      const segments = Array.isArray(data?.segments)
        ? data.segments
            .map(s => ({
              start_sec: Number(s.start_sec),
              end_sec: Number(s.end_sec),
              pitch: Number(s.pitch),
            }))
            .filter(s => Number.isFinite(s.start_sec) && Number.isFinite(s.end_sec) && Number.isFinite(s.pitch) && s.end_sec > s.start_sec)
        : [];

      const backendPitches = segments.map(s => s.pitch);
      const backendMin = backendPitches.length ? Math.min(...backendPitches) : null;
      const backendMax = backendPitches.length ? Math.max(...backendPitches) : null;
      const backendSpan = (backendMin != null && backendMax != null) ? (backendMax - backendMin) : 0;
      const backendUnique = backendPitches.length ? new Set(backendPitches).size : 0;

      console.group('[VibratoCompare] Frontend vs Backend');
      console.log('[VibratoCompare] request', {
        sessionId: $sessionId,
        noteId: note.id,
        source: getVibratoAudioSourceForApi(),
        sensitivity: vibratoSensitivity,
        params: getVibratoSensitivityParams(),
        startMs: Math.round(startMs),
        endMs: Math.round(endMs),
      });
      console.log('[VibratoCompare] frontend_ui_pitchtool', uiStats);
      console.log('[VibratoCompare] backend_segments', {
        count: segments.length,
        spanSemitones: backendSpan,
        uniquePitchCount: backendUnique,
        minPitch: backendMin,
        minPitchName: backendMin != null ? noteName(backendMin) : null,
        maxPitch: backendMax,
        maxPitchName: backendMax != null ? noteName(backendMax) : null,
      });
      if (segments.length > 0) {
        console.log(
          '[VibratoCompare] backend_segment_list',
          segments
            .map(s => `${s.start_sec.toFixed(3)}-${s.end_sec.toFixed(3)}:${s.pitch}(${noteName(s.pitch)})`)
            .join(' | ')
        );
      }
      if (uiStats?.frameCount > 0) {
        console.log('[VibratoCompare] delta', {
          segmentCountMinusUiRuns: segments.length - (uiStats.runCount || 0),
          backendSpanMinusUiSpan: backendSpan - (uiStats.spanSemitones || 0),
          backendUniqueMinusUiUnique: backendUnique - (uiStats.uniquePitchCount || 0),
        });
      }
      console.groupEnd();

      if (segments.length < 2) {
        vibratoError = 'No clear vibrato detected in this range';
        return;
      }

      vibratoSegments = segments;
      showToast(`Detected ${segments.length} vibrato slices`);
    } catch (err) {
      vibratoError = String(err?.message || 'Vibrato analysis failed');
    } finally {
      vibratoLoading = false;
    }
  }

  function applyVibratoToSelectedNote() {
    if (vibratoNoteId === null || vibratoSegments.length < 2) return;
    const idx = notes.findIndex(n => n.id === vibratoNoteId && n.type !== 'break');
    if (idx === -1) return;

    const note = notes[idx];
    const noteStart = note.startBeat;
    const noteEnd = note.startBeat + note.duration;
    if (note.duration < 2) {
      showToast('Note too short to split');
      return;
    }

    const totalSec = Math.max(0.001, beatToTime(noteEnd) - beatToTime(noteStart));
    const startSec = beatToTime(noteStart);

    const maxSegmentCount = Math.min(vibratoSegments.length, Math.max(2, note.duration));
    const useSegments = vibratoSegments.slice(0, maxSegmentCount);

    const boundaries = [noteStart];
    let prev = noteStart;
    for (let i = 0; i < useSegments.length - 1; i++) {
      const proposedRel = (useSegments[i].end_sec - startSec) / totalSec;
      const proposedBeat = noteStart + Math.round(proposedRel * note.duration);
      const remaining = (useSegments.length - 1) - i;
      const minBeat = prev + 1;
      const maxBeat = noteEnd - remaining;
      const bounded = Math.max(minBeat, Math.min(maxBeat, proposedBeat));
      boundaries.push(bounded);
      prev = bounded;
    }
    boundaries.push(noteEnd);

    const maxId = Math.max(0, ...notes.map(n => n.id || 0));
    const replacements = [];
    for (let i = 0; i < useSegments.length; i++) {
      const segStart = boundaries[i];
      const segEnd = boundaries[i + 1];
      const dur = Math.max(1, segEnd - segStart);
      replacements.push({
        id: maxId + 1 + i,
        startBeat: segStart,
        duration: dur,
        pitch: Math.round(useSegments[i].pitch),
        syllable: i === 0 ? note.syllable : ' ~',
        isRap: false,
        isGolden: !!note.isGolden,
        confidence: note.confidence ?? 1.0,
        original: { startBeat: segStart, duration: dur, pitch: Math.round(useSegments[i].pitch) },
      });
    }

    pushUndo();
    notes = [...notes.slice(0, idx), ...replacements, ...notes.slice(idx + 1)];
    selectedNote = replacements[0]?.id ?? null;
    selectedNotes = selectedNote !== null ? new Set([selectedNote]) : new Set();
    markUnsaved();
    updatePitchRange();
    computeTotalBeats();
    closeVibratoModal();
    draw();
    showToast(`Applied vibrato split (${replacements.length} notes)`);
  }

  async function hyphenateSegmentPreviewLines(lines, silent = false) {
    const raw = (lines || []).map(v => String(v || '').trim()).filter(Boolean);
    if (raw.length === 0) return [];

    console.log('[SegmentAI] Hyphenate start', {
      silent,
      lineCount: raw.length,
      language: segRegenLanguage,
    });

    segRegenHyphenateLoading = true;
    try {
      const fd = new FormData();
      fd.append('lyrics', raw.join('\n'));
      fd.append('language', segRegenLanguage === 'auto' ? 'en' : segRegenLanguage);

      const resp = await fetch(`${API_BASE}/hyphenate`, {
        method: 'POST',
        body: fd,
      });
      console.log('[SegmentAI] Hyphenate response', { status: resp.status, ok: resp.ok });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data?.detail || data?.message || 'Hyphenation failed');
      }

      let hyphenated = [];
      if (Array.isArray(data?.lines) && data.lines.length > 0) {
        hyphenated = data.lines.map(l => String(l?.hyphenated || '').trim()).filter(Boolean);
      } else if (typeof data?.hyphenated === 'string' && data.hyphenated.trim()) {
        hyphenated = data.hyphenated.split(/\n+/).map(v => v.trim()).filter(Boolean);
      }

      if (hyphenated.length === 0) hyphenated = raw;
      segRegenPreviewHyphenated = true;
      console.log('[SegmentAI] Hyphenate success', { lineCount: hyphenated.length });
      if (!silent) showToast('Hyphenation applied');
      return hyphenated;
    } catch (e) {
      console.error('[SegmentAI] Hyphenate failed', e);
      if (!silent) showToast(String(e?.message || 'Hyphenation failed'));
      throw e;
    } finally {
      segRegenHyphenateLoading = false;
    }
  }

  async function generateNotesFromSegmentPreview() {
    if (segRegenGenerateLoading) {
      console.warn('[SegmentAI] Generate aborted: already loading');
      return;
    }
    if (segRegenPreviewLines.length === 0) {
      console.warn('[SegmentAI] Generate aborted: no preview lines');
      showToast('Run Preview Lyrics first');
      return;
    }
    if (!$sessionId) {
      console.warn('[SegmentAI] Generate aborted: missing session id');
      return;
    }

    console.log('[SegmentAI] Generate start', {
      sessionId: $sessionId,
      range: { ...segRegenRange },
      language: segRegenLanguage,
      audioSource: getSegRegenAudioSourceForApi(),
      previewLineCount: segRegenPreviewLines.length,
      previewHyphenated: segRegenPreviewHyphenated,
    });

    segRegenGenerateLoading = true;
    try {
      // Always hyphenate before generation.
      if (!segRegenPreviewHyphenated) {
        segRegenPreviewLines = await hyphenateSegmentPreviewLines(segRegenPreviewLines, true);
      }

      const lyricsText = segRegenPreviewLines.join('\n');

      const resp = await fetch(`${API_BASE}/segment-generate/${$sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start_ms: segRegenRange.startMs,
          end_ms: segRegenRange.endMs,
          lyrics: lyricsText,
          language: segRegenLanguage,
          audio_source: getSegRegenAudioSourceForApi(),
        }),
      });

      console.log('[SegmentAI] Generate response', { status: resp.status, ok: resp.ok });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data?.detail || data?.message || 'Segment generation failed');
      }

      let newNotes = Array.isArray(data?.notes) ? data.notes.map(n => ({ ...n })) : [];
      const timingRows = Array.isArray(data?.syllable_timings) ? data.syllable_timings : [];
      console.log('[SegmentAI] Generate payload parsed', {
        returnedNotes: newNotes.length,
        returnedBreaks: newNotes.filter(n => n?.type === 'break').length,
        returnedTimings: timingRows.length,
      });
      if (newNotes.length === 0) {
        showToast('No notes returned from segment generation');
        return;
      }

      // Convert ms range to beats so we can remove old notes
      const rangeStartBeat = timeToBeat(segRegenRange.startMs / 1000);
      const rangeEndBeat   = timeToBeat(segRegenRange.endMs   / 1000);
      console.log('[SegmentAI] Generate beat range', {
        rangeStartBeat,
        rangeEndBeat,
      });

      // Normalize generated note placement to the editor's current bpm/gap grid
      // using absolute syllable timings returned by backend.
      const generatedNoteIdx = [];
      for (let i = 0; i < newNotes.length; i++) {
        if (newNotes[i]?.type !== 'break') generatedNoteIdx.push(i);
      }
      if (timingRows.length >= generatedNoteIdx.length && generatedNoteIdx.length > 0) {
        let remappedCount = 0;
        generatedNoteIdx.forEach((noteArrayIndex, timingIndex) => {
          const row = timingRows[timingIndex] || {};
          const s = Number(row.start);
          const e = Number(row.end);
          if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) return;
          const startBeat = Math.round(timeToBeat(s));
          const endBeat = Math.max(startBeat + 1, Math.round(timeToBeat(e)));
          const duration = Math.max(1, endBeat - startBeat);
          const note = newNotes[noteArrayIndex];
          newNotes[noteArrayIndex] = {
            ...note,
            startBeat,
            duration,
            original: {
              ...(note.original || {}),
              startBeat,
              duration,
              pitch: note.pitch,
            },
          };
          remappedCount += 1;
        });
        console.log('[SegmentAI] Generate remap from timings', {
          remappedCount,
          totalGeneratedNotes: generatedNoteIdx.length,
        });
      }

      // If generated beats still sit completely outside requested range,
      // shift them into the segment window as a safety fallback.
      const generatedStarts = newNotes.map(n => Number(n.startBeat)).filter(Number.isFinite);
      const generatedEnds = newNotes
        .map(n => (n?.type === 'break' ? Number(n.endBeat ?? (n.startBeat + 1)) : Number(n.startBeat) + Number(n.duration ?? 1)))
        .filter(Number.isFinite);
      const generatedStartBeat = generatedStarts.length ? Math.min(...generatedStarts) : null;
      const generatedEndBeat = generatedEnds.length ? Math.max(...generatedEnds) : null;
      if (generatedStartBeat !== null && generatedEndBeat !== null) {
        const overlapsRange = generatedEndBeat > rangeStartBeat && generatedStartBeat < rangeEndBeat;
        if (!overlapsRange) {
          const shift = Math.round(rangeStartBeat - generatedStartBeat);
          console.warn('[SegmentAI] Generated notes outside target range, applying beat shift', {
            generatedStartBeat,
            generatedEndBeat,
            rangeStartBeat,
            rangeEndBeat,
            shift,
          });
          newNotes = newNotes.map(n => {
            const shiftedStart = Number(n.startBeat) + shift;
            const shiftedEnd = n?.type === 'break'
              ? Number(n.endBeat ?? (n.startBeat + 1)) + shift
              : undefined;
            return {
              ...n,
              startBeat: shiftedStart,
              ...(n?.type === 'break' ? { endBeat: shiftedEnd } : {}),
              original: n.original
                ? {
                    ...n.original,
                    startBeat: Number(n.original.startBeat ?? n.startBeat) + shift,
                  }
                : n.original,
            };
          });
        }
      }

      // Snapshot for undo before any mutation
      pushUndo();

      // Remove all existing notes (including breaks) that overlap the beat range
      const kept = notes.filter(n => {
        const nEnd = n.type === 'break' ? (n.endBeat ?? n.startBeat + 1) : (n.startBeat + (n.duration ?? 1));
        return nEnd <= rangeStartBeat || n.startBeat >= rangeEndBeat;
      });

      // Re-assign IDs on incoming notes so they don't collide with kept notes
      const maxId = kept.reduce((m, n) => Math.max(m, n.id ?? 0), -1);
      const insertedNotes = newNotes.map((n, i) => ({ ...n, id: maxId + 1 + i }));
      const merged = [
        ...kept,
        ...insertedNotes,
      ].sort((a, b) => a.startBeat - b.startBeat);

      console.log('[SegmentAI] Generate merge stats', {
        oldCount: notes.length,
        keptCount: kept.length,
        insertedCount: newNotes.length,
        mergedCount: merged.length,
      });

      notes = merged;
      if (insertedNotes.length > 0) {
        selectedNote = insertedNotes[0].id;
        selectedNotes = new Set([insertedNotes[0].id]);
        ensureBeatVisible(insertedNotes[0].startBeat);
      } else {
        selectedNote = null;
        selectedNotes = new Set();
      }
      markUnsaved();
      updatePitchRange();
      computeTotalBeats();
      draw();

      closeSegmentRegenerateModal();
      showToast(`Generated ${newNotes.filter(n => n.type !== 'break').length} notes for segment`);
    } catch (e) {
      console.error('[SegmentAI] Generate failed', e);
      showToast(String(e?.message || 'Segment generation failed'));
    } finally {
      segRegenGenerateLoading = false;
    }
  }

  function getSegRegenAudioSourceForApi() {
    return segRegenAudioSource === 'edited' ? 'edited' : 'vocals';
  }

  async function previewSegmentLyrics() {
    if (!$sessionId) {
      console.warn('[SegmentAI] Preview aborted: missing session id');
      return;
    }
    console.log('[SegmentAI] Preview start', {
      sessionId: $sessionId,
      range: { ...segRegenRange },
      language: segRegenLanguage,
      modelPreset: segRegenPreset,
      audioSource: getSegRegenAudioSourceForApi(),
      sourceType: segRegenRange.sourceType,
    });
    segRegenPreviewLoading = true;
    segRegenPreviewError = '';
    segRegenPreviewLines = [];
    segRegenPreviewConfidence = null;
    segRegenPreviewHyphenated = false;
    try {
      const resp = await fetch(`${API_BASE}/segment-preview/${$sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start_ms: segRegenRange.startMs,
          end_ms: segRegenRange.endMs,
          language: segRegenLanguage,
          model_preset: segRegenPreset,
          audio_source: getSegRegenAudioSourceForApi(),
          source_type: segRegenRange.sourceType,
        }),
      });
      console.log('[SegmentAI] Preview response', { status: resp.status, ok: resp.ok });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data?.detail || data?.message || 'Preview lyrics failed');
      }

      if (Array.isArray(data?.lyrics_lines) && data.lyrics_lines.length > 0) {
        segRegenPreviewLines = data.lyrics_lines.map(v => String(v));
      } else if (typeof data?.lyrics === 'string' && data.lyrics.trim()) {
        segRegenPreviewLines = data.lyrics.split(/\n+/).map(v => v.trim()).filter(Boolean);
      } else if (typeof data?.text === 'string' && data.text.trim()) {
        segRegenPreviewLines = data.text.split(/\n+/).map(v => v.trim()).filter(Boolean);
      } else {
        segRegenPreviewLines = ['(No lyrics returned by preview endpoint)'];
      }

      if (typeof data?.confidence === 'number') {
        segRegenPreviewConfidence = data.confidence;
      } else if (typeof data?.confidence_summary?.avg === 'number') {
        segRegenPreviewConfidence = data.confidence_summary.avg;
      }

      console.log('[SegmentAI] Preview parsed', {
        lineCount: segRegenPreviewLines.length,
        confidence: segRegenPreviewConfidence,
      });

      if (segRegenAutoHyphenate && segRegenPreviewLines.length > 0) {
        try {
          segRegenPreviewLines = await hyphenateSegmentPreviewLines(segRegenPreviewLines, true);
        } catch {
          // Keep raw preview lines if hyphenation fails.
        }
      }
      showToast('Lyrics preview loaded');
    } catch (e) {
      console.error('[SegmentAI] Preview failed', e);
      segRegenPreviewError = String(e?.message || e || 'Preview failed');
      showToast('Lyrics preview failed');
    } finally {
      segRegenPreviewLoading = false;
    }
  }

  function runSegmentOneGoRegenerate() {
    const secs = ((segRegenRange.endMs - segRegenRange.startMs) / 1000).toFixed(2);
    showToast(`One-go regenerate queued for ${secs}s segment (backend wiring next)`);
  }

  function segRegenModalMouseDown(e) {
    if (e.button !== 0) return;
    if (!(e.target instanceof Element) || !e.target.closest('.seg-regen-modal-title')) return;
    segRegenModalDragging = true;
    segRegenModalDragOffsetX = e.clientX - segRegenModalX;
    segRegenModalDragOffsetY = e.clientY - segRegenModalY;
    window.addEventListener('mousemove', segRegenModalMouseMove);
    window.addEventListener('mouseup', segRegenModalMouseUp);
    e.preventDefault();
  }

  function segRegenModalMouseMove(e) {
    if (!segRegenModalDragging) return;
    segRegenModalX = Math.max(0, Math.min(window.innerWidth - 410, e.clientX - segRegenModalDragOffsetX));
    segRegenModalY = Math.max(0, Math.min(window.innerHeight - 300, e.clientY - segRegenModalDragOffsetY));
  }

  function segRegenModalMouseUp() {
    segRegenModalDragging = false;
    window.removeEventListener('mousemove', segRegenModalMouseMove);
    window.removeEventListener('mouseup', segRegenModalMouseUp);
  }

  function vibratoModalMouseDown(e) {
    if (e.button !== 0) return;
    if (!(e.target instanceof Element) || !e.target.closest('.seg-regen-modal-title')) return;
    vibratoModalDragging = true;
    vibratoModalDragOffsetX = e.clientX - vibratoModalX;
    vibratoModalDragOffsetY = e.clientY - vibratoModalY;
    window.addEventListener('mousemove', vibratoModalMouseMove);
    window.addEventListener('mouseup', vibratoModalMouseUp);
    e.preventDefault();
  }

  function vibratoModalMouseMove(e) {
    if (!vibratoModalDragging) return;
    vibratoModalX = Math.max(0, Math.min(window.innerWidth - 410, e.clientX - vibratoModalDragOffsetX));
    vibratoModalY = Math.max(0, Math.min(window.innerHeight - 300, e.clientY - vibratoModalDragOffsetY));
  }

  function vibratoModalMouseUp() {
    if (!vibratoModalDragging) return;
    vibratoModalDragging = false;
    window.removeEventListener('mousemove', vibratoModalMouseMove);
    window.removeEventListener('mouseup', vibratoModalMouseUp);
    saveEditorUiPrefs('vibrato-modal-move');
  }

  function metronomeToolMouseDown(e) {
    if (e.button !== 0) return;
    if (!(e.target instanceof Element) || !e.target.closest('.metronome-tool-title')) return;
    metronomeToolDragging = true;
    metronomeToolDragOffsetX = e.clientX - metronomeToolX;
    metronomeToolDragOffsetY = e.clientY - metronomeToolY;
    window.addEventListener('mousemove', metronomeToolMouseMove);
    window.addEventListener('mouseup', metronomeToolMouseUp);
    e.preventDefault();
  }

  function metronomeToolMouseMove(e) {
    if (!metronomeToolDragging) return;
    metronomeToolX = Math.max(0, Math.min(window.innerWidth - 300, e.clientX - metronomeToolDragOffsetX));
    metronomeToolY = Math.max(0, Math.min(window.innerHeight - 240, e.clientY - metronomeToolDragOffsetY));
  }

  function metronomeToolMouseUp() {
    metronomeToolDragging = false;
    window.removeEventListener('mousemove', metronomeToolMouseMove);
    window.removeEventListener('mouseup', metronomeToolMouseUp);
  }

  function handleGlobalClick(e) {
    if (contextMenu.visible && contextMenuEl && !contextMenuEl.contains(e.target)) {
      closeContextMenu();
    }
    if (e.target !== canvasEl && !(contextMenuEl && contextMenuEl.contains(e.target))) {
      clearMarkerSelection();
      draw();
    }
  }

  // ──── Note Actions ──────────────────────────
  function deleteNote(noteId) {
    const id = noteId ?? selectedNote;
    if (id === null) return;
    pushUndo();
    // Delete all selected notes if this note is part of a multi-selection
    const idsToDelete = (selectedNotes.size > 1 && selectedNotes.has(id))
      ? new Set(selectedNotes)
      : new Set([id]);
    notes = notes.filter(n => !idsToDelete.has(n.id));
    if (idsToDelete.has(selectedNote)) selectedNote = null;
    for (const did of idsToDelete) selectedNotes.delete(did);
    selectedNotes = new Set(selectedNotes);
    markUnsaved();
    closeContextMenu();
    computeTotalBeats();
    draw();
  }

  function splitNote(noteId, splitBeat) {
    const id = noteId ?? selectedNote;
    if (id === null) return;
    const idx = notes.findIndex(n => n.id === id);
    if (idx === -1) return;
    const note = notes[idx];
    if (note.type === 'break' || note.duration < 2) return;

    pushUndo();
    // Use the click position if provided, otherwise split at midpoint
    let halfDur;
    if (splitBeat !== undefined) {
      halfDur = Math.max(1, Math.min(note.duration - 1, Math.round(splitBeat - note.startBeat)));
    } else {
      halfDur = Math.floor(note.duration / 2);
    }
    const maxId = Math.max(...notes.map(n => n.id)) + 1;

    const note1 = { ...note, duration: halfDur };
    const note2 = {
      ...note,
      id: maxId,
      startBeat: note.startBeat + halfDur,
      duration: note.duration - halfDur,
      syllable: ' ~',
      original: { startBeat: note.startBeat + halfDur, duration: note.duration - halfDur, pitch: note.pitch },
    };

    notes = [...notes.slice(0, idx), note1, note2, ...notes.slice(idx + 1)];
    selectedNote = maxId; // select the new second note
    markUnsaved();
    closeContextMenu();
    draw();
  }

  function setNoteType(noteId, type) {
    // Apply to all selected notes if multi-select, otherwise just the one
    const ids = (selectedNotes.size > 1 && selectedNotes.has(noteId ?? selectedNote))
      ? [...selectedNotes]
      : [noteId ?? selectedNote];
    if (!ids.length || ids[0] === null) return;

    pushUndo();
    for (const id of ids) {
      const note = notes.find(n => n.id === id);
      if (!note || note.type === 'break') continue;
      if (type === 'golden') {
        note.isRap = false;
        note.isGolden = true;
      } else if (type === 'rap') {
        note.isRap = true;
        note.isGolden = false;
      } else {
        note.isRap = false;
        note.isGolden = false;
      }
    }

    notes = [...notes];
    markUnsaved();
    closeContextMenu();
    draw();
  }

  function insertBreak(noteId, position) {
    const id = noteId ?? selectedNote;
    if (id === null) return;
    const idx = notes.findIndex(n => n.id === id);
    if (idx === -1) return;
    const note = notes[idx];
    if (note.type === 'break') return;

    pushUndo();
    const maxId = Math.max(...notes.map(n => n.id)) + 1;
    const breakBeat = position === 'before'
      ? note.startBeat - 1
      : note.startBeat + note.duration + 1;
    const breakNote = { id: maxId, type: 'break', startBeat: breakBeat, endBeat: null };

    const insertIdx = position === 'before' ? idx : idx + 1;
    notes = [...notes.slice(0, insertIdx), breakNote, ...notes.slice(insertIdx)];
    markUnsaved();
    closeContextMenu();
    draw();
  }

  function addBreakAt(beat) {
    pushUndo();
    const maxId = Math.max(0, ...notes.map(n => n.id)) + 1;
    const breakNote = { id: maxId, type: 'break', startBeat: beat, endBeat: null };
    // Insert in sorted position
    let insertIdx = notes.findIndex(n => {
      const nb = n.type === 'break' ? n.startBeat : n.startBeat;
      return nb > beat;
    });
    if (insertIdx === -1) insertIdx = notes.length;
    notes = [...notes.slice(0, insertIdx), breakNote, ...notes.slice(insertIdx)];
    selectedFlag = null;
    selectedNote = maxId;
    selectedNotes = new Set([maxId]);
    markUnsaved();
    closeContextMenu();
    draw();
  }

  function addNoteAt(beat, pitch, duration = 4) {
    pushUndo();
    const maxId = Math.max(0, ...notes.map(n => n.id)) + 1;
    const newNote = {
      id: maxId,
      startBeat: beat,
      duration,
      pitch: Math.max(minPitch, Math.min(maxPitch, pitch)),
      syllable: 'word ',
      isRap: false,
      isGolden: false,
      confidence: 1.0,
      original: { startBeat: beat, duration, pitch },
    };
    // Insert in sorted position
    let insertIdx = notes.filter(n => n.type !== 'break').findIndex(n => n.startBeat > beat);
    if (insertIdx === -1) {
      // Append after last non-break note
      insertIdx = notes.length;
    } else {
      // Find the actual index in the full notes array
      const targetNote = notes.filter(n => n.type !== 'break')[insertIdx];
      insertIdx = notes.indexOf(targetNote);
    }
    notes = [...notes.slice(0, insertIdx), newNote, ...notes.slice(insertIdx)];
    selectedNote = maxId;
    markUnsaved();
    closeContextMenu();
    computeTotalBeats();
    draw();
  }

  function mergeWithNext(noteId) {
    const id = noteId ?? selectedNote;
    if (id === null) return;
    const ordered = notes
      .filter(n => n.type !== 'break')
      .slice()
      .sort((a, b) => (a.startBeat - b.startBeat) || (a.id - b.id));
    const realIdx = ordered.findIndex(n => n.id === id);
    if (realIdx === -1 || realIdx >= ordered.length - 1) return;

    const current = ordered[realIdx];
    const next = ordered[realIdx + 1];
    const mergedDuration = (next.startBeat + next.duration) - current.startBeat;
    if (mergedDuration <= 0) return;

    pushUndo();
    notes = notes
      .filter(n => n.id !== next.id)
      .map(n => {
        if (n.id !== current.id) return n;
        return {
          ...n,
          duration: mergedDuration,
          syllable: n.syllable.trimEnd() + next.syllable.trimStart(),
        };
      });
    selectedNote = current.id;
    selectedNotes = new Set([current.id]);
    markUnsaved();
    closeContextMenu();
    computeTotalBeats();
    draw();
  }

  function mergeWithPrevious(noteId) {
    const id = noteId ?? selectedNote;
    if (id === null) return;
    const ordered = notes
      .filter(n => n.type !== 'break')
      .slice()
      .sort((a, b) => (a.startBeat - b.startBeat) || (a.id - b.id));
    const realIdx = ordered.findIndex(n => n.id === id);
    if (realIdx <= 0) return;
    mergeWithNext(ordered[realIdx - 1].id);
  }

  function canMergeWithNext(noteId) {
    const id = noteId ?? selectedNote;
    if (id === null) return false;
    const ordered = notes
      .filter(n => n.type !== 'break')
      .slice()
      .sort((a, b) => (a.startBeat - b.startBeat) || (a.id - b.id));
    const idx = ordered.findIndex(n => n.id === id);
    return idx !== -1 && idx < ordered.length - 1;
  }

  function canMergeWithPrevious(noteId) {
    const id = noteId ?? selectedNote;
    if (id === null) return false;
    const ordered = notes
      .filter(n => n.type !== 'break')
      .slice()
      .sort((a, b) => (a.startBeat - b.startBeat) || (a.id - b.id));
    const idx = ordered.findIndex(n => n.id === id);
    return idx > 0;
  }

  function toggleWordSpace(noteId, hasSpace) {
    const note = notes.find(n => n.id === noteId);
    if (!note || note.type === 'break') return;
    pushUndo();
    if (hasSpace && !note.syllable.endsWith(' ')) {
      note.syllable = note.syllable + ' ';
    } else if (!hasSpace && note.syllable.endsWith(' ')) {
      note.syllable = note.syllable.trimEnd();
    }
    editingSyllable = note.syllable;
    notes = [...notes];
    markUnsaved();
    draw();
  }

  function showToast(msg, durationMs = 3000, center = false) {
    toastMsg = msg;
    toastCenter = center;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastMsg = '';
      toastCenter = false;
    }, durationMs);
  }

  function autoFixWordSpaces() {
    // Option C: Convert leading spaces (old style) to trailing spaces (new style).
    // Only relocates spaces that already exist — never adds or removes word boundaries.
    // Safe for both freshly generated songs and old imports.
    pushUndo();
    let changed = 0;
    for (let i = 0; i < notes.length; i++) {
      const note = notes[i];
      if (note.type === 'break') continue;
      if (note.syllable.startsWith(' ')) {
        // Find previous non-break note (skip over any break nodes)
        let prevNote = null;
        for (let j = i - 1; j >= 0; j--) {
          if (notes[j].type !== 'break') { prevNote = notes[j]; break; }
        }
        // Strip all leading spaces from current note
        note.syllable = note.syllable.trimStart();
        // Add trailing space to prev note (space belongs after "you", not before "make")
        if (prevNote && !prevNote.syllable.endsWith(' ')) {
          prevNote.syllable = prevNote.syllable + ' ';
        }
        changed++;
      }
    }
    notes = [...notes];
    markUnsaved();
    draw();
    showToast(changed > 0 ? `✔ Converted ${changed} leading space${changed === 1 ? '' : 's'} to trailing` : 'No leading spaces found');
    console.log(`[AutoFix] Converted ${changed} leading spaces to trailing`);
    if (changed > 0) handleSave();
  }

  // ── Audio source toggle ──
  function switchAudioSource(source) {
    const prevSource = audioSource;
    audioSource = source;
    const editedUrl = getEditedAudioUrl();
    const url = source === 'original' ? originalUrl : source === 'edited' ? editedUrl : originalVocalUrl || vocalUrl;
    console.log(`[AudioSource] Switch: ${prevSource} → ${source} | url=${url} | spliced=${segRecPatched.size > 0} cleaned=${cleanedAudioAvailable}`);
    const wasPlaying = isPlaying;
    const time = currentTimeSec || audioEl?.currentTime || 0;

    // Pause without resetting position
    if (isPlaying) {
      audioEl?.pause();
      isPlaying = false;
      cancelAnimationFrame(animFrame);
      stopAllMidiNotes();
    }

    currentAudioUrl = url; // update reactive src — Svelte will update the <audio> element
    if (audioEl) {
      editedAudioLoading = source === 'edited';
      audioEl.load();
      audioEl.oncanplay = () => {
        audioEl.currentTime = time;
        audioEl.oncanplay = null;
        if (wasPlaying) {
          togglePlayback();
        }
      };
    }
    // Re-load waveform for new source
    loadWaveform(url);
    console.log('[Step4] Audio source:', source, 'at', time.toFixed(2) + 's', wasPlaying ? '(resuming)' : '(paused)');
    saveEditorUiPrefs('audio-source');
  }

  async function handleMissingAudio(type) {
    if (type === 'vocals') {
      const ok = await showConfirm('No vocals track available.\n\nGo to Step 1 to extract vocals from the mix or upload a vocals file?', { confirmLabel: 'Go to Step 1' });
      if (ok) currentStep.set(1);
    } else {
      const ok = await showConfirm('No full mix audio available.\n\nGo to Step 1 to upload the full mix?', { confirmLabel: 'Go to Step 1' });
      if (ok) currentStep.set(1);
    }
  }

  // ── Text editor (raw Ultrastar .txt) ──
  function openTextEditor() {
    textEditorContent = buildUltrastarContent();
    showTextEditor = true;
  }

  function buildUltrastarContent() {
    // Reconstruct Ultrastar .txt from current editor notes
    const lines = [];
    // Standard headers
    lines.push(`#TITLE:${$lyricsData?.title || 'Unknown'}`);
    lines.push(`#ARTIST:${$lyricsData?.artist || 'Unknown'}`);
    lines.push(`#BPM:${bpm}`);
    lines.push(`#GAP:${gapMs}`);
    // Downbeat offset
    if (downbeatOffsetMs !== 0) {
      lines.push(`#DOWNBEATOFFSET:${Math.round(downbeatOffsetMs)}`);
    }
    // Metronome tool state
    if (metronomeManualDownbeatAnchorBeat !== null) {
      const anchorMs = gapMs + (metronomeManualDownbeatAnchorBeat * 15000 / bpm);
      lines.push(`#METRONOMEANCHOR:${Math.round(anchorMs)}`);
      lines.push(`#METRONOMEIG:${metronomeSigNumerator}/${metronomeSigDenominator}`);
      lines.push(`#METRONOMESPEED:${metronomeSpeedFactor}`);
    }
    // Extra headers (YOUTUBE, COVER, LANGUAGE, etc.)
    const standardKeys = new Set(['TITLE', 'ARTIST', 'BPM', 'GAP', 'DOWNBEATOFFSET', 'METRONOMEANCHOR', 'METRONOMEIG', 'METRONOMESPEED']);
    for (const h of extraHeaders) {
      if (!standardKeys.has(h.key.toUpperCase())) {
        // Keep #MP3 in sync with current artist/title and original file extension
        const origFilename = $uploadData?.filename || '';
        const origExt = (origFilename.match(/\.\w+$/) || ['.mp3'])[0];
        const value = h.key.toUpperCase() === 'MP3'
          ? `${$lyricsData?.artist || 'Unknown'} - ${$lyricsData?.title || 'Unknown'}${origExt}`
          : h.value;
        lines.push(`#${h.key}:${value}`);
      }
    }
    // Notes
    for (const note of notes) {
      if (note.type === 'break') {
        lines.push(`- ${note.startBeat}`);
      } else {
        const prefix = note.type === 'golden' ? '*' : note.type === 'rap' ? 'F:' : ':';
        lines.push(`${prefix} ${note.startBeat} ${note.duration} ${note.pitch} ${note.syllable}`);
      }
    }
    lines.push('E');
    return lines.join('\n');
  }

  function applyTextEditorContent() {
    const rawParsed = parseUltrastar(textEditorContent);
    const newNotes = rawParsed.map(n => {
      if (n.type !== 'break') return n;
      const startMs = Math.max(0, gapMs + n.startBeat * 15000 / bpm);
      const endMs = n.endBeat != null ? Math.max(0, gapMs + n.endBeat * 15000 / bpm) : null;
      return { ...n, timeMs: startMs, endTimeMs: endMs };
    });
    if (newNotes.length === 0) {
      showAlert('No valid notes found in the text. Check the format.');
      return;
    }
    // Extract all headers from edited text
    const parsedHeaders = [];
    for (const line of textEditorContent.split('\n')) {
      const m = line.match(/^#([\w]+):(.*)/);
      if (m) {
        const key = m[1];
        const value = m[2];
        if (key.toUpperCase() === 'BPM') bpm = parseFloat(value.replace(',', '.')) || bpm;
        else if (key.toUpperCase() === 'GAP') gapMs = parseInt(value) || gapMs;
        else if (key.toUpperCase() === 'TITLE') { /* handled by lyricsData */ }
        else if (key.toUpperCase() === 'ARTIST') { /* handled by lyricsData */ }
        else parsedHeaders.push({ key, value });
      }
    }
    extraHeaders = parsedHeaders;
    pushUndo();
    notes = newNotes;
    computeTotalBeats();
    markUnsaved();
    draw();
    showTextEditor = false;
    console.log(`[TextEditor] Applied: ${notes.length} notes, BPM=${bpm}, GAP=${gapMs}, ${extraHeaders.length} extra headers`);
    // Auto-save to backend
    handleSave();
  }

  // Track syllable undo only once when the context menu opens (not per keystroke)
  let syllableUndoPushed = false;
  function updateSyllable(noteId, text) {
    const id = noteId ?? contextMenu.noteId;
    if (id === null) return;
    const note = notes.find(n => n.id === id);
    if (!note || note.type === 'break') return;
    if (!syllableUndoPushed) {
      pushUndo();
      syllableUndoPushed = true;
    }
    note.syllable = text;
    notes = [...notes];
    markUnsaved();
    draw();
  }

  function playNotePitch(noteId) {
    const id = noteId ?? selectedNote;
    if (id === null) return;
    const note = notes.find(n => n.id === id);
    if (!note || note.type === 'break') return;
    playMidiPitch(note.pitch, Math.min(2, (note.duration * 15) / bpm));
    closeContextMenu();
  }

  function playMidiPitch(midiPitch, durationSec = 0.5) {
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    const freq = 440 * Math.pow(2, (midiPitch - 69) / 12);
    osc.frequency.value = freq;
    osc.type = 'triangle';
    gain.gain.value = 0.35;
    const fadeTime = Math.min(0.15, durationSec * 0.3);
    osc.start();
    gain.gain.setValueAtTime(0.35, audioCtx.currentTime);
    gain.gain.setValueAtTime(0.35, audioCtx.currentTime + durationSec - fadeTime);
    gain.gain.linearRampToValueAtTime(0, audioCtx.currentTime + durationSec);
    osc.stop(audioCtx.currentTime + durationSec + 0.05);
  }

  function handleWheel(event) {
    event.preventDefault();

    if (event.ctrlKey || event.metaKey) {
      // Zoom — keep the point under the cursor fixed
      const oldZoom = zoom;
      zoom = Math.max(0.5, Math.min(100, zoom + event.deltaY * -0.01));
      console.log(`[Wheel] Zoom ${oldZoom.toFixed(1)} → ${zoom.toFixed(1)}`);
      const mouseX = event.clientX - canvasEl.getBoundingClientRect().left;
      const anchorBeat = (scrollX + mouseX) / oldZoom;
      scrollX = clampScrollX(anchorBeat * zoom - mouseX);
    } else {
      // Horizontal scroll only
      if (Math.abs(event.deltaX) > 1) {
        scrollX = clampScrollX(scrollX + event.deltaX);
      }
    }

    draw();
  }

  // Scrollbar input handler
  function handleScrollbar(event) {
    // legacy — no longer used
  }

  function onScrollTrackPointerDown(e) {
    if (!scrollTrackEl) return;
    e.preventDefault();
    const rect = scrollTrackEl.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    // Click sets center beat; back-calculate left-edge scrollX
    const clickedCenterBeat = getMinBeat() + frac * scrollBeatRange;
    scrollX = clampScrollX((clickedCenterBeat - canvasW / (2 * zoom)) * zoom);
    draw();
    // Begin drag — store center beat at drag start
    scrollHandleDragging = true;
    scrollDragStartX = e.clientX;
    scrollDragStartBeat = scrollX / zoom + canvasW / (2 * zoom);
    window.addEventListener('pointermove', onScrollHandlePointerMove);
    window.addEventListener('pointerup',   onScrollHandlePointerUp);
  }

  function onScrollHandlePointerMove(e) {
    if (!scrollHandleDragging || !scrollTrackEl) return;
    const rect = scrollTrackEl.getBoundingClientRect();
    const deltaPx        = e.clientX - scrollDragStartX;
    const deltaBeat      = (deltaPx / rect.width) * scrollBeatRange;
    const newCenterBeat  = Math.min(totalBeats, Math.max(getMinBeat(), scrollDragStartBeat + deltaBeat));
    scrollX = clampScrollX((newCenterBeat - canvasW / (2 * zoom)) * zoom);
    draw();
  }

  function onScrollHandlePointerUp() {
    scrollHandleDragging = false;
    window.removeEventListener('pointermove', onScrollHandlePointerMove);
    window.removeEventListener('pointerup',   onScrollHandlePointerUp);
  }

  // ──── Playback ───────────────────────────────
  function togglePlayback() {
    if (!audioEl) {
      console.log('[Play] No audioEl');
      return;
    }
    if (setGapMode || metronomePickTarget !== 0) return; // block playback during transient editing modes

    if (isPlaying) {
      console.log(`[Play] Pausing at ${audioEl.currentTime.toFixed(2)}s, beat=${playbackBeat.toFixed(1)}`);
      // if (vocalTraceEnabled && vocalTraceFrames.length > 0) logVocalTraceState();
      quickTraceActive = false;
      quickTraceEndSec = null;
      audioEl.pause();
      currentTimeSec = audioEl.currentTime;
      isPlaying = false;
      cancelAnimationFrame(animFrame);
      stopAllMidiNotes();
      draw(); // Redraw to show paused cursor
    } else {
      // If loop is active and playhead is outside loop, jump to loop start.
      // During active segment capture (preroll/recording) we keep loop visual
      // boundaries but avoid forcing loop jumps.
      if (loopEnabled && loopStartBeat !== null && loopEndBeat !== null && segRecPhase !== 'preroll' && segRecPhase !== 'recording') {
        if (playbackBeat < loopStartBeat || playbackBeat >= loopEndBeat) {
          const loopStartTime = beatToTime(loopStartBeat);
          currentTimeSec = loopStartTime;
          playbackBeat = loopStartBeat;
          console.log(`[Play] Jumped to loop start beat ${loopStartBeat}`);
        }
      }
      // Resume from our tracked position
      audioEl.currentTime = currentTimeSec;
      audioEl.playbackRate = playbackRate;
      audioEl.preservesPitch = true;
      audioEl.volume = muteVocal ? 0 : audioVolume;
      console.log(`[Play] Starting from ${currentTimeSec.toFixed(2)}s, beat=${playbackBeat.toFixed(1)}, rate=${playbackRate}`);
      audioEl.play().catch(err => {
        console.warn('[Play] audioEl.play() rejected (autoplay policy?):', err.message);
        isPlaying = false;
        return;
      });
      isPlaying = true;
      // Clear all existing vocal trace frames when starting a new recording run.
      // Keeping frames from a previous run would mix stale data with the new trace.
      if (vocalTraceEnabled) {
        vocalTraceFrames = [];
        // Align next sample to the nearest grid step at or after current time
        vocalTraceNextSampleSec = Math.ceil(currentTimeSec / VOCAL_TRACE_STEP_SEC) * VOCAL_TRACE_STEP_SEC;
      }
      // Pre-warm vocal trace rolling median so frame 1 already has a full window
      if (vocalTraceEnabled && vocalTraceDecodedBuffer && vocalTraceDetector && vocalTraceSampleBuf) {
        warmupVocalTrace(currentTimeSec);
      }
      // Auto-show trails when recording starts
      if (vocalTraceEnabled) vocalTraceVisible = true;
      if (micEnabled) micShowTrail = true;
      // Initialize metronome to current beat so it doesn't click immediately
      if (metronomeEnabled) {
        const clickInterval = getMetronomeClickInterval();
        const offsetBeat = playbackBeat - getMetronomeClickOffset();
        lastMetronomeBeat = Math.floor(offsetBeat / clickInterval);
      }
      if (midiPlayback) ensureMidiCtx();
      updatePlayback();
    }
  }

  function logVocalTraceState(label = 'Stop summary', noteSubset = notes) {
    console.group(`[VocalTrace] ${label}`);
    console.log(`Total frames recorded: ${vocalTraceFrames.length}`);
    const vtBeatGap = vocalTraceFrames.length > 1
      ? Math.abs(vocalTraceFrames[1].beat - vocalTraceFrames[0].beat) * 1.5 : 0.15;
    for (const note of noteSubset) {
      if (note.type === 'break') continue;
      const noteEndBeat = note.startBeat + note.duration;
      // Find frames for this note
      let lo = 0, hi = vocalTraceFrames.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (vocalTraceFrames[mid].beat < note.startBeat) lo = mid + 1; else hi = mid; }
      const noteFrames = [];
      for (let i = lo; i < vocalTraceFrames.length && vocalTraceFrames[i].beat <= noteEndBeat; i++) {
        noteFrames.push(vocalTraceFrames[i]);
      }
      if (noteFrames.length === 0) continue;
      // Compute drawn blocks
      const blocks = [];
      let i = 0;
      while (i < noteFrames.length) {
        const frame = noteFrames[i];
        let fp = frame.pitch;
        while (fp - note.pitch > 6)  fp -= 12;
        while (fp - note.pitch < -6) fp += 12;
        const isHit = Math.abs(fp - note.pitch) <= pitchTolerance;
        let endBeat = frame.beat;
        if (isHit) {
          while (i + 1 < noteFrames.length) {
            let fp2 = noteFrames[i + 1].pitch;
            while (fp2 - note.pitch > 6)  fp2 -= 12;
            while (fp2 - note.pitch < -6) fp2 += 12;
            if (Math.abs(fp2 - note.pitch) > pitchTolerance) break;
            i++; endBeat = noteFrames[i].beat;
          }
          const drawnStart = Math.max(frame.beat, note.startBeat);
          const drawnEnd   = Math.min(endBeat + vtBeatGap, noteEndBeat);
          blocks.push({ type: 'HIT', start: drawnStart, end: drawnEnd, len: drawnEnd - drawnStart });
        } else {
          while (i + 1 < noteFrames.length && noteFrames[i + 1].pitch === frame.pitch) { i++; endBeat = noteFrames[i].beat; }
          blocks.push({ type: 'MISS', start: frame.beat, end: endBeat, pitch: frame.pitch, len: endBeat - frame.beat });
        }
        i++;
      }
      console.log(`Note beat=${note.startBeat}–${noteEndBeat} (dur=${note.duration}) pitch=${note.pitch} | ${noteFrames.length} frames | blocks:`);
      for (const b of blocks) {
        if (b.type === 'HIT') console.log(`  HIT  drawn=${b.start.toFixed(2)}–${b.end.toFixed(2)} len=${b.len.toFixed(2)} (note starts at ${note.startBeat}, offset=${( b.start - note.startBeat).toFixed(2)})`);
        else                  console.log(`  MISS drawn=${b.start.toFixed(2)}–${b.end.toFixed(2)} len=${b.len.toFixed(2)} pitch=${b.pitch}`);
      }
    }
    console.groupEnd();
  }

  function stopPlayback() {
    console.log('[Stop] Resetting to 0');
    // if (vocalTraceEnabled && vocalTraceFrames.length > 0) logVocalTraceState();
    quickTraceActive = false;
    quickTraceEndSec = null;
    if (audioEl) {
      audioEl.pause();
      audioEl.currentTime = 0;
    }
    isPlaying = false;
    playbackBeat = 0;
    currentTimeSec = 0;
    cancelAnimationFrame(animFrame);
    stopAllMidiNotes();
    draw();
  }

  // Seek playhead to an absolute time (seconds)
  function seekToTime(timeSec) {
    if (!audioEl) {
      console.log('[Seek] No audioEl');
      return;
    }
    const maxTime = audioEl.duration || audioDuration || 300;
    const t = Math.max(0, Math.min(maxTime, timeSec));
    console.log(`[Seek] seekToTime ${t.toFixed(2)}s`);

    // WKWebView (Tauri) doesn't always re-request the audio range when currentTime
    // is set while the stream is active — the audio keeps playing from where it was
    // buffered. Fix: reload the src with a media fragment to force a new range request.
    const inTauri = !!window.__TAURI__;
    if (inTauri && isPlaying) {
      const baseUrl = audioEl.src.split('#')[0];
      audioEl.pause();
      audioEl.src = `${baseUrl}#t=${t.toFixed(3)}`;
      audioEl.load();
      audioEl.oncanplay = () => {
        audioEl.currentTime = t;
        audioEl.oncanplay = null;
        audioEl.play().catch(() => {});
      };
    } else {
      audioEl.currentTime = t;
    }

    currentTimeSec = t;
    playbackBeat = timeToBeat(t);
    // Scroll only if the playhead would be off-screen
    const canvasWidth = canvasEl?.width || 800;
    const px = beatToX(playbackBeat);
    if (px < 0 || px > canvasWidth) {
      const minScrollX = getMinBeat() * zoom;
      scrollX = Math.max(minScrollX, playbackBeat * zoom - canvasWidth * 0.1);
    }
    draw();
  }

  function seekPlayback(deltaSec) {
    if (!audioEl) {
      console.log('[Seek] No audioEl');
      return;
    }
    const maxTime = audioEl.duration || audioDuration || 300;
    const oldTime = currentTimeSec;  // Use our tracked time, not audioEl.currentTime
    const newTime = Math.max(0, Math.min(maxTime, oldTime + deltaSec));
    console.log(`[Seek] delta=${deltaSec}s, old=${oldTime.toFixed(2)}s, new=${newTime.toFixed(2)}s, max=${maxTime.toFixed(2)}s`);
    const inTauri = !!window.__TAURI__;
    if (inTauri && isPlaying) {
      const baseUrl = audioEl.src.split('#')[0];
      audioEl.pause();
      audioEl.src = `${baseUrl}#t=${newTime.toFixed(3)}`;
      audioEl.load();
      audioEl.oncanplay = () => {
        audioEl.currentTime = newTime;
        audioEl.oncanplay = null;
        audioEl.play().catch(() => {});
      };
    } else {
      audioEl.currentTime = newTime;
    }
    currentTimeSec = newTime;
    const gapSec = gapMs / 1000;
    playbackBeat = ((newTime - gapSec) * bpm) / 15;
    // Update scroll position to follow
    const canvasWidth = canvasEl?.width || 800;
    const minScrollX = getMinBeat() * zoom;
    if (scrollMode) {
      scrollX = Math.max(minScrollX, playbackBeat * zoom - canvasWidth * 0.3);
    } else {
      const cursorX = beatToX(playbackBeat);
      if (cursorX < 0 || cursorX >= canvasWidth) {
        scrollX = Math.max(minScrollX, Math.floor(playbackBeat * zoom / canvasWidth) * canvasWidth);
      }
    }
    draw();
  }

  function handleKeydown(e) {
    // Skip all shortcuts when text editor modal is open
    if (showTextEditor) return;
    if (showNotesModal) return;
    if ($storageManagerOpen) return;
    // Skip shortcuts when typing in a text/number input field (BPM, GAP, context menu, etc.)
    // For range inputs: only block arrow keys (which move the slider); let all other shortcuts through
    if (e.target.tagName === 'INPUT' && e.target.type !== 'checkbox') {
      if (e.target.type !== 'range') return;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight' || e.key === 'ArrowUp' || e.key === 'ArrowDown') return;
    }

    console.log(`[Key] code=${e.code} key=${e.key} shift=${e.shiftKey} ctrl=${e.ctrlKey} meta=${e.metaKey}`);

    // Vibrato modal captures keyboard focus; only Escape should close it.
    if (vibratoModalOpen) {
      if (e.code === 'Escape') {
        e.preventDefault();
        closeVibratoModal();
      }
      return;
    }

    // ── No editing shortcuts during playback ──
    // Allow: Space (play/pause), arrows (seek), L (loop), M (mic), Escape, speed
    // Block: undo/redo, clipboard, note editing
    if (isPlaying) {
      // Only allow playback-related keys
      if (e.code === 'Space') { e.preventDefault(); togglePlayback(); return; }
      if (e.code === 'ArrowLeft') { e.preventDefault(); seekPlayback(e.shiftKey ? -1 : -5); return; }
      if (e.code === 'ArrowRight') { e.preventDefault(); seekPlayback(e.shiftKey ? 1 : 5); return; }
      if (e.key.toLowerCase() === 'l' && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); toggleLoop(); return; }
      if (e.key.toLowerCase() === 'm' && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); micEnabled = !micEnabled; if (micEnabled && vocalTraceEnabled) { vocalTraceEnabled = false; stopVocalTrace(); } toggleMic(); return; }
      if (e.key.toLowerCase() === 'v' && !e.ctrlKey && !e.metaKey && !e.altKey && hasVocalsAudio) { e.preventDefault(); vocalTraceEnabled = !vocalTraceEnabled; if (vocalTraceEnabled && micEnabled) { micEnabled = false; stopMic(); } toggleVocalTrace(); return; }
      if (e.code === 'Escape') {
        e.preventDefault();
        if (loopStartBeat !== null) clearLoop();
        return;
      }
      // Block everything else during playback
      return;
    }

    // Undo / Redo (Cmd+Z / Cmd+Shift+Z on Mac, Ctrl+Z / Ctrl+Shift+Z on others)
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      if (e.shiftKey) {
        redo();
      } else {
        undo();
      }
      return;
    }

    // ── Select All (Ctrl/Cmd+A) ──
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'a') {
      e.preventDefault();
      selectedNotes = new Set(notes.filter(n => n.type !== 'break').map(n => n.id));
      if (selectedNotes.size > 0) selectedNote = [...selectedNotes][0];
      draw();
      return;
    }

    // ── Clipboard shortcuts ──
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'x') {
      e.preventDefault();
      clipboardCut();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'c') {
      e.preventDefault();
      clipboardCopy();
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'v') {
      e.preventDefault();
      if (clipboard) {
        const targetBeat = getKeyboardPasteBeat();
        // If already in paste mode, paste at playhead position
        if (pasteMode) {
          finalizePaste(targetBeat);
        } else {
          // Re-enter paste mode
          pasteMode = true;
          pastePreviewBeat = targetBeat;
          draw();
        }
      }
      return;
    }

    // Spacebar: toggle play/pause
    if (e.code === 'Space') {
      e.preventDefault();
      if (!setGapMode) togglePlayback();
    }

    // Arrow keys: move selected notes, or seek if nothing selected
    const hasSelection = selectedNotes.size > 0 || selectedNote !== null;
    if (e.code === 'ArrowLeft' || e.code === 'ArrowRight' || e.code === 'ArrowUp' || e.code === 'ArrowDown') {
      e.preventDefault();

      // Arrow left/right: move editable cleanup segments by one visible grid step.
      if (selectedCleanupSegment !== null && (e.code === 'ArrowLeft' || e.code === 'ArrowRight')) {
        const direction = e.code === 'ArrowRight' ? 1 : -1;
        const mode = (e.ctrlKey || e.metaKey)
          ? (e.shiftKey ? 'start' : 'end')
          : 'move';
        const largeStep = e.shiftKey && !(e.ctrlKey || e.metaKey);
        if (adjustSelectedCleanupSegment(mode, direction, largeStep)) return;
      }

      // Arrow left/right: nudge selected flag by one visible grid line.
      if (!e.ctrlKey && !e.metaKey && !e.altKey && selectedFlag !== null && (e.code === 'ArrowLeft' || e.code === 'ArrowRight')) {
        nudgeFlag(selectedFlag, e.code === 'ArrowRight' ? 1 : -1);
        return;
      }

      // Arrow left/right: nudge selected breakpoint by one visible grid line.
      if (!e.ctrlKey && !e.metaKey && !e.altKey && selectedNote !== null && (e.code === 'ArrowLeft' || e.code === 'ArrowRight')) {
        const selectedBreak = notes.find(n => n.id === selectedNote && n.type === 'break');
        if (selectedBreak) {
          nudgeBreak(selectedBreak.id, e.code === 'ArrowRight' ? 1 : -1);
          return;
        }
      }

      // ── Option+Left/Right: Extend selection to adjacent touching notes ──
      if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && (e.code === 'ArrowLeft' || e.code === 'ArrowRight') &&
          selectedNote !== null && selectedNotes.size <= 1) {
        e.preventDefault();
        const realNotes = notes.filter(n => n.type !== 'break').sort((a, b) => (a.startBeat - b.startBeat) || (a.id - b.id));
        const currentNote = notes.find(n => n.id === selectedNote);
        if (currentNote) {
          if (e.code === 'ArrowRight') {
            // Find next touching note (starts where current ends)
            const nextNote = realNotes.find(n => n.startBeat === currentNote.startBeat + currentNote.duration);
            if (nextNote) {
              selectedNotes.add(nextNote.id);
              selectedNote = nextNote.id;
              draw();
            }
          } else {
            // Find previous touching note (ends where current starts)
            const prevNote = realNotes.find(n => n.startBeat + n.duration === currentNote.startBeat);
            if (prevNote) {
              selectedNotes.add(prevNote.id);
              selectedNote = prevNote.id;
              draw();
            }
          }
        }
        return;
      }

      // ── Shift+Option+Left/Right: Move shared boundary (only when 2 touching notes selected) ──
      if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && (e.code === 'ArrowLeft' || e.code === 'ArrowRight')) {
        e.preventDefault();
        const pair = getTouchingSelectedPair();
        if (pair) {
          const snap = snapBeatValue(1);
          const direction = e.code === 'ArrowRight' ? 1 : -1;
          const delta = direction * snap;
          
          // Left note keeps start fixed, adjusts duration
          const leftNote = notes.find(n => n.id === pair.left.id);
          // Right note adjusts start, keeps end fixed
          const rightNote = notes.find(n => n.id === pair.right.id);
          
          if (leftNote && rightNote) {
            const newLeftDur = clampValue(leftNote.duration + delta, snap, pair.sharedBeat - leftNote.startBeat + (rightNote.duration - snap));
            const newRightStart = pair.sharedBeat + delta;
            const newRightDur = rightNote.duration - delta;
            
            // Ensure both notes have minimum duration
            if (newLeftDur >= snap && newRightDur >= snap) {
              pushUndo();
              notes = notes.map(n => {
                if (n.id === pair.left.id) return { ...n, duration: newLeftDur };
                if (n.id === pair.right.id) return { ...n, startBeat: newRightStart, duration: newRightDur };
                return n;
              });
              markUnsaved();
              draw();
            }
          }
        }
        return;
      }

      // Ctrl+Left/Right (single note only): resize duration from right edge
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.code === 'ArrowLeft' || e.code === 'ArrowRight') &&
          selectedNotes.size <= 1 && selectedNote !== null) {
        const snap = snapBeatValue(1); // one grid unit
        const delta = e.code === 'ArrowRight' ? snap : -snap;
        const note = notes.find(n => n.id === selectedNote);
        if (note && note.type !== 'break') {
          const { maxBeat } = getSongBeatBounds();
          const maxDur = Number.isFinite(maxBeat)
            ? Math.max(snap, Math.floor(maxBeat - note.startBeat))
            : Infinity;
          const newDur = clampValue(note.duration + delta, snap, maxDur);
          if (newDur !== note.duration) {
            pushUndo();
            notes = notes.map(n => n.id === selectedNote ? { ...n, duration: newDur } : n);
            const updated = notes.find(n => n.id === selectedNote);
            if (updated) ensureBeatVisible(updated.startBeat + updated.duration, 8);
            markUnsaved();
            draw();
          }
        }
        return;
      }

      // Shift+Ctrl+Left/Right (single note only): resize from left edge (moves startBeat, keeps endBeat fixed)
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && !e.altKey && (e.code === 'ArrowLeft' || e.code === 'ArrowRight') &&
          selectedNotes.size <= 1 && selectedNote !== null) {
        const snap = snapBeatValue(1);
        const delta = e.code === 'ArrowRight' ? snap : -snap;
        const note = notes.find(n => n.id === selectedNote);
        if (note && note.type !== 'break') {
          const { minBeat } = getSongBeatBounds();
          const maxStart = note.startBeat + note.duration - snap;
          const newStart = clampValue(note.startBeat + delta, Math.ceil(minBeat), maxStart);
          const newDur = note.duration - (newStart - note.startBeat);
          if (newDur !== note.duration) {
            pushUndo();
            notes = notes.map(n => n.id === selectedNote ? { ...n, startBeat: newStart, duration: newDur } : n);
            const updated = notes.find(n => n.id === selectedNote);
            if (updated) ensureBeatVisible(updated.startBeat, 8);
            markUnsaved();
            draw();
          }
        }
        return;
      }

      if (hasSelection) {
        const ids = selectedNotes.size > 0 ? selectedNotes : new Set([selectedNote]);
        const pitchStep = (e.shiftKey || e.ctrlKey || e.metaKey) ? 12 : 1;
        const isHorizontalMove = e.code === 'ArrowLeft' || e.code === 'ArrowRight';
        const selectedNoteObjects = notes.filter(n => ids.has(n.id) && n.type !== 'break');
        const moveDelta = isHorizontalMove
          ? clampSelectedMoveDeltaToSongBounds(
              e.code === 'ArrowLeft' ? -(e.shiftKey ? 4 : 1) : (e.shiftKey ? 4 : 1),
              selectedNoteObjects,
            )
          : 0;
        if (isHorizontalMove && moveDelta === 0) return;
        pushUndo();
        notes = notes.map(n => {
          if (!ids.has(n.id) || n.type === 'break') return n;
          if (isHorizontalMove) return { ...n, startBeat: n.startBeat + moveDelta };
          if (e.code === 'ArrowUp')    return { ...n, pitch: n.pitch + pitchStep };
          if (e.code === 'ArrowDown')  return { ...n, pitch: n.pitch - pitchStep };
          return n;
        });
        // Play pitch preview on up/down — only for single note
        if ((e.code === 'ArrowUp' || e.code === 'ArrowDown') && selectedNotes.size <= 1) {
          const previewId = selectedNotes.size === 0 ? selectedNote : [...selectedNotes][0];
          const movedNote = notes.find(n => n.id === previewId);
          if (movedNote) {
            if (dragOscStopTimer) { clearTimeout(dragOscStopTimer); dragOscStopTimer = null; }
            startDragOsc(movedNote.pitch);
            dragOscStopTimer = setTimeout(() => { stopDragOsc(); dragOscStopTimer = null; }, 400);
          }
        }
        if (isHorizontalMove && selectedNoteObjects.length === 1) {
          const movedStart = Math.min(...selectedNoteObjects.map(n => n.startBeat + moveDelta));
          const movedEnd = Math.max(...selectedNoteObjects.map(n => n.startBeat + n.duration + moveDelta));
          ensureBeatVisible(e.code === 'ArrowLeft' ? movedStart : movedEnd, 8);
        }
        markUnsaved();
        updatePitchRange();
        draw();
      } else {
        if (e.code === 'ArrowLeft') {
          nudgeViewport(e.shiftKey ? -160 : -48);
          return;
        }
        if (e.code === 'ArrowRight') {
          nudgeViewport(e.shiftKey ? 160 : 48);
          return;
        }
      }
      return;
    }

    // Tab / Shift+Tab: select next / previous note (single selection only)
    if (e.code === 'Tab' && !e.ctrlKey && !e.metaKey && !e.altKey && selectedNote !== null && selectedNotes.size <= 1) {
      e.preventDefault();
      const realNotes = notes
        .filter(n => n.type !== 'break')
        .sort((a, b) => (a.startBeat - b.startBeat) || (a.id - b.id));
      const idx = realNotes.findIndex(n => n.id === selectedNote);
      if (idx !== -1) {
        const targetIdx = e.shiftKey ? idx - 1 : idx + 1;
        if (targetIdx >= 0 && targetIdx < realNotes.length) {
          const targetId = realNotes[targetIdx].id;
          selectedNote = targetId;
          selectedNotes = new Set([targetId]);
          draw();
        }
      }
      return;
    }

    // L: toggle loop on/off
    if (e.key.toLowerCase() === 'l' && !e.ctrlKey && !e.metaKey && !e.altKey && selectedNote === null) {
      e.preventDefault();
      toggleLoop();
    }

    // M: toggle mic sing-along
    if (e.key.toLowerCase() === 'm' && !e.ctrlKey && !e.metaKey && !e.altKey && !contextMenu.visible) {
      e.preventDefault();
      micEnabled = !micEnabled;
      if (micEnabled && vocalTraceEnabled) { vocalTraceEnabled = false; stopVocalTrace(); }
      toggleMic();
    }

    // 9: toggle MIDI playback
    if (e.key === '9' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      toggleMidiPlayback();
    }

    // 0: toggle metronome
    if (e.key === '0' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      toggleMetronome();
    }

    // Ctrl/Cmd+G: Set GAP mode
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'g') {
      e.preventDefault();
      if (setGapMode) {
        cancelSetGapMode();
      } else {
        enterSetGapMode();
      }
      return;
    }

    // Escape: cancel setGap mode, paste mode, clear loop, or deselect
    if (e.code === 'Escape') {
      e.preventDefault();
      if (beatMarkerMode) {
        exitBeatMarkerMode();
        draw();
      } else if (setGapMode) {
        cancelSetGapMode();
      } else if (metronomePickTarget === 1 || metronomePickTarget === 2) {
        clearMetronomePickTarget();
        showToast('Metronome downbeat pick cancelled');
      } else if (pasteMode) {
        cancelPaste();
      } else if (selectedCleanupSegment !== null) {
        selectedCleanupSegment = null;
        draw();
      } else if (selectedNotes.size > 0) {
        selectedNotes = new Set();
        selectedNote = null;
        draw();
      } else if (loopStartBeat !== null) {
        clearLoop();
      }
    }

    if ((e.code === 'Delete' || e.code === 'Backspace') && selectedCleanupSegment !== null) {
      e.preventDefault();
      deleteCleanupSegment(selectedCleanupSegment);
      return;
    }

    // Delete selected flag or selected breakpoint directly.
    if ((e.code === 'Delete' || e.code === 'Backspace') && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (selectedFlag !== null) {
        e.preventDefault();
        deleteFlag(selectedFlag);
        return;
      }
      if (selectedNote !== null) {
        const selectedBreak = notes.find(n => n.id === selectedNote && n.type === 'break');
        if (selectedBreak) {
          e.preventDefault();
          deleteNote(selectedBreak.id);
          return;
        }
      }
    }

    // Note action shortcuts (selected note or currently opened note context menu)
    if ((selectedNote !== null || (contextMenu.visible && contextMenu.noteId !== null)) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const shortcutNoteId = contextMenu.visible && contextMenu.noteId !== null
        ? contextMenu.noteId
        : selectedNote;

      if (e.key.toLowerCase() === 'p') {
        e.preventDefault();
        playNotePitch(shortcutNoteId);
      }

      if (e.code === 'Delete' || e.code === 'Backspace') {
        e.preventDefault();
        if (selectedNotes.size > 1) {
          pushUndo();
          notes = notes.filter(n => !selectedNotes.has(n.id));
          selectedNotes = new Set();
          selectedNote = null;
          editorState.update(s => ({ ...s, hasChanges: true }));
          hasUnsavedChanges = true;
          draw();
        } else {
          deleteNote(selectedNote);
        }
      }
      if (e.key.toLowerCase() === 's' && !e.shiftKey && contextMenu.visible) {
        e.preventDefault();
        splitNote(shortcutNoteId);
      }
      if (e.key.toLowerCase() === 'j' && e.shiftKey) {
        e.preventDefault();
        mergeWithPrevious(shortcutNoteId);
        return;
      }
      if (e.key.toLowerCase() === 'j' && !e.shiftKey) {
        e.preventDefault();
        mergeWithNext(shortcutNoteId);
        return;
      }
    }
  }

  function updatePlayback() {
    if (!isPlaying) return;

    const currentTime = audioEl.currentTime;
    currentTimeSec = currentTime;
    const gapSec = gapMs / 1000;
    playbackBeat = ((currentTime - gapSec) * bpm) / 15;

    // ── Loop wrap ──
    // Segment recording keeps loop visuals, but wrapping is disabled while
    // recording/preroll is active.
    if (loopEnabled && loopStartBeat !== null && loopEndBeat !== null && segRecPhase !== 'preroll' && segRecPhase !== 'recording') {
      if (playbackBeat >= loopEndBeat) {
        const loopStartTime = beatToTime(loopStartBeat);
        audioEl.currentTime = loopStartTime;
        currentTimeSec = loopStartTime;
        playbackBeat = loopStartBeat;
        // Stop all midi notes so they retrigger cleanly
        if (midiPlayback) stopAllMidiNotes();
        // Clear sung blocks for notes in the loop region so each pass starts fresh
        if (micEnabled && micNoteHits.size > 0) {
          for (const note of notes) {
            if (note.type === 'break') continue;
            const noteEnd = note.startBeat + note.duration;
            // Clear if note overlaps the loop region
            if (noteEnd > loopStartBeat && note.startBeat < loopEndBeat) {
              micNoteHits.delete(note.id);
            }
          }
        }
        // Clear vocal trace frames in the loop region so each pass starts fresh
        if (vocalTraceEnabled && vocalTraceFrames.length > 0) {
          vocalTraceFrames = vocalTraceFrames.filter(f => f.beat < loopStartBeat || f.beat >= loopEndBeat);
          vocalTraceRecentPitches = [];
          vocalTraceLastPitch = -1;
          vocalTracePitchConfidence = 0;
          vocalTraceNextSampleSec = Math.ceil(beatToTime(loopStartBeat) / VOCAL_TRACE_STEP_SEC) * VOCAL_TRACE_STEP_SEC;
          if (vocalTraceDecodedBuffer && vocalTraceDetector && vocalTraceSampleBuf) {
            warmupVocalTrace(loopStartTime);
          }
        }
        console.log(`[Loop] Wrapped to beat ${loopStartBeat}`);
      }
    }

    // Recording hard stop at the segment end so cursor does not drift past
    // the active range while MediaRecorder stop is still pending.
    if (segRecPhase === 'recording' && segRecSegmentId !== null) {
      const seg = cleanupSegments.find(s => s.id === segRecSegmentId);
      if (seg && currentTimeSec >= (seg.endMs / 1000)) {
        const segEndSec = seg.endMs / 1000;
        if (audioEl) audioEl.currentTime = segEndSec;
        currentTimeSec = segEndSec;
        playbackBeat = timeToBeat(segEndSec);
        draw();
        stopSegmentRecording();
        return;
      }
    }

    // Scroll logic
    const canvasWidth = canvasEl?.width || 800;
    const minScrollX = getMinBeat() * zoom;
    if (loopEnabled && loopStartBeat !== null && loopEndBeat !== null) {
      // Keep normal page/follow behavior during loops too, but clamp scroll so
      // the active viewport stays anchored to the loop region.
      const loopMinScroll = Math.max(minScrollX, loopStartBeat * zoom);
      const loopMaxScroll = Math.max(loopMinScroll, (loopEndBeat * zoom) - canvasWidth);
      if (scrollMode) {
        const targetScroll = playbackBeat * zoom - canvasWidth * 0.3;
        scrollX = Math.max(loopMinScroll, Math.min(loopMaxScroll, targetScroll));
      } else {
        const cursorX = beatToX(playbackBeat);
        if (cursorX >= canvasWidth || cursorX < 0) {
          const targetScroll = Math.floor(playbackBeat * zoom / canvasWidth) * canvasWidth;
          scrollX = Math.max(loopMinScroll, Math.min(loopMaxScroll, targetScroll));
        }
      }
    } else if (scrollMode) {
      // Fixed cursor: cursor stays at 30%, notes scroll
      scrollX = Math.max(minScrollX, playbackBeat * zoom - canvasWidth * 0.3);
    } else {
      // Page mode: jump to next page only when cursor exits right edge
      const cursorX = beatToX(playbackBeat);
      if (cursorX >= canvasWidth || cursorX < 0) {
        scrollX = Math.max(minScrollX, Math.floor(playbackBeat * zoom / canvasWidth) * canvasWidth);
      }
    }

    draw();
    if (midiPlayback) updateMidiPlayback(playbackBeat);
    if (metronomeEnabled) updateMetronome(playbackBeat);
    if (micEnabled && micAnalyser) sampleMicPitch(currentTimeSec);
    if (vocalTraceEnabled && vocalTraceDecodedBuffer) {
      // Fixed-grid: sample at deterministic timeSec positions regardless of rAF timing
      while (vocalTraceNextSampleSec <= currentTimeSec) {
        sampleVocalTrace(vocalTraceNextSampleSec);
        vocalTraceNextSampleSec += VOCAL_TRACE_STEP_SEC;
      }
    }

    if (quickTraceActive && quickTraceEndSec !== null && currentTimeSec >= quickTraceEndSec) {
      quickTraceActive = false;
      quickTraceEndSec = null;
      if (vocalTraceFrames.length > 0) logVocalTraceState(`Trace ${TRACE_SCOPE_LABEL} summary`);
      // Pause at the end of the short trace window so users can iterate quickly.
      togglePlayback();
      return;
    }
    animFrame = requestAnimationFrame(updatePlayback);
  }

  function medianPitch(values) {
    if (!values || values.length === 0) return 60;
    const s = [...values].sort((a, b) => a - b);
    return s[Math.floor(s.length / 2)];
  }

  function groupPitchFrames(frames, maxGapBeat = 0.45, maxPitchJump = 2) {
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

  function mergePlaceholderNotes(inputNotes, maxGapBeat = 0.35, maxPitchJump = 3) {
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

  function trimPlaceholdersAgainstNotes(placeholders, fixedNotes) {
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

  function buildPitchSplitGroups(frames, startBeat, endBeat, vtBeatGap) {
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

  function splitPlaceholderNotesByPitchRuns(placeholders, framePool, startBeat, endBeat, vtBeatGap) {
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

  function splitWordIntoSyllablesSimple(word) {
    const lettersOnly = (word || '').replace(/[^A-Za-z]/g, '');
    if (!lettersOnly) return [word || ''];

    // Readability-first exceptions for high-frequency words where the
    // generic onset heuristic often feels unintuitive in karaoke text.
    const preferred = {
      another: ['a', 'no', 'ther'],
    };
    const preferredSplit = preferred[lettersOnly.toLowerCase()];
    if (preferredSplit) return preferredSplit;

    const lower = lettersOnly.toLowerCase();
    const isVowel = ch => /[aeiouy]/.test(ch);
    const parts = [];
    let i = 0;

    while (i < lettersOnly.length) {
      const onsetStart = i;

      // Onset: leading consonants before the vowel nucleus.
      while (i < lettersOnly.length && !isVowel(lower[i])) i += 1;
      if (i >= lettersOnly.length) {
        if (parts.length) parts[parts.length - 1] += lettersOnly.slice(onsetStart);
        else parts.push(lettersOnly);
        break;
      }

      // Nucleus: contiguous vowel group.
      while (i < lettersOnly.length && isVowel(lower[i])) i += 1;

      // Following consonant cluster before the next vowel.
      const consonantStart = i;
      while (i < lettersOnly.length && !isVowel(lower[i])) i += 1;
      const hasNextVowel = i < lettersOnly.length;

      // If there is another vowel ahead, keep one consonant for next syllable onset
      // (maximal onset style). Otherwise keep the tail in this final syllable.
      let syllableEnd = i;
      if (hasNextVowel) {
        const clusterLen = i - consonantStart;
        syllableEnd = clusterLen <= 1 ? consonantStart : i - 1;
      }

      const piece = lettersOnly.slice(onsetStart, Math.max(onsetStart + 1, syllableEnd));
      if (piece) parts.push(piece);

      if (hasNextVowel) {
        i = syllableEnd;
      }
    }

    return parts.length > 1 ? parts : [word];
  }

  function isSyllableSplitLowConfidence(originalWord, parts) {
    if (!Array.isArray(parts) || parts.length < 2) return true;
    const lettersOnly = (originalWord || '').replace(/[^A-Za-z]/g, '').toLowerCase();
    if (!lettersOnly) return true;

    // Guard against clearly unnatural boundaries in consonant clusters.
    for (let i = 0; i < parts.length - 1; i++) {
      const left = (parts[i] || '').toLowerCase();
      const right = (parts[i + 1] || '').toLowerCase();
      if (!left || !right) return true;

      const leftLast = left[left.length - 1] || '';
      const rightFirst = right[0] || '';
      if ((leftLast === 't' && rightFirst === 'h') ||
          (leftLast === 'c' && rightFirst === 'h') ||
          (leftLast === 'n' && rightFirst === 'g')) {
        return true;
      }
    }

    // Very short first chunk followed by a long consonant-start chunk is
    // often a bad split (e.g. ho-meless, ma-keup).
    if (parts.length === 2) {
      const left = parts[0];
      const right = parts[1];
      const rightStartsWithVowel = /^[aeiouy]/i.test(right);
      if (left.length <= 2 && right.length >= 4 && !rightStartsWithVowel) return true;
      if (left.length <= 3 && right.length >= 4 && /^h/i.test(right)) return true;
    }

    return false;
  }

  function formatContinuationSegments(originalWord, segmentCount) {
    if (segmentCount <= 1) return [`${originalWord} `];
    const out = [];
    for (let i = 0; i < segmentCount; i++) {
      if (i === 0) out.push(`${originalWord}`);
      else if (i === segmentCount - 1) out.push('~ ');
      else out.push('~');
    }
    return out;
  }

  function formatSyllablesForSegments(originalWord, segmentCount) {
    if (segmentCount <= 1) return [`${originalWord} `];

    const syllables = splitWordIntoSyllablesSimple(originalWord);
    if (isSyllableSplitLowConfidence(originalWord, syllables)) {
      // Keep pitch segmentation, but avoid forcing unreliable text hyphenation.
      return formatContinuationSegments(originalWord, segmentCount);
    }
    if (syllables.length <= 1) {
      // One syllable split by pitch only: continuation marker, no word space break.
      return formatContinuationSegments(originalWord, segmentCount);
    }

    // Map syllables onto note segments proportionally and preserve word ending only
    // on the final segment.
    const out = [];
    for (let i = 0; i < segmentCount; i++) {
      const start = Math.floor((i * syllables.length) / segmentCount);
      const end = Math.floor(((i + 1) * syllables.length) / segmentCount);
      const part = syllables.slice(start, Math.max(start + 1, end)).join('');
      if (i < segmentCount - 1) out.push(`${part}-`);
      else out.push(`${part} `);
    }
    return out;
  }

  function mergeSplitGroupsToCount(splitGroups, targetCount) {
    if (!Array.isArray(splitGroups) || splitGroups.length <= targetCount) {
      return splitGroups || [];
    }
    const merged = [];
    for (let i = 0; i < splitGroups.length; i++) {
      const bucketStart = Math.floor((i * targetCount) / splitGroups.length);
      const bucket = Math.min(targetCount - 1, bucketStart);
      const group = splitGroups[i];
      const prev = merged[bucket];
      if (!prev) {
        merged[bucket] = { ...group };
        continue;
      }
      prev.startBeat = Math.min(prev.startBeat, group.startBeat);
      const prevEnd = prev.startBeat + prev.duration;
      const groupEnd = group.startBeat + group.duration;
      prev.duration = Math.max(1, Math.max(prevEnd, groupEnd) - prev.startBeat);
      prev.pitch = Math.round((prev.pitch + group.pitch) / 2);
    }
    return merged.filter(Boolean);
  }

  function normalizeWordSegments(segments, noteStart, noteEnd) {
    if (!Array.isArray(segments) || !segments.length) return [];
    const sorted = [...segments].sort((a, b) => a.startBeat - b.startBeat);
    const normalized = [];
    let cursor = Math.round(noteStart);
    const finalEnd = Math.round(Math.max(noteStart + 1, noteEnd));

    for (let i = 0; i < sorted.length; i++) {
      const seg = sorted[i];
      const rawStart = Math.round(seg.startBeat);
      const rawEnd = Math.round(seg.startBeat + seg.duration);
      const nextRawStart = i < sorted.length - 1 ? Math.round(sorted[i + 1].startBeat) : finalEnd;

      let start = Math.max(cursor, rawStart);
      start = Math.min(start, finalEnd - 1);

      let end;
      if (i === sorted.length - 1) {
        end = finalEnd;
      } else {
        const cappedByNext = Math.max(start + 1, Math.min(rawEnd, nextRawStart));
        end = Math.min(finalEnd, cappedByNext);
      }

      if (end <= start) {
        if (i === sorted.length - 1) {
          start = Math.max(noteStart, finalEnd - 1);
          end = finalEnd;
        } else {
          end = Math.min(finalEnd, start + 1);
        }
      }

      normalized.push({
        ...seg,
        startBeat: start,
        duration: Math.max(1, end - start),
      });
      cursor = end;
      if (cursor >= finalEnd) break;
    }

    if (!normalized.length) {
      return [{ ...segments[0], startBeat: Math.round(noteStart), duration: Math.max(1, Math.round(noteEnd - noteStart)) }];
    }

    const last = normalized[normalized.length - 1];
    const lastEnd = last.startBeat + last.duration;
    if (lastEnd < finalEnd) {
      last.duration += (finalEnd - lastEnd);
    }
    return normalized;
  }

  function splitRecognizedWordNotes(wordSpan, spanFrames, fallbackFrames, startBeat, endBeat, vtBeatGap, wordIndex) {
    const pitchSource = spanFrames.length > 0 ? spanFrames : fallbackFrames;
    if (!pitchSource.length) return [];
    const { runStats, stableRuns, splitGroups } = buildPitchSplitGroups(spanFrames, startBeat, endBeat, vtBeatGap);
    const noteStart = Math.round(Math.max(startBeat, wordSpan.startBeat));
    const noteEnd = Math.round(Math.min(endBeat, wordSpan.endBeat + vtBeatGap));
    const baseDuration = Math.max(1, noteEnd - noteStart);
    const syllables = splitWordIntoSyllablesSimple(wordSpan.word);

    console.log('[Analyze5s] split decision', {
      word: wordSpan.word,
      spanFrames: spanFrames.length,
      runs: runStats,
      stableRuns,
      splitGroups,
    });

    if (splitGroups.length < 2) {
      // No stable multi-pitch evidence: keep recognized word as a single note.
      // This avoids text-only forced splits that can create spelling artifacts.
      return [{
        startBeat: noteStart,
        duration: baseDuration,
        pitch: medianPitch(pitchSource.map(frame => frame.pitch)),
        syllable: `${wordSpan.word} `,
        analyzeWordIndex: wordIndex,
      }];
    }

    // Keep one-syllable words intact to avoid hyphen-only fragments like "-".
    if (syllables.length < 2) {
      return [{
        startBeat: noteStart,
        duration: baseDuration,
        pitch: medianPitch(pitchSource.map(frame => frame.pitch)),
        syllable: `${wordSpan.word} `,
        analyzeWordIndex: wordIndex,
      }];
    }

    const targetSplitCount = Math.min(3, syllables.length);
    const compressedGroups = mergeSplitGroupsToCount(splitGroups, targetSplitCount);
    const normalizedGroups = normalizeWordSegments(compressedGroups, noteStart, noteEnd);
    const hasSplit = normalizedGroups.length > 1;
    const syllableParts = formatSyllablesForSegments(wordSpan.word, normalizedGroups.length);
    console.log('[Analyze5s] syllable map', {
      word: wordSpan.word,
      splitCount: normalizedGroups.length,
      syllableParts,
    });
    return normalizedGroups.map((group, index) => ({
      startBeat: group.startBeat,
      duration: Math.max(1, group.duration),
      pitch: group.pitch,
      // Use conservative syllable mapping for split words while preserving
      // old continuation behavior for 1-syllable words.
      syllable: hasSplit ? (syllableParts[index] || '-') : `${wordSpan.word} `,
      analyzeWordIndex: wordIndex,
    }));
  }

  function capRecognizedWordOverlaps(proposals) {
    // Sort ALL notes by start beat, then by type (recognized words before placeholders for stable ordering)
    const sorted = proposals
      .map(n => ({ note: n, isRecognized: Number.isInteger(n.analyzeWordIndex) }))
      .sort((a, b) => {
        if (a.note.startBeat !== b.note.startBeat) return a.note.startBeat - b.note.startBeat;
        // Recognized words before placeholders at same start beat (shouldn't happen, but for stability)
        return (b.isRecognized ? 1 : 0) - (a.isRecognized ? 1 : 0);
      });

    // Cap overlaps between all adjacent notes
    for (let i = 0; i < sorted.length - 1; i++) {
      const cur = sorted[i].note;
      const next = sorted[i + 1].note;
      const curEnd = cur.startBeat + cur.duration;

      // Keep at least a 1-beat gap to avoid touching/overlapping notes.
      if (curEnd >= next.startBeat) {
        const newDur = Math.max(1, next.startBeat - cur.startBeat - 1);
        cur.duration = newDur;
      }
    }
  }

  function assignWordsToProposals(proposals, wordSpans) {
    if (!proposals.length || !wordSpans.length) return { assigned: 0, unassigned: wordSpans.length };
    let assigned = 0;
    for (const w of wordSpans) {
      const indexed = proposals.find(n => n.analyzeWordIndex === w.index);
      if (indexed) {
        if (!indexed.syllable || indexed.syllable.trim() === '' || indexed.syllable.trim() === '...') {
          indexed.syllable = `${w.word} `;
        }
        assigned += 1;
        continue;
      }
      let best = null;
      let bestOverlap = -1;
      for (const n of proposals) {
        const nStart = n.startBeat;
        const nEnd = n.startBeat + n.duration;
        const overlap = Math.max(0, Math.min(nEnd, w.endBeat) - Math.max(nStart, w.startBeat));
        if (overlap > bestOverlap) {
          bestOverlap = overlap;
          best = n;
        }
      }
      if (!best) continue;

      // If no overlap exists, attach to nearest note center.
      if (bestOverlap <= 0) {
        const wc = (w.startBeat + w.endBeat) / 2;
        let nearest = proposals[0];
        let bestDist = Infinity;
        for (const n of proposals) {
          const nc = n.startBeat + n.duration / 2;
          const d = Math.abs(wc - nc);
          if (d < bestDist) {
            bestDist = d;
            nearest = n;
          }
        }
        best = nearest;
      }

      if (!best.syllable || best.syllable.trim() === '' || best.syllable.trim() === '...') {
        best.syllable = `${w.word} `;
      }
      assigned += 1;
    }
    return { assigned, unassigned: Math.max(0, wordSpans.length - assigned) };
  }

  function buildNotesFromAnalyzeWindow(startSec, endSec, words) {
    const startBeat = timeToBeat(startSec);
    const endBeat = timeToBeat(endSec);
    const framePool = vocalTraceFrames.filter(f => f.beat >= startBeat && f.beat <= endBeat);
    const vtBeatGap = (VOCAL_TRACE_STEP_SEC * bpm) / 15;

    const proposalNotes = [];
    const unknownProposalNotes = [];
    let nextId = Math.max(0, ...notes.map(n => typeof n.id === 'number' ? n.id : 0));

    const wordSpans = (words || []).map(w => ({
      ...w,
      index: Number.parseInt(w.id, 10),
      startBeat: timeToBeat(w.start),
      endBeat: timeToBeat(w.end),
    }));
    console.log('[Analyze5s] frame pool', {
      frameCount: framePool.length,
      startBeat,
      endBeat,
      wordSpans: wordSpans.length,
    });

    // 1) Recognized words → one note per word span to avoid over-segmentation.
    for (let wi = 0; wi < wordSpans.length; wi++) {
      const w = wordSpans[wi];
      const spanFrames = framePool.filter(f => f.beat >= w.startBeat && f.beat <= w.endBeat);
      const hasWordFrames = spanFrames.length > 0;
      const fallbackPool = framePool.filter(f => f.beat >= (w.startBeat - 1.5) && f.beat <= (w.endBeat + 1.5));
      const pitchSource = hasWordFrames ? spanFrames : (fallbackPool.length > 0 ? fallbackPool : framePool);
      const wordNotes = splitRecognizedWordNotes(w, spanFrames, pitchSource, startBeat, endBeat, vtBeatGap, wi);
      for (let noteIndex = 0; noteIndex < wordNotes.length; noteIndex++) {
        const note = wordNotes[noteIndex];
        proposalNotes.push({
          id: ++nextId,
          startBeat: note.startBeat,
          duration: note.duration,
          pitch: note.pitch,
          syllable: note.syllable,
          type: ':',
          isRap: false,
          isGolden: false,
          analyzeWordIndex: note.analyzeWordIndex,
        });
        console.log('[Analyze5s] word note', {
          idx: wi,
          segment: noteIndex,
          word: w.word,
          startBeat: note.startBeat,
          endBeat: note.startBeat + note.duration,
          dur: note.duration,
          hasWordFrames,
          spanFrames: spanFrames.length,
          fallbackFrames: fallbackPool.length,
          pitch: note.pitch,
          splitCount: wordNotes.length,
        });
      }
    }

    // 2) Unrecognized voiced regions → placeholder notes
    const uncoveredFrames = framePool.filter(f => !wordSpans.some(w => f.beat >= w.startBeat && f.beat <= w.endBeat));
    const unknownGroups = groupPitchFrames(uncoveredFrames, 0.65, 4);
    console.log('[Analyze5s] uncovered pitch groups', {
      uncoveredFrames: uncoveredFrames.length,
      unknownGroups: unknownGroups.length,
    });
    for (const g of unknownGroups) {
      const gStart = Math.round(Math.max(startBeat, g[0].beat));
      const gEnd = Math.round(Math.min(endBeat, g[g.length - 1].beat + vtBeatGap));
      const dur = Math.max(1, gEnd - gStart);
      const pitch = medianPitch(g.map(x => x.pitch));
      unknownProposalNotes.push({
        id: ++nextId,
        startBeat: gStart,
        duration: dur,
        pitch,
        syllable: '... ',
        type: ':',
        isRap: false,
        isGolden: false,
      });
    }

    const mergedUnknown = mergePlaceholderNotes(unknownProposalNotes);
    capRecognizedWordOverlaps(proposalNotes);
    const overlapCleanup = trimPlaceholdersAgainstNotes(mergedUnknown, proposalNotes);
    const placeholderSplit = splitPlaceholderNotesByPitchRuns(overlapCleanup.trimmed, framePool, startBeat, endBeat, vtBeatGap);
    proposalNotes.push(...placeholderSplit.notes);
    // Split placeholders can reintroduce overlaps (tail extension), so cap again on full proposal set.
    capRecognizedWordOverlaps(proposalNotes);
    const assignment = assignWordsToProposals(proposalNotes, wordSpans);
    console.log('[Analyze5s] proposal post-process', {
      recognizedWordNotes: wordSpans.length,
      unknownRaw: unknownProposalNotes.length,
      unknownMerged: mergedUnknown.length,
      unknownTrimmed: overlapCleanup.trimmed.length,
      unknownDropped: overlapCleanup.dropped,
      unknownSplit: overlapCleanup.split,
      placeholderSplit: placeholderSplit.split,
      wordAssignments: assignment,
      totalProposals: proposalNotes.length,
    });

    // Replace non-break notes in analyzed window with proposals.
    const remaining = notes.filter(n => n.type === 'break' || (n.startBeat + n.duration <= startBeat || n.startBeat >= endBeat));
    let reassignedId = Math.max(0, ...remaining.map(n => typeof n.id === 'number' ? n.id : 0));
    const proposalNotesWithUniqueIds = proposalNotes.map(note => ({
      ...note,
      id: ++reassignedId,
    }));
    console.log('[Analyze5s] replacement', {
      existingNotes: notes.length,
      keptOutsideWindow: remaining.length,
      inserted: proposalNotesWithUniqueIds.length,
    });
    notes = [...remaining, ...proposalNotesWithUniqueIds]
      .map(note => {
        const { analyzeWordIndex, ...cleanNote } = note;
        return cleanNote;
      })
      .sort((a, b) => a.startBeat - b.startBeat);
    return {
      proposalCount: proposalNotes.length,
      frameCount: framePool.length,
      wordCount: wordSpans.length,
      unknownMergedCount: overlapCleanup.trimmed.length,
      assignedWords: assignment.assigned,
    };
  }


  // Set playback speed
  function setPlaybackRate(rate) {
    console.log(`[Speed] Set playback rate: ${rate}x`);
    playbackRate = rate;
    if (audioEl) {
      audioEl.playbackRate = rate;
      audioEl.preservesPitch = true;
    }
    saveEditorUiPrefs('playback-rate');
  }

  // ──── MIDI Pitch Playback ────────────────────
  function ensureMidiCtx() {
    if (!midiAudioCtx || midiAudioCtx.state === 'closed') {
      midiAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
  }

  function updateMidiPlayback(currentBeat) {
    if (!midiAudioCtx) return;

    for (const note of notes) {
      if (note.type === 'break') continue;
      const noteEnd = note.startBeat + note.duration;
      const isInNote = currentBeat >= note.startBeat && currentBeat < noteEnd;

      if (isInNote && !midiActiveNotes.has(note.id)) {
        // Start this note
        const osc = midiAudioCtx.createOscillator();
        const gain = midiAudioCtx.createGain();
        osc.connect(gain);
        gain.connect(midiAudioCtx.destination);
        osc.type = 'triangle';
        const freq = 440 * Math.pow(2, (note.pitch - 69) / 12);
        osc.frequency.value = freq;
        gain.gain.value = midiVolume;
        osc.start();
        midiActiveNotes.set(note.id, { osc, gain });
      } else if (!isInNote && midiActiveNotes.has(note.id)) {
        // Stop this note
        const entry = midiActiveNotes.get(note.id);
        try {
          entry.gain.gain.linearRampToValueAtTime(0, midiAudioCtx.currentTime + 0.03);
          entry.osc.stop(midiAudioCtx.currentTime + 0.04);
        } catch (e) { /* already stopped */ }
        midiActiveNotes.delete(note.id);
      }
    }
  }

  // ──── Metronome ────────────────────────────
  function ensureMetronomeCtx() {
    if (!metronomeCtx || metronomeCtx.state === 'closed') {
      metronomeCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
  }

  function playMetronomeClick(isDownbeat) {
    if (tapperOpen) return; // muted while tapper modal is open
    ensureMetronomeCtx();
    const osc = metronomeCtx.createOscillator();
    const gain = metronomeCtx.createGain();
    osc.connect(gain);
    gain.connect(metronomeCtx.destination);
    osc.type = 'sine';
    // Higher pitch for downbeat (beat 1 of measure), lower for other beats
    osc.frequency.value = isDownbeat ? 1200 : 800;
    gain.gain.setValueAtTime(0.3, metronomeCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, metronomeCtx.currentTime + 0.06);
    osc.start(metronomeCtx.currentTime);
    osc.stop(metronomeCtx.currentTime + 0.06);
  }

  function updateMetronome(currentBeat) {
    // Always click quarter notes; accenting is based on downbeat anchors.
    const clickInterval = getMetronomeClickInterval();
    const clickOffset = getMetronomeClickOffset();
    const downbeatAnchor = getMetronomeDownbeatAnchorBeat();
    const downbeatInterval = Math.max(0.0001, getMetronomeDownbeatInterval());
    const offsetBeat = currentBeat - clickOffset;
    const clickBeat = Math.floor(offsetBeat / clickInterval);
    if (clickBeat !== lastMetronomeBeat) {
      lastMetronomeBeat = clickBeat;
      const clickedBeat = clickBeat * clickInterval + clickOffset;
      const phase = (clickedBeat - downbeatAnchor) / downbeatInterval;
      const isDownbeat = Math.abs(phase - Math.round(phase)) < 1e-3;
      playMetronomeClick(isDownbeat);
    }
  }

  function toggleMetronome() {
    metronomeEnabled = !metronomeEnabled;
    if (metronomeEnabled) ensureMetronomeCtx();
    if (!metronomeEnabled) {
      metronomeToolOpen = false;
      clearMetronomePickTarget();
    }
    lastMetronomeBeat = -1;
    console.log('[Metronome]', metronomeEnabled ? 'ON' : 'OFF');
    saveEditorUiPrefs('metronome-toggle');
  }

  function stopAllMidiNotes() {
    for (const [id, entry] of midiActiveNotes) {
      try {
        entry.gain.gain.linearRampToValueAtTime(0, (midiAudioCtx?.currentTime || 0) + 0.03);
        entry.osc.stop((midiAudioCtx?.currentTime || 0) + 0.04);
      } catch (e) { /* already stopped */ }
    }
    midiActiveNotes.clear();
  }

  function toggleMidiPlayback() {
    midiPlayback = !midiPlayback;
    if (midiPlayback && isPlaying) {
      ensureMidiCtx();
    } else if (!midiPlayback) {
      stopAllMidiNotes();
    }
    console.log('[MIDI] Pitch playback:', midiPlayback);
    saveEditorUiPrefs('midi-toggle');
  }

  function toggleMuteVocal() {
    muteVocal = !muteVocal;
    if (audioEl) audioEl.volume = muteVocal ? 0 : audioVolume;
    console.log('[Audio] Mute vocal:', muteVocal);
  }

  function handleVolumeChange(e) {
    audioVolume = parseFloat(e.target.value);
    if (audioEl && !muteVocal) audioEl.volume = audioVolume;
    saveEditorUiPrefs('audio-volume');
  }

  // ──── Mic Sing-Along ────────────────────────────
  function clearMicTrackDisconnectWatchers(track = null) {
    if (!track) {
      const active = micStream?.getAudioTracks?.()[0];
      if (active) clearMicTrackDisconnectWatchers(active);
      return;
    }
    if (micTrackEndedHandler) {
      track.removeEventListener('ended', micTrackEndedHandler);
      micTrackEndedHandler = null;
    }
    if (micTrackMuteHandler) {
      track.removeEventListener('mute', micTrackMuteHandler);
      micTrackMuteHandler = null;
    }
  }

  function handleActiveMicDisconnected(reason = 'unknown') {
    if (micDisconnectHandled) return;
    micDisconnectHandled = true;
    console.warn('[Mic] Active microphone disconnected:', reason);

    // In segment-recording modal, do not close the modal on disconnect.
    // Recover to default mic so user can continue without losing modal state.
    if (segRecPhase === 'armed') {
      const shouldRecover = !micStarting;
      stopMic();
      if (shouldRecover) {
        micDeviceId = '';
        saveEditorUiPrefs('segrec-mic-disconnect-recover-default');
        micStarting = true;
        startMic()
          .then(() => {
            if (micStream) {
              showToast('Mic disconnected - switched to default input', MIC_EVENT_TOAST_MS, true);
            } else {
              showToast('Microphone disconnected', MIC_EVENT_TOAST_MS, true);
            }
          })
          .catch(() => {
            showToast('Microphone disconnected', MIC_EVENT_TOAST_MS, true);
          })
          .finally(() => {
            micStarting = false;
            draw();
          });
      } else {
        showToast('Microphone disconnected', MIC_EVENT_TOAST_MS, true);
        draw();
      }
      return;
    }

    // For sing-along mode, try to continue on default input automatically.
    if (micEnabled && segRecPhase === 'idle') {
      const shouldRecover = !micStarting;
      stopMic();
      if (shouldRecover) {
        micDeviceId = '';
        saveEditorUiPrefs('mic-disconnect-recover-default');
        micStarting = true;
        startMic()
          .then(() => {
            if (micStream) {
              micEnabled = true;
              showToast('Mic disconnected - switched to default input', MIC_EVENT_TOAST_MS, true);
            } else {
              micEnabled = false;
              showToast('Microphone disconnected', MIC_EVENT_TOAST_MS, true);
            }
          })
          .catch(() => {
            micEnabled = false;
            showToast('Microphone disconnected', MIC_EVENT_TOAST_MS, true);
          })
          .finally(() => {
            micStarting = false;
            draw();
          });
      } else {
        micEnabled = false;
        showToast('Microphone disconnected', MIC_EVENT_TOAST_MS, true);
        draw();
      }
      return;
    }

    // Stop ongoing segment capture cleanly if the input vanishes.
    if (segRecPhase === 'recording' || segRecPhase === 'preroll') {
      stopSegmentRecording();
      showToast('Recording stopped: microphone disconnected', MIC_EVENT_TOAST_MS, true);
    } else if (micEnabled) {
      showToast('Microphone disconnected', MIC_EVENT_TOAST_MS, true);
    }

    micEnabled = false;
    stopMic();
    draw();
  }

  function installMicTrackDisconnectWatchers(stream) {
    const track = stream?.getAudioTracks?.()[0];
    if (!track) return;
    clearMicTrackDisconnectWatchers(track);

    // `ended` is the most reliable unplug signal; `mute` catches some browsers.
    micTrackEndedHandler = () => handleActiveMicDisconnected('track-ended');
    micTrackMuteHandler = () => {
      if (!micEnabled && segRecPhase === 'idle') return;
      // Some drivers briefly toggle mute; defer hard stop slightly.
      setTimeout(() => {
        const live = track.readyState === 'live';
        if (!live) handleActiveMicDisconnected('track-muted-not-live');
      }, 120);
    };

    track.addEventListener('ended', micTrackEndedHandler);
    track.addEventListener('mute', micTrackMuteHandler);
  }

  async function handleMediaDeviceChange() {
    await loadMicDevices(true);
    if (!micStream) return;
    const track = micStream.getAudioTracks()[0];
    if (!track || track.readyState !== 'live') {
      handleActiveMicDisconnected('devicechange-no-live-track');
      return;
    }
    const settings = track.getSettings?.() || {};
    const activeDeviceId = settings.deviceId || micDeviceId;
    if (activeDeviceId && micDeviceId && activeDeviceId !== micDeviceId) {
      // Browser auto-switched the live stream to another input (usually default)
      // after unplugging the previously selected external mic.
      micDeviceId = activeDeviceId;
      saveEditorUiPrefs('mic-device-autoswitched');
      showToast('Mic disconnected - switched to default input', MIC_EVENT_TOAST_MS, true);
    }
    if (activeDeviceId && !micDevices.some(d => d.deviceId === activeDeviceId)) {
      handleActiveMicDisconnected('devicechange-active-missing');
    }
  }

  async function loadMicDevices(notifyOnFallback = false) {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      micDevices = devices.filter(d => d.kind === 'audioinput');
      console.log('[Mic] Found', micDevices.length, 'input devices');
      // Set dropdown to the active mic (from stream) or first device
      if (!micDeviceId && micStream) {
        const activeTrack = micStream.getAudioTracks()[0];
        if (activeTrack) {
          const settings = activeTrack.getSettings();
          micDeviceId = settings.deviceId || (micDevices[0]?.deviceId ?? '');
        }
      }
      if (!micDeviceId && micDevices.length > 0) {
        micDeviceId = micDevices[0].deviceId;
      }
      // If a persisted/selected device no longer exists (e.g. unplugged),
      // fall back to first available device to avoid exact-constraint failures.
      if (micDeviceId && !micDevices.some(d => d.deviceId === micDeviceId)) {
        console.warn('[Mic] Selected device is no longer available, falling back to default input');
        micDeviceId = micDevices[0]?.deviceId || '';
        saveEditorUiPrefs('mic-device-unavailable-fallback');
        if (notifyOnFallback && (micStream || micEnabled || segRecPhase !== 'idle')) {
          showToast('Mic disconnected - switched to default input', MIC_EVENT_TOAST_MS, true);
        }
      }
    } catch (err) {
      console.error('[Mic] Failed to enumerate devices:', err);
    }
  }

  async function startMic() {
    try {
      micDisconnectHandled = false;
      const audioConstraints = {
        echoCancellation: false, // warm-up latency + distorts pitch signal; highpass filter handles bass
        noiseSuppression: false, // same — introduces ~1s init delay and degrades pitch clarity
        autoGainControl: false,  // keep off — we want raw pitch, not normalized volume
        ...(micDeviceId ? { deviceId: { exact: micDeviceId } } : {})
      };
      const constraints = { audio: audioConstraints };
      try {
        micStream = await navigator.mediaDevices.getUserMedia(constraints);
      } catch (err) {
        const canFallbackToDefault = !!micDeviceId && (err?.name === 'OverconstrainedError' || err?.name === 'NotFoundError');
        if (!canFallbackToDefault) throw err;

        console.warn('[Mic] Selected input unavailable, retrying with default device', err);
        micDeviceId = '';
        saveEditorUiPrefs('mic-device-fallback-default');
        const fallbackConstraints = {
          audio: {
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
          }
        };
        micStream = await navigator.mediaDevices.getUserMedia(fallbackConstraints);
      }

      micAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
      micSourceNode = micAudioCtx.createMediaStreamSource(micStream);
      installMicTrackDisconnectWatchers(micStream);

      // High-pass filter at 200Hz — removes bass bleed from speakers/room rumble
      const highpass = micAudioCtx.createBiquadFilter();
      highpass.type = 'highpass';
      highpass.frequency.value = 200;
      highpass.Q.value = 0.7;

      // Gain node for mic volume control
      micGainNode = micAudioCtx.createGain();
      micGainNode.gain.value = micGain;

      micAnalyser = micAudioCtx.createAnalyser();
      micAnalyser.fftSize = 2048;
      micAnalyser.smoothingTimeConstant = 0;
      micSourceNode.connect(highpass);
      highpass.connect(micGainNode);
      micGainNode.connect(micAnalyser);

      micInputBuffer = new Float32Array(micAnalyser.fftSize);
      micDetector = PitchDetector.forFloat32Array(micAnalyser.fftSize);

      // Start MediaRecorder for voice capture
      try {
        // WKWebView (macOS/Tauri) doesn't support webm/opus — pick a supported format
        const preferredTypes = [
          'audio/webm;codecs=opus',
          'audio/webm',
          'audio/mp4',
          'audio/ogg;codecs=opus',
          'audio/ogg',
          '',
        ];
        const mimeType = preferredTypes.find(t => t === '' || MediaRecorder.isTypeSupported(t));
        micRecorder = new MediaRecorder(micStream, mimeType ? { mimeType } : {});
        micRecordedChunks = [];
        micRecorder.ondataavailable = (e) => { if (e.data.size > 0) micRecordedChunks.push(e.data); };
        micRecorder.start(1000); // collect in 1s chunks
        console.log('[Mic] MediaRecorder started, mimeType:', micRecorder.mimeType);
      } catch (recErr) {
        console.warn('[Mic] MediaRecorder not available:', recErr);
        micRecorder = null;
      }

      // Load device list after permission is granted (labels become available)
      await loadMicDevices();

      // Ensure the selected ID reflects the actual active track after fallback/default pick.
      const activeTrack = micStream?.getAudioTracks?.()[0];
      if (activeTrack) {
        const settings = activeTrack.getSettings?.() || {};
        if (settings.deviceId && settings.deviceId !== micDeviceId) {
          micDeviceId = settings.deviceId;
          saveEditorUiPrefs('mic-device-active-track');
        }
      }

      // Start mic level polling (for the level indicator)
      micLevelTimer = setInterval(() => {
        if (!micAnalyser) return;
        const buf = new Float32Array(micAnalyser.fftSize);
        micAnalyser.getFloatTimeDomainData(buf);
        let maxVal = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = Math.abs(buf[i]);
          if (v > maxVal) maxVal = v;
        }
        const nextLevel = Math.min(1, maxVal * 3); // amplify for visibility
        micLevel = nextLevel;
        micPeakLevel = Math.max(nextLevel, micPeakLevel * 0.92);
        if (maxVal >= 0.98) {
          micOversteering = true;
          if (micOversteerTimer) clearTimeout(micOversteerTimer);
          micOversteerTimer = setTimeout(() => {
            micOversteering = false;
            micOversteerTimer = null;
          }, 900);
        }
      }, 50);

      console.log('[Mic] Started — sampleRate:', micAudioCtx.sampleRate);
    } catch (err) {
      console.error('[Mic] Failed to start:', err);
      micEnabled = false;
    }
  }

  function stopMic() {
    if (micLevelTimer) { clearInterval(micLevelTimer); micLevelTimer = null; }
    micLevel = 0;
    micPeakLevel = 0;
    micOversteering = false;
    if (micOversteerTimer) { clearTimeout(micOversteerTimer); micOversteerTimer = null; }
    if (micRecorder && micRecorder.state !== 'inactive') {
      micRecorder.stop();
      console.log('[Mic] MediaRecorder stopped,', micRecordedChunks.length, 'chunks');
    }
    if (micStream) {
      clearMicTrackDisconnectWatchers(micStream.getAudioTracks()[0]);
      micStream.getTracks().forEach(t => t.stop());
      micStream = null;
    }
    if (micGainNode) { micGainNode.disconnect(); micGainNode = null; }
    if (micSourceNode) { micSourceNode.disconnect(); micSourceNode = null; }
    if (micAudioCtx && micAudioCtx.state !== 'closed') {
      micAudioCtx.close().catch(() => {});
    }
    micAudioCtx = null;
    micAnalyser = null;
    micDetector = null;
    micInputBuffer = null;
    micDisconnectHandled = false;
    console.log('[Mic] Stopped');
  }

  async function toggleMic() {
    if (micEnabled) {
      micShowTrail = true; // always show when activating
      draw();
      micStarting = true;
      await startMic();
      micStarting = false;
    } else {
      stopMic();
      draw();
    }
  }

  // ── Segment recording functions ──
  function resetSegRecLyricsPreview() {
    segRecLyricsLoading = false;
    segRecLyricsError = '';
    segRecLyricsLines = [];
    segRecLyricsHyphenated = false;
  }

  async function generateLyricsForRecordedSegment(silent = false) {
    const seg = cleanupSegments.find(s => s.id === segRecSegmentId);
    if (!$sessionId) return;
    if (!segRecApplied && !segRecBlob) return;
    if (segRecApplied && !seg) return;

    segRecLyricsLoading = true;
    segRecLyricsError = '';
    segRecLyricsLines = [];
    segRecLyricsHyphenated = false;

    const language = SUPPORTED_LANGUAGES.some(l => l.code === $lyricsData?.language)
      ? $lyricsData.language
      : 'auto';

    try {
      let previewResp;
      if (segRecApplied) {
        previewResp = await fetch(`${API_BASE}/segment-preview/${$sessionId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            start_ms: seg.startMs,
            end_ms: seg.endMs,
            language,
            model_preset: segRegenPreset,
            audio_source: 'edited',
            source_type: 'cleanup',
          }),
        });
      } else {
        const fd = new FormData();
        const ext = segRecBlob.type.includes('mp4') ? 'mp4' : segRecBlob.type.includes('ogg') ? 'ogg' : 'webm';
        fd.append('recording', segRecBlob, `segment_preview.${ext}`);
        fd.append('language', language);
        previewResp = await fetch(`${API_BASE}/segment-preview-upload/${$sessionId}`, {
          method: 'POST',
          body: fd,
        });
      }

      const previewData = await previewResp.json().catch(() => ({}));
      if (!previewResp.ok) {
        throw new Error(previewData?.detail || previewData?.message || 'Preview lyrics failed');
      }

      let lines = [];
      if (Array.isArray(previewData?.lyrics_lines) && previewData.lyrics_lines.length > 0) {
        lines = previewData.lyrics_lines.map(v => String(v));
      } else if (typeof previewData?.lyrics === 'string' && previewData.lyrics.trim()) {
        lines = previewData.lyrics.split(/\n+/).map(v => v.trim()).filter(Boolean);
      } else if (typeof previewData?.text === 'string' && previewData.text.trim()) {
        lines = previewData.text.split(/\n+/).map(v => v.trim()).filter(Boolean);
      }

      if (lines.length > 0) {
        try {
          const hyphenated = await hyphenateSegmentPreviewLines(lines, true);
          segRecLyricsLines = hyphenated;
          segRecLyricsHyphenated = true;
        } catch {
          segRecLyricsLines = lines;
          segRecLyricsHyphenated = false;
        }
      } else {
        segRecLyricsLines = ['(No lyrics recognized for this segment)'];
      }

      if (!silent) showToast('Segment lyrics generated');
    } catch (err) {
      console.error('[SegRec] Lyrics generation failed:', err);
      segRecLyricsError = String(err?.message || err || 'Lyrics generation failed');
      if (!silent) showToast('Segment lyrics failed');
    } finally {
      segRecLyricsLoading = false;
    }
  }

  async function armSegmentRecording(segId) {
    const seg = cleanupSegments.find(s => s.id === segId);
    console.log(`[SegRec] Arm segment id=${segId} | startMs=${seg?.startMs?.toFixed(0)} endMs=${seg?.endMs?.toFixed(0)} dur=${seg ? (seg.endMs - seg.startMs).toFixed(0) : '?'}ms`);
    if (!seg) return;

    // Segment recording has exclusive control: disable interactive tracing/singalong modes first.
    if (vocalTraceEnabled) {
      vocalTraceEnabled = false;
      stopVocalTrace();
    }
    if (micEnabled) {
      micEnabled = false;
      stopMic();
    }

    // Ensure mic is running
    if (!micStream) {
      micStarting = true;
      await startMic();
      await tick();
      micStarting = false;
      if (!micStream) {
        console.error('[SegRec] Mic failed to start');
        return;
      }
    }

    segRecSegmentId = segId;
    segRecApplied = false;
    segRecBlob = null;
    resetSegRecLyricsPreview();
    if (segRecObjectUrl) { URL.revokeObjectURL(segRecObjectUrl); segRecObjectUrl = null; }
    segRecChunks = [];

    // Set loop to segment range so user can practice
    const startSec = seg.startMs / 1000;
    const endSec = seg.endMs / 1000;
    loopStartBeat = timeToBeat(startSec);
    loopEndBeat = timeToBeat(endSec);
    loopEnabled = true;

    // Seek to pre-roll start
    seekToTime(Math.max(0, startSec - segRecPrerollSec));

    segRecPhase = 'armed';
    closeContextMenu();
    draw();
  }

  async function startSegmentRecording() {
    const seg = cleanupSegments.find(s => s.id === segRecSegmentId);
    if (!seg || !micStream) { console.warn('[SegRec] startSegmentRecording: missing seg or micStream', { segRecSegmentId, hasMic: !!micStream }); return; }

    const startSec = seg.startMs / 1000;
    const endSec = seg.endMs / 1000;
    const durationSec = endSec - startSec;
    console.log(`[SegRec] Start recording: preroll=${segRecPrerollSec}s region=${startSec.toFixed(2)}–${endSec.toFixed(2)}s dur=${durationSec.toFixed(2)}s`);

    segRecPhase = 'preroll';
    segRecCountdown = Math.ceil(segRecPrerollSec);

    // Keep segment loop visible while recording for clear visual boundaries.
    loopStartBeat = timeToBeat(startSec);
    loopEndBeat = timeToBeat(endSec);
    loopEnabled = true;

    // Convert audio-time waits to wall-clock waits using the active playback rate
    // so recording aligns with the segment start even at non-1x speed.
    const effectivePlaybackRate = Math.max(0.1, playbackRate || 1.0);

    // Seek to pre-roll start.
    seekToTime(Math.max(0, startSec - segRecPrerollSec));

    // If preroll would start before time 0, keep the playhead parked at 0 for
    // the clipped lead duration (c - s), then start playback.
    const clippedLeadSec = Math.max(0, segRecPrerollSec - startSec);
    const runningPrerollSec = Math.max(0, segRecPrerollSec - clippedLeadSec);
    if (clippedLeadSec > 0) {
      await new Promise(resolve => setTimeout(resolve, clippedLeadSec * 1000));
      if (segRecPhase !== 'preroll') return; // was cancelled
    }

    if (!isPlaying) togglePlayback();

    // Countdown
    segRecCountdownTimer = setInterval(() => {
      segRecCountdown--;
      if (segRecCountdown <= 0) {
        clearInterval(segRecCountdownTimer);
        segRecCountdownTimer = null;
      }
    }, 1000);

    // Start MediaRecorder when playback reaches segment start.
    await new Promise(resolve => setTimeout(resolve, (runningPrerollSec / effectivePlaybackRate) * 1000));

    if (segRecPhase !== 'preroll') return; // was cancelled

    // Start recording
    segRecChunks = [];
    const preferredTypes = ['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/ogg;codecs=opus','audio/ogg',''];
    const mimeType = preferredTypes.find(t => t === '' || MediaRecorder.isTypeSupported(t));
    segRecRecorder = new MediaRecorder(micStream, mimeType ? { mimeType } : {});
    segRecRecorder.ondataavailable = e => { if (e.data.size > 0) segRecChunks.push(e.data); };
    segRecRecorder.onstop = () => {
      segRecBlob = new Blob(segRecChunks, { type: segRecRecorder.mimeType });
      segRecObjectUrl = URL.createObjectURL(segRecBlob);
      console.log(`[SegRec] Recording done: mimeType=${segRecRecorder.mimeType} size=${segRecBlob.size} bytes objectUrl=${segRecObjectUrl}`);
      // Restore loop on the recorded segment for quick retry/listen workflows.
      loopStartBeat = timeToBeat(startSec);
      loopEndBeat = timeToBeat(endSec);
      loopEnabled = true;
      segRecPhase = 'review';
      generateLyricsForRecordedSegment(true);
      draw();
    };
    segRecRecorder.start();
    segRecPhase = 'recording';
    console.log(`[SegRec] MediaRecorder started: mimeType=${segRecRecorder.mimeType}`);
    draw();

    // Auto-stop fallback (primary stop uses playback-end clamp in updatePlayback).
    // Add a small buffer to avoid early stop if recorder start is slightly ahead
    // of the audible playback clock.
    segRecStopTimer = setTimeout(() => {
      if (segRecPhase === 'recording') stopSegmentRecording();
    }, ((durationSec / effectivePlaybackRate) * 1000) + 250);
  }

  function stopSegmentRecording() {
    console.log(`[SegRec] Stop recording (phase=${segRecPhase} recorderState=${segRecRecorder?.state})`);
    if (segRecStopTimer) { clearTimeout(segRecStopTimer); segRecStopTimer = null; }
    if (segRecCountdownTimer) { clearInterval(segRecCountdownTimer); segRecCountdownTimer = null; }
    if (segRecRecorder && segRecRecorder.state !== 'inactive') segRecRecorder.stop();
    if (isPlaying) togglePlayback();
  }

  function cancelSegmentRecording() {
    const seg = cleanupSegments.find(s => s.id === segRecSegmentId);
    stopSegmentRecording();
    // Keep loop context anchored to this segment even when closing review.
    if (seg) {
      loopStartBeat = timeToBeat(seg.startMs / 1000);
      loopEndBeat = timeToBeat(seg.endMs / 1000);
      loopEnabled = true;
    }
    if (segRecObjectUrl) { URL.revokeObjectURL(segRecObjectUrl); segRecObjectUrl = null; }
    segRecBlob = null;
    segRecChunks = [];
    segRecSegmentId = null;
    segRecApplied = false;
    resetSegRecLyricsPreview();
    segRecPhase = 'idle';
    micEnabled = false;
    stopMic();
    draw();
  }

  async function applySegmentRecording(closeAfterApply = true) {
    if (!segRecBlob || segRecSegmentId === null) return;
    const seg = cleanupSegments.find(s => s.id === segRecSegmentId);
    if (!seg) return;

    segRecUploading = true;
    try {
      const formData = new FormData();
      const ext = segRecBlob.type.includes('mp4') ? 'mp4' : segRecBlob.type.includes('ogg') ? 'ogg' : 'webm';
      formData.append('recording', segRecBlob, `segment_recording.${ext}`);
      formData.append('start_ms', String(seg.startMs));
      formData.append('end_ms', String(seg.endMs));
      formData.append('playback_rate', String(Math.max(0.1, playbackRate || 1.0)));

      const resp = await fetch(`${API_BASE}/splice-recording/${$sessionId}`, { method: 'POST', body: formData });
      if (!resp.ok) throw new Error(`Splice failed: ${resp.statusText}`);

      const spliceResult = await resp.json();
      console.log(`[SegRec] Splice OK:`, spliceResult);
      const appliedSegId = segRecSegmentId;
      segRecPatched = new Set([...segRecPatched, segRecSegmentId]);
      // Bust vocal URL cache so the editor plays the new spliced audio
      const cacheBust = `?v=${Date.now()}`;
      vocalUrl = (hasVocalsAudio ? getAudioUrl($sessionId, 'vocals') : '') + cacheBust;
      if (!originalVocalUrl) originalVocalUrl = hasVocalsAudio ? getAudioUrl($sessionId, 'vocals') : '';
      console.log(`[SegRec] Updated vocalUrl=${vocalUrl} | originalVocalUrl=${originalVocalUrl} | audioSource: ${audioSource} → edited`);
      const wasPlaying = isPlaying;
      const resumeTime = currentTimeSec || audioEl?.currentTime || 0;
      if (wasPlaying) {
        audioEl?.pause();
        isPlaying = false;
        cancelAnimationFrame(animFrame);
      }

      // Force edited source immediately and wait one microtask so the bound <audio src>
      // is updated before calling load()/seek/play.
      audioSource = 'edited';
      currentAudioUrl = vocalUrl;
      await tick();

      if (audioEl) {
        // Defensive: set src directly as well (in case DOM binding is delayed).
        editedAudioLoading = true;
        if (audioEl.src !== currentAudioUrl) audioEl.src = currentAudioUrl;
        audioEl.load();
        audioEl.onloadedmetadata = () => {
          audioEl.currentTime = Math.max(0, Math.min(resumeTime, audioEl.duration || resumeTime));
          audioEl.onloadedmetadata = null;
          if (wasPlaying) audioEl.play().catch(() => {});
        };
      }

      // Keep waveform in sync with the patched vocal immediately.
      loadWaveform(currentAudioUrl);
      cleanedAudioDirty = true;
      segRecApplied = true;
      micEnabled = false;
      // Refresh green pitch line for the newly patched segment
      if (pitchLineVisible) computeRecordedPitchLine();
      stopMic();
      handleSave(); // triggers auto-regenerate
      if (closeAfterApply) {
        segRecPhase = 'idle';
        segRecSegmentId = null;
      } else {
        segRecPhase = 'review';
      }
      // Keep modal open; if preview was generated pre-splice, users already saw it.
      if (!closeAfterApply && segRecSegmentId === appliedSegId && segRecLyricsLines.length === 0 && !segRecLyricsError) {
        await generateLyricsForRecordedSegment(true);
      }
      showToast('Recording applied');
    } catch (err) {
      console.error('[SegRec] Splice failed:', err);
      alert('Failed to splice recording: ' + err.message);
    } finally {
      segRecUploading = false;
    }
  }

  async function openSegmentAiFromRecordedPreview() {
    const seg = cleanupSegments.find(s => s.id === segRecSegmentId);
    if (!seg || !segRecBlob) return;

    // Ensure the recorded take is inserted before opening AI generation.
    if (!segRecApplied) {
      await applySegmentRecording(false);
      if (!segRecApplied) return;
    }

    // Ensure we have preview text to prefill the AI modal.
    if (!segRecLyricsLoading && (segRecLyricsLines.length === 0 || segRecLyricsError)) {
      await generateLyricsForRecordedSegment(true);
    }

    const usableLines = segRecLyricsLines
      .map(v => String(v || '').trim())
      .filter(v => v.length > 0 && !/^\(no lyrics recognized/i.test(v));

    if (usableLines.length === 0) {
      showToast('No usable lyrics recognized. Retry recording.');
      return;
    }

    const prefillLines = [...usableLines];
    const prefillHyphenated = !!segRecLyricsHyphenated;

    // Close recording review and open AI modal at the same segment range.
    cancelSegmentRecording();
    openSegmentRegenerateFromCleanup(seg);

    // Prefill AI modal state so Generate Notes is immediately available.
    segRegenPreviewLines = prefillLines;
    segRegenPreviewHyphenated = prefillHyphenated;
    segRegenPreviewError = '';
    segRegenPreviewConfidence = null;
    showToast('AI modal prefilled from recording preview');
  }

  async function changeMicDevice(e) {
    micDeviceId = e.target.value;
    saveEditorUiPrefs('mic-device-change');
    if (micEnabled || segRecPhase !== 'idle') {
      stopMic();
      await startMic();
    }
  }

  function handleMicGainInput(e) {
    const value = Number(e.target.value);
    const nextGain = Math.max(0, Math.min(2, value / 100));
    micGain = nextGain;
    if (micGainNode) micGainNode.gain.value = micGain;
  }

  function sampleMicPitch(timeSec) {
    if (!micAnalyser || !micDetector || !micInputBuffer) return;

    micAnalyser.getFloatTimeDomainData(micInputBuffer);
    const [frequency, clarity] = micDetector.findPitch(micInputBuffer, micAudioCtx.sampleRate);

    if (clarity < micClarityThreshold || frequency < 60 || frequency > 2000) {
      // Silence: decay confidence, reset sticky pitch and rolling window
      if (micPitchConfidence > 0) micPitchConfidence--;
      if (micPitchConfidence === 0) { micLastPitch = -1; micRecentPitches = []; }
      return;
    }

    // Convert frequency to MIDI pitch
    let midiPitch = Math.round(12 * Math.log2(frequency / 440) + 69);
    const rawPitch = midiPitch;

    // ── Find which note (if any) the current beat falls in ──
    const currentBeat = timeToBeat(timeSec);
    let targetNote = null;
    for (const note of notes) {
      if (note.type === 'break') continue;
      if (currentBeat >= note.startBeat && currentBeat < note.startBeat + note.duration) {
        targetNote = note;
        break;
      }
    }

    // USDX mode: only detect pitch during note regions
    if (!targetNote) {
      // Keep the rolling state alive but don't record anything
      micRecentPitches.push(midiPitch);
      if (micRecentPitches.length > 5) micRecentPitches.shift();
      return;
    }

    const targetPitch = targetNote.pitch;

    // ── Rolling median smoothing (always on — suppresses vibrato) ──
    micRecentPitches.push(midiPitch);
    if (micRecentPitches.length > 5) micRecentPitches.shift();
    const sorted = [...micRecentPitches].sort((a, b) => a - b);
    midiPitch = sorted[Math.floor(sorted.length / 2)];

    // Sticky prediction: hold stable pitch through brief jumps
    if (micLastPitch > 0) {
      const drift = Math.abs(midiPitch - micLastPitch);
      if (drift === 0) {
        micPitchConfidence = Math.min(8, micPitchConfidence + 1);
      } else if (drift <= 2 && micPitchConfidence >= 4) {
        midiPitch = micLastPitch;
        micPitchConfidence--;
      } else {
        micPitchConfidence = 1;
      }
    } else {
      micPitchConfidence = 1;
    }
    micLastPitch = midiPitch;

    // ── Octave correction toward target note ──
    while (midiPitch - targetPitch > 6) midiPitch -= 12;
    while (midiPitch - targetPitch < -6) midiPitch += 12;

    // Clamp to realistic vocal range: C2 (36) to C6 (84)
    if (midiPitch < 36) midiPitch += 12;
    if (midiPitch > 84) midiPitch -= 12;

    // ── Determine hit/miss ──
    const isHit = Math.abs(midiPitch - targetPitch) <= pitchTolerance;

    // Store in per-note hit map
    if (!micNoteHits.has(targetNote.id)) {
      micNoteHits.set(targetNote.id, []);
    }
    // Clear-ahead: remove any old hits at or beyond current beat (handles rewind/re-record)
    const hits = micNoteHits.get(targetNote.id);
    if (hits.length > 0 && hits[hits.length - 1].beat >= currentBeat) {
      // Find first hit at or beyond currentBeat and truncate
      let cutIdx = hits.length;
      for (let j = 0; j < hits.length; j++) {
        if (hits[j].beat >= currentBeat - 0.01) { cutIdx = j; break; }
      }
      hits.length = cutIdx;
    }
    hits.push({ beat: currentBeat, sungPitch: midiPitch, isHit });

    // Optional: raw trail for debugging
    if (micShowRawTrail) {
      micPitchTrail.push({ time: timeSec, pitch: midiPitch, rawPitch, clarity, frequency });
      if (micPitchTrail.length > 30000) micPitchTrail = micPitchTrail.slice(-25000);
    }
  }

  function clearMicTrail() {
    micPitchTrail = [];
    micNoteHits = new Map();
    micLastPitch = -1;
    micPitchConfidence = 0;
    micRecentPitches = [];
    draw();
  }

  // ── Vocal trace (simulated mic from vocal audio) ──

  async function startVocalTrace() {
    vocalTraceLoading = true;
    vocalTraceAbortController = new AbortController();
    const controller = vocalTraceAbortController;
    const timeoutId = setTimeout(() => controller.abort(), 30000); // 30s timeout
    try {
      const response = await fetch(vocalUrl, { signal: controller.signal });
      const arrayBuffer = await response.arrayBuffer();
      const tmpCtx = new (window.AudioContext || window.webkitAudioContext)();
      vocalTraceDecodedBuffer = await tmpCtx.decodeAudioData(arrayBuffer);
      tmpCtx.close();
      vocalTraceSampleBuf = new Float32Array(2048);
      vocalTraceDetector = PitchDetector.forFloat32Array(2048);
      // Only reset frames on first load — keep existing data when re-enabling
      if (vocalTraceFrames.length === 0) {
        vocalTraceLastPitch = -1;
        vocalTracePitchConfidence = 0;
        vocalTraceRecentPitches = [];
      }
      // console.log('[VocalTrace] Loaded, duration:', vocalTraceDecodedBuffer.duration);
    } catch (err) {
      console.error('[VocalTrace] Failed to load:', err);
      vocalTraceEnabled = false;
    } finally {
      clearTimeout(timeoutId);
      vocalTraceLoading = false;
      vocalTraceAbortController = null;
    }
  }

  function stopVocalTrace() {
    // Keep note/between data so it stays visible after unchecking
    vocalTraceDecodedBuffer = null;
    vocalTraceSampleBuf = null;
    vocalTraceDetector = null;
    vocalTraceLastPitch = -1;
    vocalTracePitchConfidence = 0;
    vocalTraceRecentPitches = [];
  }

  function clearVocalTrace() {
    vocalTraceFrames = [];
    draw();
  }

  async function toggleVocalTrace() {
    if (vocalTraceEnabled) {
      // Mutual exclusion: disable mic if active
      if (micEnabled) { micEnabled = false; stopMic(); }
      vocalTraceVisible = true; // always show when activating
      draw();
      await startVocalTrace();
    } else {
      stopVocalTrace();
      draw();
    }
  }

  function warmupVocalTrace(startTimeSec) {
    // Backward-scan warmup: step back from startTimeSec collecting up to 5 voiced
    // grid points. This seeds the rolling median with exactly the values that would
    // have been present had we played from GAP, regardless of actual start position.
    vocalTraceLastPitch = -1;
    vocalTracePitchConfidence = 0;
    vocalTraceRecentPitches = [];

    const gapSec = gapMs / 1000;
    const sampleRate = vocalTraceDecodedBuffer.sampleRate;
    const channelData = vocalTraceDecodedBuffer.getChannelData(0);
    const fftSize = 2048;

    // Start one grid step before startTimeSec (the step we're about to sample live)
    const firstGridSec = Math.ceil(startTimeSec / VOCAL_TRACE_STEP_SEC) * VOCAL_TRACE_STEP_SEC;
    const collected = []; // chronological order, oldest last (we prepend)

    // Safety: don't scan before GAP (or at all if startTimeSec is already at/before GAP)
    if (startTimeSec <= gapSec) {
      // console.log(`[VocalTrace] Warmed up: window=0 frames (start at/before GAP), startTimeSec=${startTimeSec.toFixed(3)}`);
      return;
    }

    let t = firstGridSec - VOCAL_TRACE_STEP_SEC;
    let maxSteps = 200; // safety cap: scan at most 200 steps back (~5 seconds)
    while (collected.length < 5 && t >= gapSec && maxSteps-- > 0) {
      // Snap t to the exact grid (avoid float drift)
      const gridT = Math.round(t / VOCAL_TRACE_STEP_SEC) * VOCAL_TRACE_STEP_SEC;
      const startSample = Math.floor(gridT * sampleRate);
      if (startSample >= 0 && startSample + fftSize <= channelData.length) {
        for (let j = 0; j < fftSize; j++) vocalTraceSampleBuf[j] = channelData[startSample + j];
        const [frequency, clarity] = vocalTraceDetector.findPitch(vocalTraceSampleBuf, sampleRate);
        if (clarity >= micClarityThreshold && frequency >= 60 && frequency <= 2000) {
          let midiPitch = Math.round(12 * Math.log2(frequency / 440) + 69);
          if (midiPitch < 36) midiPitch += 12;
          if (midiPitch > 84) midiPitch -= 12;
          collected.unshift(midiPitch); // prepend → oldest first
        }
      }
      t -= VOCAL_TRACE_STEP_SEC;
    }

    vocalTraceRecentPitches = collected;
    if (collected.length > 0) {
      vocalTraceLastPitch = collected[collected.length - 1];
      // Seed confidence to reflect how stable the warmup window is.
      // Count how many trailing frames match the last pitch — same as if we had
      // played forward from GAP and accumulated confidence naturally.
      let conf = 0;
      for (let i = collected.length - 1; i >= 0; i--) {
        if (collected[i] === vocalTraceLastPitch) conf++;
        else break;
      }
      vocalTracePitchConfidence = Math.min(8, conf);
    }
    // console.log(`[VocalTrace] Warmed up: window=${vocalTraceRecentPitches.length} frames, lastPitch=${vocalTraceLastPitch}, startTimeSec=${startTimeSec.toFixed(3)}`);
  }

  function sampleVocalTrace(timeSec) {
    if (!vocalTraceDecodedBuffer || !vocalTraceDetector || !vocalTraceSampleBuf) return;

    const sampleRate = vocalTraceDecodedBuffer.sampleRate;
    const channelData = vocalTraceDecodedBuffer.getChannelData(0);
    const startSample = Math.floor(timeSec * sampleRate);
    const fftSize = 2048;

    if (startSample + fftSize > channelData.length) return;
    for (let i = 0; i < fftSize; i++) vocalTraceSampleBuf[i] = channelData[startSample + i];

    const [frequency, clarity] = vocalTraceDetector.findPitch(vocalTraceSampleBuf, sampleRate);
    const currentBeatForLog = timeToBeat(timeSec);

    if (clarity < micClarityThreshold || frequency < 60 || frequency > 2000) {
      // console.log(`[VT:sample] beat=${currentBeatForLog.toFixed(3)} timeSec=${timeSec.toFixed(4)} UNVOICED clarity=${clarity.toFixed(2)} freq=${frequency.toFixed(1)}`);
      vocalTraceRecentPitches = [];
      vocalTraceLastPitch = -1;
      return;
    }

    let midiPitch = Math.round(12 * Math.log2(frequency / 440) + 69);

    // Rolling median smoothing
    vocalTraceRecentPitches.push(midiPitch);
    if (vocalTraceRecentPitches.length > 5) vocalTraceRecentPitches.shift();
    const sorted = [...vocalTraceRecentPitches].sort((a, b) => a - b);
    midiPitch = sorted[Math.floor(sorted.length / 2)];
    vocalTraceLastPitch = midiPitch;

    // Clamp to vocal range
    if (midiPitch < 36) midiPitch += 12;
    if (midiPitch > 84) midiPitch -= 12;

    const currentBeat = currentBeatForLog;
    // console.log(`[VT:frame] beat=${currentBeat.toFixed(3)} timeSec=${timeSec.toFixed(4)} rawMidi=${Math.round(12 * Math.log2(frequency / 440) + 69)} smoothed=${midiPitch} clarity=${clarity.toFixed(2)} window=[${vocalTraceRecentPitches.join(',')}] windowSize=${vocalTraceRecentPitches.length}`);

    // Always append — frames ahead of playhead are cleared on play start.
    vocalTraceFrames.push({ beat: currentBeat, pitch: midiPitch });
  }

  // ── Pitch line: offline full-song pitch analysis ──

  // Run pitch detection on an audio URL; returns array of {beat, pitch} frames
  async function _detectPitchFrames(audioUrl) {
    const response = await fetch(audioUrl);
    const arrayBuffer = await response.arrayBuffer();
    const tmpCtx = new (window.AudioContext || window.webkitAudioContext)();
    const buffer = await tmpCtx.decodeAudioData(arrayBuffer);
    tmpCtx.close();

    const sampleRate = buffer.sampleRate;
    const channelData = buffer.getChannelData(0);
    const fftSize = 2048;
    const hopSize = 512;
    const detector = PitchDetector.forFloat32Array(fftSize);
    const samples = new Float32Array(fftSize);
    const frames = [];
    const yieldEvery = 2000;

    for (let startSample = 0; startSample + fftSize <= channelData.length; startSample += hopSize) {
      for (let i = 0; i < fftSize; i++) samples[i] = channelData[startSample + i];
      const timeSec = startSample / sampleRate;
      const [frequency, clarity] = detector.findPitch(samples, sampleRate);
      if (clarity >= micClarityThreshold && frequency >= 60 && frequency <= 2000) {
        let midiPitch = Math.round(12 * Math.log2(frequency / 440) + 69);
        if (midiPitch < 36) midiPitch += 12;
        if (midiPitch > 84) midiPitch -= 12;
        frames.push({ beat: timeToBeat(timeSec), pitch: midiPitch });
      }
      if (frames.length % yieldEvery === 0 && frames.length > 0) {
        await new Promise(r => setTimeout(r, 0));
      }
    }
    return frames;
  }

  async function computePitchLine() {
    // Blue line: always the original/baseline vocal track
    const baseUrl = originalVocalUrl || vocalUrl;
    if (!baseUrl) return;
    pitchLineLoading = true;
    pitchLineSourceUrl = baseUrl;
    pitchLineFrames = [];
    try {
      console.log('[PitchLine] Analysing baseline vocal', baseUrl);
      pitchLineFrames = await _detectPitchFrames(baseUrl);
      console.log(`[PitchLine] Done: ${pitchLineFrames.length} voiced frames`);
    } catch (err) {
      console.error('[PitchLine] Failed:', err);
    } finally {
      pitchLineLoading = false;
      draw();
    }
  }

  async function computeRecordedPitchLine() {
    // Green line: spliced vocal track filtered to patched segments.
    // Always use vocalUrl (the patched vocal) — NOT the cleaned audio, which may be
    // stale/missing after a new splice and before cleaned audio is regenerated.
    if (segRecPatched.size === 0) {
      recordedPitchFrames = [];
      draw();
      return;
    }
    const sourceUrl = vocalUrl || originalVocalUrl;
    if (!sourceUrl) return;
    recordedPitchLoading = true;
    recordedPitchFrames = [];
    try {
      console.log('[PitchLine] Analysing recorded patches', sourceUrl);
      const allFrames = await _detectPitchFrames(sourceUrl);
      // Keep only frames that fall inside a patched (green) segment
      const patchedSegs = cleanupSegments.filter(s => segRecPatched.has(s.id));
      recordedPitchFrames = allFrames.filter(({ beat }) => {
        const ms = beatToTime(beat) * 1000;
        return patchedSegs.some(s => ms >= s.startMs && ms <= s.endMs);
      });
      console.log(`[PitchLine] Recorded: ${recordedPitchFrames.length} voiced frames in patched segments`);
    } catch (err) {
      console.error('[PitchLine] Recorded pitch failed:', err);
    } finally {
      recordedPitchLoading = false;
      draw();
    }
  }

  async function togglePitchLine() {
    pitchLineVisible = !pitchLineVisible;
    if (pitchLineVisible) {
      const baseUrl = originalVocalUrl || vocalUrl;
      if (pitchLineFrames.length === 0 || pitchLineSourceUrl !== baseUrl) {
        await computePitchLine();
      }
      if (segRecPatched.size > 0 && recordedPitchFrames.length === 0) {
        computeRecordedPitchLine(); // fire-and-forget, draws when done
      } else {
        draw();
      }
    } else {
      draw();
    }
  }

  async function exportMicTrail() {
    // Convert USDX note hits to exportable format
    const noteHitsData = {};
    for (const [noteId, hits] of micNoteHits) {
      const note = notes.find(n => n.id === noteId);
      if (note) {
        noteHitsData[noteId] = {
          target: { start: note.startBeat, dur: note.duration, pitch: note.pitch, text: note.syllable },
          totalSamples: hits.length,
          hitSamples: hits.filter(h => h.isHit).length,
          hitRate: hits.length > 0 ? +(hits.filter(h => h.isHit).length / hits.length * 100).toFixed(1) : 0,
          samples: hits
        };
      }
    }
    const trailData = {
      exported: new Date().toISOString(),
      settings: { mode: 'usdx', clarityThreshold: micClarityThreshold },
      song: { bpm, gapMs, noteCount: notes.filter(n => n.type !== 'break').length },
      noteHits: noteHitsData,
      rawSamples: micPitchTrail.map(s => ({
        time: +s.time.toFixed(4),
        freq: s.frequency ? +s.frequency.toFixed(1) : null,
        rawMidi: s.rawPitch,
        smoothed: s.pitch,
        clarity: +s.clarity.toFixed(3)
      }))
    };
    try {
      // Build FormData with trail JSON + optional audio recording
      const formData = new FormData();
      formData.append('trail', JSON.stringify(trailData));

      if (micRecordedChunks.length > 0) {
        const audioBlob = new Blob(micRecordedChunks, { type: 'audio/webm' });
        formData.append('audio', audioBlob, 'mic-recording.webm');
        console.log(`[Mic] Uploading audio: ${(audioBlob.size / 1024).toFixed(1)} KB`);
      }

      const resp = await fetch(`${API_BASE}/save-mic-trail/${$sessionId}`, {
        method: 'POST',
        body: formData
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const result = await resp.json();
      console.log(`[Mic] Saved ${micPitchTrail.length} samples to server: ${result.filename}`);
      if (result.analysis) {
        console.log('[Mic] Server analysis:', result.analysis);
      }
    } catch (err) {
      console.error('[Mic] Failed to save trail to server:', err);
    }
  }

  // ──── Grain Scrub ────────────────────────────
  function startScrubGrain(timeSec) {
    if (!scrubAudioBuffer) return;

    // Stop previous grain
    stopScrubGrain();

    // Create/reuse context
    if (!scrubCtx || scrubCtx.state === 'closed') {
      scrubCtx = new (window.AudioContext || window.webkitAudioContext)();
    }

    const grainDuration = 0.05; // 50ms grain
    const sampleRate = scrubAudioBuffer.sampleRate;
    const startSample = Math.floor(timeSec * sampleRate);
    const grainSamples = Math.floor(grainDuration * sampleRate);

    if (startSample < 0 || startSample >= scrubAudioBuffer.length) return;

    // Create a short buffer for the grain
    const numChannels = scrubAudioBuffer.numberOfChannels;
    const grainBuffer = scrubCtx.createBuffer(numChannels, grainSamples, sampleRate);

    for (let ch = 0; ch < numChannels; ch++) {
      const source = scrubAudioBuffer.getChannelData(ch);
      const dest = grainBuffer.getChannelData(ch);
      for (let i = 0; i < grainSamples; i++) {
        const idx = startSample + i;
        // Apply tiny fade in/out to avoid clicks
        let env = 1;
        if (i < 64) env = i / 64;
        else if (i > grainSamples - 64) env = (grainSamples - i) / 64;
        dest[i] = (idx < source.length ? source[idx] : 0) * env;
      }
    }

    scrubSource = scrubCtx.createBufferSource();
    scrubSource.buffer = grainBuffer;
    scrubSource.loop = true;

    scrubGain = scrubCtx.createGain();
    scrubGain.gain.value = 1.0;

    scrubSource.connect(scrubGain);
    scrubGain.connect(scrubCtx.destination);
    scrubSource.start();
  }

  function stopScrubGrain() {
    if (scrubSource) {
      try { scrubSource.stop(); } catch (e) {}
      scrubSource = null;
    }
    if (scrubGain) {
      scrubGain = null;
    }
  }

  // ──── Loop Region ────────────────────────────
  function toggleLoop() {
    if (loopEnabled) {
      // Disable — clear region entirely
      loopStartBeat = null;
      loopEndBeat = null;
      loopEnabled = false;
      console.log('[Loop] Disabled and cleared');
      draw();
      return;
    }
    // Enable — always create a fresh loop near the playhead
    const canvasWidth = canvasEl?.width || 800;
    const visibleStartBeat = xToBeat(0);
    const visibleEndBeat = xToBeat(canvasWidth);
    let startBeat;
    if (playbackBeat >= visibleStartBeat && playbackBeat <= visibleEndBeat) {
      // Playhead is visible — snap to full beat just before playhead
      startBeat = Math.floor(playbackBeat);
    } else {
      // Playhead not visible — use center of viewport
      startBeat = Math.floor((visibleStartBeat + visibleEndBeat) / 2);
    }
    loopStartBeat = startBeat;
    loopEndBeat = startBeat + BEATS_PER_QUARTER;
    loopEnabled = true;
    console.log(`[Loop] Created loop: beat ${loopStartBeat} → ${loopEndBeat} | ms ${(beatToTime(loopStartBeat) * 1000).toFixed(1)} → ${(beatToTime(loopEndBeat) * 1000).toFixed(1)} | sec ${beatToTime(loopStartBeat).toFixed(3)} → ${beatToTime(loopEndBeat).toFixed(3)}`);
    draw();
  }

  function clearLoop() {
    loopStartBeat = null;
    loopEndBeat = null;
    loopEnabled = false;
    console.log('[Loop] Cleared');
    draw();
  }

  // Resize canvas to fit container
  function resizeCanvas() {
    if (!canvasEl) return;
    canvasEl.width = canvasEl.parentElement.clientWidth;
    canvasW = canvasEl.width;
    canvasEl.height = viewHeight;
    //console.log(`[Resize] Canvas ${canvasEl.width}x${canvasEl.height}`);
    draw();
  }

  // Load waveform peaks from audio URL via Web Audio API
  async function loadWaveform(url) {
    console.log('[Waveform] Loading from', url);
    const token = ++waveformLoadToken;
    waveformLoading = true;
    try {
      const resp = await fetch(url);
      const arrayBuffer = await resp.arrayBuffer();
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const decoded = await audioCtx.decodeAudioData(arrayBuffer);
      audioCtx.close();

      // Store decoded buffer for grain scrubbing
      scrubAudioBuffer = decoded;

      // Downsample to 750 peaks per second for smooth waveform at all zoom levels.
      // Use floating-point sample boundaries to avoid accumulated rounding drift.
      const rawData = decoded.getChannelData(0);
      const peaksPerSec = 750;
      const totalPeaks = Math.ceil(decoded.duration * peaksPerSec);
      const totalSamples = rawData.length;
      const peaks = new Float32Array(totalPeaks);

      for (let i = 0; i < totalPeaks; i++) {
        let max = 0;
        const start = Math.floor(i * totalSamples / totalPeaks);
        const end = Math.min(Math.floor((i + 1) * totalSamples / totalPeaks), totalSamples);
        for (let j = start; j < end; j++) {
          const abs = Math.abs(rawData[j]);
          if (abs > max) max = abs;
        }
        peaks[i] = max;
      }

      waveformPeaks = peaks;
      waveformDuration = decoded.duration;
      console.log(`[Waveform] Loaded ${totalPeaks} peaks for ${decoded.duration.toFixed(1)}s audio`);
      draw();
    } catch (err) {
      console.warn('[Waveform] Failed to load:', err);
      waveformPeaks = [];
    } finally {
      if (token === waveformLoadToken) waveformLoading = false;
    }
  }

  // ──── Lifecycle ──────────────────────────────
  async function loadData() {
    const session = $sessionId;
    console.log('[Step4] loadData, session:', session, 'hasResult:', !!$generationResult);
    if (!$generationResult) return;

    // Skip if already loaded for this session (prevents reactive re-triggers)
    if (dataLoadedSession === session) return;
    dataLoadedSession = session;

    try {
      const data = await getEditorData($sessionId);
      console.log('[Step4] Editor data:', { bpm: data.bpm, gap: data.gap_ms, duration: data.audio_duration, contentLen: data.ultrastar_content?.length });
      
      notes = parseUltrastar(data.ultrastar_content);
      // Stamp timeMs on breaks using loaded BPM/GAP so resyncAllToGrid can recalc them later
      notes = notes.map(n => {
        if (n.type !== 'break') return n;
        const startMs = Math.max(0, data.gap_ms + n.startBeat * 15000 / data.bpm);
        const endMs = n.endBeat != null ? Math.max(0, data.gap_ms + n.endBeat * 15000 / data.bpm) : null;
        return { ...n, timeMs: startMs, endTimeMs: endMs };
      });
      console.log('[Step4] Parsed', notes.length, 'notes/breaks');

      // Parse extra headers from ultrastar content
      const standardKeys = new Set(['TITLE', 'ARTIST', 'BPM', 'GAP', 'DOWNBEATOFFSET', 'METRONOMEANCHOR', 'METRONOMEIG', 'METRONOMESPEED']);
      extraHeaders = [];
      let foundDownbeatOffset = false;
      let loadedMetronomeAnchorMs = null;
      let loadedMetronomeIg = null;
      let loadedMetronomeSpeed = null;
      for (const line of (data.ultrastar_content || '').split('\n')) {
        const m = line.match(/^#([\w]+):(.*)/);
        if (m) {
          const key = m[1].toUpperCase();
          if (key === 'DOWNBEATOFFSET') {
            downbeatOffsetMs = parseFloat(m[2]) || 0;
            foundDownbeatOffset = true;
          } else if (key === 'METRONOMEANCHOR') {
            loadedMetronomeAnchorMs = parseFloat(m[2]) || null;
          } else if (key === 'METRONOMEIG') {
            loadedMetronomeIg = m[2].trim();
          } else if (key === 'METRONOMESPEED') {
            loadedMetronomeSpeed = parseFloat(m[2]) || 1;
          } else if (!standardKeys.has(key)) {
            extraHeaders.push({ key: m[1], value: m[2] });
          }
        }
      }
      if (foundDownbeatOffset) {
        console.log(`%c🎵 [Downbeat] Found in header: ${downbeatOffsetMs}ms`, 'color: #ff69b4; font-weight: bold');
      } else {
        console.log(`%c🎵 [Downbeat] No value in header`, 'color: #ff69b4; font-weight: bold');
      }
      if (extraHeaders.length > 0) {
        console.log('[Step4] Extra headers:', extraHeaders.map(h => h.key).join(', '));
      }

      bpm = data.bpm;
      gapMs = data.gap_ms;

      // Restore metronome tool state from headers (needs bpm + gapMs resolved first)
      console.log(`%c[MetronomeTool] Load — raw header values: ANCHOR=${loadedMetronomeAnchorMs} IG=${loadedMetronomeIg} SPEED=${loadedMetronomeSpeed}`, 'color:#7dd3fc');
      if (loadedMetronomeAnchorMs !== null) {
        if (loadedMetronomeIg) {
          const parts = loadedMetronomeIg.split('/');
          if (parts.length === 2) {
            metronomeSigNumerator = parseInt(parts[0]) || 4;
            metronomeSigDenominator = parseInt(parts[1]) || 4;
          }
        }
        metronomeSpeedFactor = loadedMetronomeSpeed !== null ? loadedMetronomeSpeed : 1;
        metronomeDownbeat1Beat = (loadedMetronomeAnchorMs - data.gap_ms) * data.bpm / 15000;
        metronomeDownbeat2Beat = null;
        console.log(`%c[MetronomeTool] Restoring: anchorMs=${loadedMetronomeAnchorMs} → beat=${metronomeDownbeat1Beat?.toFixed(3)} sig=${metronomeSigNumerator}/${metronomeSigDenominator} speed=${metronomeSpeedFactor} bpm=${data.bpm} gap=${data.gap_ms}`, 'color:#7dd3fc;font-weight:bold');
        recalcMetronomeFromControls('load');
        console.log(`%c[MetronomeTool] After recalc: anchorBeat=${metronomeManualDownbeatAnchorBeat?.toFixed(3)} interval=${metronomeManualDownbeatInterval?.toFixed(3)} beatUnit=${metronomeManualBeatUnitInterval?.toFixed(3)}`, 'color:#7dd3fc');
      } else {
        console.log('[MetronomeTool] No METRONOMEANCHOR header found — clearing metronome tool state');
        metronomeDownbeat1Beat = null;
        metronomeManualDownbeatAnchorBeat = null;
        metronomeManualDownbeatInterval = null;
        metronomeManualBeatUnitInterval = null;
        metronomeSpeedFactor = 1;
        metronomeSigNumerator = 4;
        metronomeSigDenominator = 4;
      }

      // Compute first downbeat once on load
      if (!foundDownbeatOffset) {
        downbeatFromHeader = false;
        const beatsPerMeasure = BEATS_PER_QUARTER * 4;
        const beatAtZero = (-data.gap_ms * data.bpm) / 15000;
        const firstBeat = Math.ceil(beatAtZero / beatsPerMeasure) * beatsPerMeasure;
        downbeatOffsetMs = data.gap_ms + (firstBeat * 15000 / data.bpm);
      } else {
        downbeatFromHeader = true;
      }

      // Store initial values and raw timings for BPM re-quantization
      initialBpm = data.bpm;
      previousBpm = data.bpm;
      initialGap = data.gap_ms;
      bpmChanged = false;
      rawTimings = data.syllable_timings || [];

      cleanupSegmentIdCounter = 1;
      setCleanupSegmentsFromApi(data.cleanup_segments || []);

      // Extract pitches from parsed notes (non-break, in order) for re-quantization
      pitchMap = notes.filter(n => n.type !== 'break').map(n => n.pitch);
      console.log('[Step4] Stored', rawTimings.length, 'raw timings,', pitchMap.length, 'pitches for re-quantization');
      audioDuration = data.audio_duration;

      // Restore save state
      editCount = data.edit_count || 0;
      lastSaveTime = data.last_saved ? new Date(data.last_saved * 1000) : null;
      cleanedAudioAvailable = !!data.cleaned_audio_available;
      hasUnsavedChanges = false;
      hasVocalsAudio = data.has_vocals !== false;
      hasOriginalAudio = data.has_original !== false;
      console.log(`[Step4] loadData: has_vocals=${data.has_vocals} has_original=${data.has_original} has_vocal_splice=${data.has_vocal_splice} has_original_demucs=${data.has_original_demucs}`);
      vocalUrl = hasVocalsAudio ? getAudioUrl($sessionId, 'vocals') : '';
      // If splices exist, original demucs vocal is served at /demucs; else same as vocals
      originalVocalUrl = (hasVocalsAudio && data.has_original_demucs)
        ? getAudioUrl($sessionId, 'demucs')
        : vocalUrl;
      if (data.has_vocal_splice && cleanupSegments.length > 0 && segRecPatched.size === 0 && !cleanupSegmentsHavePatchedMetadata) {
        // Legacy sessions may not contain per-segment patched flags.
        // Favor preserving recorded audio by treating existing segments as patched.
        segRecPatched = new Set(cleanupSegments.map(s => s.id));
      }
      originalUrl = hasOriginalAudio ? getAudioUrl($sessionId, 'original') : '';
      console.log(`[Step4] URLs: vocalUrl=${vocalUrl} | originalVocalUrl=${originalVocalUrl} | originalUrl=${originalUrl}`);
      const defaultSource = hasVocalsAudio ? (data.has_vocal_splice ? 'edited' : 'vocals') : (hasOriginalAudio ? 'original' : 'original');
      console.log(`[Step4] segRecPatched.size=${segRecPatched.size} | default audioSource=${defaultSource}`);
      const uiPrefs = restoreEditorUiPrefs();
      if (uiPrefs) {
        if (typeof uiPrefs.scrollMode === 'boolean') scrollMode = uiPrefs.scrollMode;
        if (typeof uiPrefs.playbackRate === 'number' && [0.25, 0.5, 0.75, 1].includes(uiPrefs.playbackRate)) {
          playbackRate = uiPrefs.playbackRate;
        }
        if (typeof uiPrefs.audioVolume === 'number' && Number.isFinite(uiPrefs.audioVolume)) {
          audioVolume = Math.max(0, Math.min(1, uiPrefs.audioVolume));
        }
        if (typeof uiPrefs.midiPlayback === 'boolean') midiPlayback = uiPrefs.midiPlayback;
        if (typeof uiPrefs.metronomeEnabled === 'boolean') metronomeEnabled = uiPrefs.metronomeEnabled;
        if (typeof uiPrefs.waveformHeight === 'number' && Number.isFinite(uiPrefs.waveformHeight)) {
          waveformHeight = Math.max(40, Math.min(240, uiPrefs.waveformHeight));
        }
        if (typeof uiPrefs.micDeviceId === 'string') {
          micDeviceId = uiPrefs.micDeviceId;
        }
        if (typeof uiPrefs.vibratoModalX === 'number' && Number.isFinite(uiPrefs.vibratoModalX)) {
          vibratoModalX = Math.max(0, Math.min(window.innerWidth - 410, uiPrefs.vibratoModalX));
        }
        if (typeof uiPrefs.vibratoModalY === 'number' && Number.isFinite(uiPrefs.vibratoModalY)) {
          vibratoModalY = Math.max(0, Math.min(window.innerHeight - 300, uiPrefs.vibratoModalY));
        }
      }
      const preferredSource = uiPrefs?.audioSource || defaultSource;
      audioSource = resolvePreferredAudioSource(preferredSource);
      if (preferredSource !== audioSource) {
        console.log(`[Step4] Audio source fallback: preferred=${preferredSource} -> resolved=${audioSource}`);
      }
      // Set the reactive audio URL driving the <audio> element
      const editedUrl = getEditedAudioUrl();
      currentAudioUrl = audioSource === 'original' ? originalUrl : audioSource === 'edited' ? editedUrl : originalVocalUrl || vocalUrl;
      console.log('[Step4] Audio: vocals=' + hasVocalsAudio + ', original=' + hasOriginalAudio + ', source=' + audioSource + ', currentAudioUrl=' + currentAudioUrl);
      // Explicitly reload the audio element — Svelte reactive src binding updates the
      // attribute but the browser does not re-fetch/re-buffer unless load() is called.
      await tick();
      if (audioEl && currentAudioUrl) {
        editedAudioLoading = audioSource === 'edited';
        if (audioEl.src !== currentAudioUrl) audioEl.src = currentAudioUrl;
        audioEl.playbackRate = playbackRate;
        audioEl.preservesPitch = true;
        audioEl.load();
      }
      saveEditorUiPrefs('loadData');
      computeTotalBeats();

      // Position playhead and scroll at GAP (song start) — unless we have a saved scroll position
      const gapSec = gapMs / 1000;
      currentTimeSec = gapSec;
      playbackBeat = 0; // beat 0 = GAP position
      const canvasWidth = canvasEl?.width || 800;
      const savedScroll = session ? localStorage.getItem(`editor_scroll_${session}`) : null;
      if (savedScroll) {
        try {
          const { sx, z } = JSON.parse(savedScroll);
          if (typeof z === 'number') zoom = z;
          if (typeof sx === 'number') scrollX = sx;
          console.log(`[Step4] Restored scroll: scrollX=${sx} zoom=${z}`);
        } catch { /* ignore */ }
      } else {
        scrollX = Math.max(getMinBeat() * zoom, (playbackBeat * zoom) - canvasWidth * 0.1);
      }

      // Restore session notes and flags
      loadSessionNotes();
      loadFlags();

      // Load waveform for the active audio source
      if (currentAudioUrl) {
        loadWaveform(currentAudioUrl);
      }

      updatePitchRange();
      console.log('[Step4] Pitch range:', minPitch, '-', maxPitch);
      draw();
    } catch (err) {
      console.error('[Step4] loadData error:', err);
      errorMessage.set(err.message);
    }
  }

  function scrollViewportToTop() {
    // Ensure editor opens from the top of the page, not at prior scroll offset.
    if (typeof window !== 'undefined') {
      window.scrollTo({ top: 0, behavior: 'auto' });
    }
  }

  $: if ($currentStep === 4) {
    scrollViewportToTop();
  }

  onMount(() => {
    console.log('[Step4] onMount');
    scrollViewportToTop();
    if (canvasEl) {
      ctx = canvasEl.getContext('2d');
      resizeCanvas();
      loadData();
    }
    window.addEventListener('keydown', handleKeydown);
    window.addEventListener('keydown', handleKeydownSave);
    window.addEventListener('keydown', handleTapperKeydown);
    window.addEventListener('resize', resizeCanvas);
    window.addEventListener('click', handleGlobalClick);
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    window.addEventListener('blur', handleMouseUp);
    if (navigator.mediaDevices?.addEventListener) {
      mediaDeviceChangeHandler = () => { handleMediaDeviceChange(); };
      navigator.mediaDevices.addEventListener('devicechange', mediaDeviceChangeHandler);
    }
    autosaveInterval = setInterval(() => { if (hasUnsavedChanges) handleSave(); }, 10000);
  });

  onDestroy(() => {
    console.log(`%c[Step4] onDestroy — hasUnsavedChanges=${hasUnsavedChanges} metronomeAnchor=${metronomeManualDownbeatAnchorBeat} sig=${metronomeSigNumerator}/${metronomeSigDenominator} speed=${metronomeSpeedFactor}`, 'color:#ffd700;font-weight:bold');
    if (hasUnsavedChanges) {
      console.log('[Step4] onDestroy — triggering save');
      handleSave();
    } else {
      console.log('[Step4] onDestroy — nothing to save');
    }
    // Persist scroll position for this session
    if ($sessionId) {
      localStorage.setItem(`editor_scroll_${$sessionId}`, JSON.stringify({ sx: scrollX, z: zoom }));
    }
    saveEditorUiPrefs('destroy');
    saveSessionNotes();
    saveFlags();
    clearCleanupKeyboardSaveTimer();
    if (autosaveInterval) clearInterval(autosaveInterval);
    cancelAnimationFrame(animFrame);
    window.removeEventListener('keydown', handleKeydown);
    window.removeEventListener('keydown', handleKeydownSave);
    window.removeEventListener('keydown', handleTapperKeydown);
    window.removeEventListener('resize', resizeCanvas);
    window.removeEventListener('click', handleGlobalClick);
    window.removeEventListener('mousemove', handleMouseMove);
    window.removeEventListener('mouseup', handleMouseUp);
    window.removeEventListener('blur', handleMouseUp);
    if (mediaDeviceChangeHandler && navigator.mediaDevices?.removeEventListener) {
      navigator.mediaDevices.removeEventListener('devicechange', mediaDeviceChangeHandler);
      mediaDeviceChangeHandler = null;
    }
    window.removeEventListener('mousemove', segRegenModalMouseMove);
    window.removeEventListener('mouseup', segRegenModalMouseUp);
    stopMic();
  });

  // ── Recording modal drag ──
  let segRecModalX = 10;
  let segRecModalY = 10;
  let segRecModalDragging = false;
  let segRecModalDragOffsetX = 0;
  let segRecModalDragOffsetY = 0;

  function segRecModalMouseDown(e) {
    if (e.button !== 0) return;
    if (!(e.target instanceof Element) || !e.target.closest('.seg-rec-modal-title')) return;
    segRecModalDragging = true;
    segRecModalDragOffsetX = e.clientX - segRecModalX;
    segRecModalDragOffsetY = e.clientY - segRecModalY;
    window.addEventListener('mousemove', segRecModalMouseMove);
    window.addEventListener('mouseup', segRecModalMouseUp);
    e.preventDefault();
  }

  function segRecModalMouseMove(e) {
    if (!segRecModalDragging) return;
    segRecModalX = Math.max(0, Math.min(window.innerWidth - 270, e.clientX - segRecModalDragOffsetX));
    segRecModalY = Math.max(0, Math.min(window.innerHeight - 200, e.clientY - segRecModalDragOffsetY));
  }

  function segRecModalMouseUp() {
    segRecModalDragging = false;
    window.removeEventListener('mousemove', segRecModalMouseMove);
    window.removeEventListener('mouseup', segRecModalMouseUp);
  }

  // Reload when we enter this step (one-shot per session)
  $: if ($generationResult && canvasEl && $sessionId && dataLoadedSession !== $sessionId) {
    loadData();
  }

  // Resize canvas whenever viewHeight changes (e.g. waveform height slider)
  $: if (canvasEl && viewHeight) { resizeCanvas(); draw(); }
</script>

<div class="step-content">
  {#if segRecPhase !== 'idle'}
    {@const seg = cleanupSegments.find(s => s.id === segRecSegmentId)}
    <div class="seg-rec-modal" class:seg-rec-recording={segRecPhase === 'recording'}
      style="left:{segRecModalX}px;top:{segRecModalY}px"
      on:mousedown={segRecModalMouseDown}>
      <div class="seg-rec-modal-title">
        {#if segRecPhase === 'armed'}
          🎙 Record over segment
        {:else if segRecPhase === 'preroll'}
          ⏱ Get ready…
        {:else if segRecPhase === 'recording'}
          🔴 Recording
        {:else if segRecPhase === 'review'}
          ✅ Review
        {/if}
      </div>
      <div class="seg-rec-modal-info">
        {seg ? `${(seg.startMs/1000).toFixed(1)}s – ${(seg.endMs/1000).toFixed(1)}s  (${((seg.endMs - seg.startMs)/1000).toFixed(1)}s)` : ''}
      </div>
      {#if segRecPhase === 'armed' || segRecPhase === 'preroll' || segRecPhase === 'recording'}
        <div class="seg-rec-mic-panel">
          <div class="seg-rec-mic-row">
            <span class="seg-rec-mic-label">Mic</span>
            <select class="mic-select seg-rec-mic-select" value={micDeviceId} on:change={changeMicDevice} title="Select recording microphone">
              {#if micDevices.length === 0}
                <option value="">Default microphone</option>
              {:else}
                {#each micDevices as device}
                  <option value={device.deviceId}>{device.label || `Mic ${micDevices.indexOf(device) + 1}`}</option>
                {/each}
              {/if}
            </select>
          </div>
          <div class="seg-rec-mic-row">
            <span class="seg-rec-mic-label">Input</span>
            <div class="seg-rec-mic-meter" title="Mic input level">
              <div class="seg-rec-mic-meter-fill" style="width:{Math.round(Math.min(1, micPeakLevel) * 100)}%" class:seg-rec-mic-meter-warm={micPeakLevel > 0.65} class:seg-rec-mic-meter-hot={micPeakLevel > 0.85 || micOversteering}></div>
              <div class="seg-rec-mic-meter-live" style="left:{Math.round(Math.min(1, micLevel) * 100)}%"></div>
            </div>
            <span class="seg-rec-mic-status" class:seg-rec-mic-status-hot={micOversteering}>{micOversteering ? 'CLIP' : 'OK'}</span>
          </div>
          <div class="seg-rec-mic-row">
            <span class="seg-rec-mic-label">Gain</span>
            <input type="range" class="mic-gain-slider seg-rec-mic-slider" min="0" max="200" step="1"
                   value={Math.round(micGain * 100)}
                   on:input={handleMicGainInput}
                   title={`Mic gain: ${Math.round(micGain * 100)}%`} />
            <span class="seg-rec-mic-gain-readout">{Math.round(micGain * 100)}%</span>
          </div>
          {#if micOversteering}
            <div class="seg-rec-mic-warning">Input is clipping. Reduce gain or increase distance from the mic.</div>
          {/if}
        </div>
      {/if}
      {#if segRecPhase === 'preroll'}
        <div class="seg-rec-countdown-overlay">{segRecCountdown}</div>
      {/if}
      {#if segRecPhase === 'review' && segRecObjectUrl}
        <audio controls src={segRecObjectUrl} style="width:100%;margin:6px 0;"></audio>
      {/if}
      {#if segRecPhase === 'review'}
        <div class="seg-rec-lyrics-panel" style="margin-top:6px; border:1px solid var(--border-weak, #3a3f52); border-radius:8px; padding:8px; background:rgba(17,21,30,0.45);">
          {#if !segRecApplied && !segRecLyricsLoading && segRecLyricsLines.length === 0 && !segRecLyricsError}
            <div style="margin-top:6px;font-size:0.82rem;opacity:0.75;">Recognizing lyrics from recording preview...</div>
          {:else if segRecLyricsLoading}
            <div style="margin-top:6px;font-size:0.82rem;opacity:0.9;display:flex;align-items:center;gap:8px;">
              <span class="loading-spinner"></span>
              <span>Recognizing and hyphenating lyrics...</span>
            </div>
          {:else if segRecLyricsError}
            <div style="margin-top:6px;font-size:0.82rem;color:#ff9b9b;">{segRecLyricsError}</div>
          {:else if segRecLyricsLines.length > 0}
            <div style="margin-top:6px;max-height:120px;overflow:auto;font-size:0.85rem;line-height:1.35;">
              {#each segRecLyricsLines as line}
                <div>{line}</div>
              {/each}
            </div>
          {:else}
            <div style="margin-top:6px;font-size:0.82rem;opacity:0.75;">No lyrics generated yet.</div>
          {/if}
        </div>
      {/if}
      <div class="seg-rec-modal-actions">
        {#if segRecPhase === 'armed'}
          <button class="tool-btn sm seg-rec-primary-action" on:click={startSegmentRecording}>▶ Start</button>
          <button class="tool-btn sm" on:click={cancelSegmentRecording}>✕ Cancel</button>
        {:else if segRecPhase === 'recording'}
          <button class="tool-btn sm" on:click={stopSegmentRecording}>⏹ Stop</button>
        {:else if segRecPhase === 'review'}
          <button class="tool-btn sm seg-rec-primary-action" on:click={applySegmentRecording} disabled={segRecUploading || !segRecBlob}>
            {segRecUploading ? '⏳ Splicing…' : '✓ Use this'}
          </button>
          <button class="tool-btn sm" on:click={openSegmentAiFromRecordedPreview} disabled={segRecUploading || segRecLyricsLoading || !segRecBlob}>
            🤖 Generate AI
          </button>
          <button class="tool-btn sm" on:click={() => armSegmentRecording(segRecSegmentId)} disabled={segRecUploading}>↺ Retry</button>
          <button class="tool-btn sm" on:click={cancelSegmentRecording}>✕ Discard</button>
        {/if}
      </div>
      {#if segRecPhase === 'armed'}
        <div class="seg-rec-hint">Loop is set — practice, then hit Start when ready.</div>
      {/if}
    </div>
  {/if}
  {#if isRegeneratingCleaned}
    <div class="loading-modal-overlay" style="z-index:9999">
      <div class="loading-modal">
        <span class="loading-spinner"></span>
        <span class="loading-label">Regenerating cleaned audio…</span>
      </div>
    </div>
  {/if}
  {#if uiBusy}
    <div class="editor-busy-shield" aria-live="polite" aria-busy="true">
      <div class="loading-modal">
        <span class="loading-spinner"></span>
        <span class="loading-label">{isRegeneratingCleaned ? 'Processing cleaned audio…' : isSaving ? 'Saving changes…' : 'Processing…'}</span>
      </div>
    </div>
  {/if}
  <div class="toolbar">
    

    <!-- <div class="zoom-controls">
      <button class="tool-btn" on:click={() => { zoom = Math.max(0.5, zoom - 1); console.log('[UI] zoom-', zoom); draw(); }}>−</button>
      <span class="zoom-label">Zoom: {zoom.toFixed(1)}x</span>
      <button class="tool-btn" on:click={() => { zoom = Math.min(100, zoom + 1); console.log('[UI] zoom+', zoom); draw(); }}>+</button>
    </div> -->

    <div class="mode-controls">
      <!-- <label>
        <input type="checkbox" bind:checked={scrollMode} on:change={() => console.log('[UI] scrollMode', scrollMode)} />
        Scroll
      </label> -->
      <!-- <label>
        <input type="checkbox" bind:checked={showWaveform} on:change={() => { console.log('[UI] waveform', showWaveform); draw(); }} />
        Wave
      </label> -->
      
      <!-- {#if metronomeEnabled}
        <button class="tool-btn sm" class:active={metronomeOffset === 0} on:click={() => { metronomeOffset = 0; lastMetronomeBeat = -1; }} title="On beat">♩</button>
        <button class="tool-btn sm" class:active={metronomeOffset === 4} on:click={() => { metronomeOffset = 4; lastMetronomeBeat = -1; }} title="Half beat offset (8th note)">♩½</button>
      {/if} -->
      

      
      
    </div>

    

    
    <div class="save-controls">
      <!-- <button class="tool-btn save-btn" on:click={handleSave} disabled={isSaving} title="Save">
        {isSaving ? '⏳' : '💾'} Save
      </button>
      <button class="tool-btn" on:click={handleReload} title="Reload from last save">
        🔄 Reload
      </button> -->
      
      <!-- {#if hasUnsavedChanges}
        <span class="unsaved-indicator">● unsaved</span>
      {:else if lastSaveTime}
        <span class="saved-indicator">✓ saved {lastSaveTime.toLocaleTimeString()}</span>
      {/if} -->
    </div>

    <!-- <div class="info">
      {#if selectedNote !== null}
        {@const note = notes.find(n => n.id === selectedNote)}
        {#if note && note.type !== 'break'}
          <span class="note-info">
            {note.syllable.trim()} | Beat {note.startBeat} | Dur {note.duration} | {noteName(note.pitch)}
          </span>
        {/if}
      {/if}
    </div> -->
    <div class="toolbar-toolset-wrapper">
      <div id="mic-controls-wrapper">
        <button class="tool-btn" class:active={micEnabled} disabled={uiModalGuardActive} class:disabled-audio={uiModalGuardActive} on:click={() => {
          if (uiModalGuardActive) return;
          micEnabled = !micEnabled;
          if (micEnabled && vocalTraceEnabled) { vocalTraceEnabled = false; stopVocalTrace(); }
          toggleMic();
        }} title={uiModalGuardActive ? 'Disabled while modal is active' : 'Microphone sing-along (M)'}>
          Mic <span class="mic-icon-wrap" class:mic-off={!micEnabled}>🎙️</span>
        </button>
      {#if micEnabled}
        <div class="mic-level" title="Mic input level — tap the mic to check">
          <div class="mic-level-bar" style="height:{Math.round(micLevel * 100)}%"
               class:mic-level-hot={micLevel > 0.8}
               class:mic-level-warm={micLevel > 0.3 && micLevel <= 0.8}></div>
        </div>
         <input type="range" class="mic-gain-slider" min="0" max="200" step="1"
           value={Math.round(micGain * 100)}
           on:input={handleMicGainInput}
               title="Mic volume: {Math.round(micGain * 100)}%" />
        <select class="mic-select" bind:value={pitchTolerance} on:change={() => draw()} title="Pitch tolerance (difficulty)">
          <option value={1}>Hard (±1)</option>
          <option value={2}>Medium (±2)</option>
          <option value={3}>Easy (±3)</option>
        </select>
        {#if micShowRawTrail && micPitchTrail.length > 0}
          <button class="tool-btn sm" on:click={exportMicTrail} title="Export mic trail as JSON">📋 Export</button>
        {/if}
        {#if micDevices.length > 1}
          <select class="mic-select" value={micDeviceId} on:change={changeMicDevice} title="Select microphone">
            {#each micDevices as device}
              <option value={device.deviceId}>{device.label || `Mic ${micDevices.indexOf(device) + 1}`}</option>
            {/each}
          </select>
        {/if}
      {/if}
      {#if micNoteHits.size > 0 || micPitchTrail.length > 0}
        {#if micShowTrail}
          <button class="tool-btn sm active" on:click={() => { micShowTrail = false; draw(); }} title="Hide sung blocks"><span class="mic-icon-wrap">👁</span></button>
        {:else}
          <button class="tool-btn sm" on:click={() => { micShowTrail = true; draw(); }} title="Show sung blocks"><span class="mic-icon-wrap mic-off">👁</span></button>
        {/if}
        <button class="tool-btn sm" on:click={clearMicTrail} title="Clear sung blocks">🗑</button>
      {/if}

      </div>
      <div id="vocal_trace_outer_wrapper">
        <div id="vocal_trace-controls-wrapper">
          <button class="tool-btn" class:active={vocalTraceEnabled} class:disabled-audio={!hasVocalsAudio || uiModalGuardActive} on:click={(e) => {
            if (uiModalGuardActive) return;
            if (!hasVocalsAudio) { handleMissingAudio('vocals'); return; }
            vocalTraceEnabled = !vocalTraceEnabled;
            if (vocalTraceEnabled && micEnabled) { micEnabled = false; stopMic(); }
            toggleVocalTrace();
            e.currentTarget.blur();
          }} title={uiModalGuardActive ? 'Disabled while modal is active' : hasVocalsAudio ? 'Vocal trace — plays the vocal audio through pitch detection. Draw pink pitch lines (V)' : 'No vocals — go to Step 1 to extract or upload'}>
            Vocal <span class="mic-icon-wrap" class:mic-off={!vocalTraceEnabled}>🎙️</span>
          </button>
          {#if vocalTraceLoading}
            <div class="loading-modal-overlay">
              <div class="loading-modal">
                <span class="loading-spinner"></span>
                <span class="loading-label">Loading vocal trace…</span>
                <button class="tool-btn sm" on:click={() => {
                  if (vocalTraceAbortController) vocalTraceAbortController.abort();
                  vocalTraceEnabled = false;
                  vocalTraceLoading = false;
                }} title="Cancel">Cancel</button>
              </div>
            </div>
          {/if}
          {#if vocalTraceFrames.length > 0}
            {#if vocalTraceVisible}
              <button class="tool-btn sm active" on:click={() => { vocalTraceVisible = false; draw(); }} title="Hide vocal trace"><span class="mic-icon-wrap">👁</span></button>
            {:else}
              <button class="tool-btn sm" on:click={() => { vocalTraceVisible = true; draw(); }} title="Show vocal trace"><span class="mic-icon-wrap mic-off">👁</span></button>
            {/if}
            <button class="tool-btn sm" on:click={clearVocalTrace} title="Clear vocal trace">🗑</button>
          {/if}
        </div>
        <div id="pitch-line-controls-wrapper">
          <button class="tool-btn" class:active={pitchLineVisible} class:disabled-audio={!hasVocalsAudio || uiModalGuardActive} disabled={uiModalGuardActive}
            on:click={() => { if (uiModalGuardActive) return; if (!hasVocalsAudio) { handleMissingAudio('vocals'); return; } togglePitchLine(); }}
            title={uiModalGuardActive ? 'Disabled while modal is active' : hasVocalsAudio ? 'Pitch line — precompute pitch from selected Vocals/Edited source (cyan dots)' : 'No vocals — go to Step 1 to extract or upload'}>
            Pitch <span style="font-size:0.85em">〰️</span>
          </button>
          {#if pitchLineLoading}
            <div class="loading-modal-overlay">
              <div class="loading-modal">
                <span class="loading-spinner"></span>
                <span class="loading-label">Analysing pitch…</span>
              </div>
            </div>
          {/if}
          {#if pitchLineFrames.length > 0 && !pitchLineLoading}
            <button class="tool-btn sm" on:click={() => { pitchLineFrames = []; pitchLineSourceUrl = null; pitchLineVisible = false; draw(); }} title="Clear pitch line">🗑</button>
          {/if}
        </div>
      </div>
      <div id="BPM-controls-wrapper">
        <div class="bpm-controls">
          <span class="bpm-label">BPM</span>
          <!-- <button class="tool-btn sm" on:click={() => { bpm = Math.max(10, bpm - 1); handleBpmChange(); }}>−</button> -->
          <!-- <button class="tool-btn sm nudge" on:click={() => { bpm = Math.round((Math.max(10, bpm - 0.1)) * 1000) / 1000; handleBpmChange(); }}>−.1</button>
          <button class="tool-btn sm nudge" on:click={() => { bpm = Math.round((Math.max(10, bpm - 0.01)) * 1000) / 1000; handleBpmChange(); }}>−.01</button> -->
          <input type="number" class="bpm-input" class:disabled-audio={uiModalGuardActive} bind:value={bpm} on:change={() => { if (uiModalGuardActive) return; console.log('[UI] bpm input', bpm); handleBpmChange(); }} step="0.001" min="10" max="1000" disabled={uiModalGuardActive} />
          <!-- <button class="tool-btn sm nudge" on:click={() => { bpm = Math.round((bpm + 0.01) * 1000) / 1000; handleBpmChange(); }}>.01+</button>
          <button class="tool-btn sm nudge" on:click={() => { bpm = Math.round((bpm + 0.1) * 1000) / 1000; handleBpmChange(); }}>.1+</button> -->
          <!-- <button class="tool-btn sm" on:click={() => { bpm = bpm + 1; handleBpmChange(); }}>+</button> -->
              <button class="tool-btn" class:disabled-audio={uiModalGuardActive} style="margin-left: 4px;"
                on:click={() => { if (uiModalGuardActive) return; openTapper(); }}
                disabled={uiModalGuardActive}
                title={uiModalGuardActive ? 'Disabled while modal is active' : 'Tap the beat to calculate BPM (Enter key)'}>
            Tap
          </button>
          <!-- Cal button kept for reference (beat marker calibration)
          <button class="tool-btn" class:active={beatMarkerMode} style="margin-left: 4px;"
                on:click={() => beatMarkerMode ? (exitBeatMarkerMode(), draw()) : enterBeatMarkerMode()}
                title="Calibrate BPM by clicking downbeats on the waveform">
            Cal
          </button>
          -->
        </div>
        <div id="gap-controls" title="Click to set a new GAP position on the waveform (Ctrl+G)">
          <span class="bpm-label gap-label">GAP</span>
          <span class="gap-input gap-display" class:disabled-audio={uiModalGuardActive} role="button" tabindex="0"
            on:click={() => { if (uiModalGuardActive) return; enterSetGapMode(); }}
            on:keydown={(e) => { if (uiModalGuardActive) return; e.key === 'Enter' && enterSetGapMode(); }}
            title={uiModalGuardActive ? 'Disabled while modal is active' : `Click to set GAP (Ctrl+G) — ${gapMs}ms`}
            >
            {gapMs} ms
          </span>
        </div>
      </div>
      <div id="edit-controls-wrapper">
          <button class="tool-btn" class:disabled-audio={uiModalGuardActive} on:click={() => { if (uiModalGuardActive) return; autoFixWordSpaces(); }} disabled={uiModalGuardActive} title={uiModalGuardActive ? 'Disabled while modal is active' : 'Convert old-style leading spaces to trailing (for imported songs)'}>
           Fix Spaces&nbsp;🔤
        </button>
          <button class="tool-btn" class:disabled-audio={uiModalGuardActive} on:click={() => { if (uiModalGuardActive) return; openTextEditor(); }} disabled={uiModalGuardActive} title={uiModalGuardActive ? 'Disabled while modal is active' : 'Edit raw Ultrastar .txt'}>
           Text&nbsp;📝 
        </button>
          <button class="tool-btn" class:disabled-audio={uiModalGuardActive} on:click={() => { if (uiModalGuardActive) return; loadSessionNotes(); showNotesModal = true; }} disabled={uiModalGuardActive} title={uiModalGuardActive ? 'Disabled while modal is active' : 'Session notes'}>
           Notes&nbsp;🗒️
        </button>
      </div>
    </div>
    <div class="toolbar-toolset-wrapper">
      <div class="playback-controls">
        <button class="tool-btn" on:click={() => { console.log('[UI] jump to 0s'); seekToTime(0); }} title="Jump to 0s">⏮⏮</button>
        <button class="tool-btn" on:click={() => { console.log('[UI] jump to GAP'); seekToTime(gapMs / 1000); }} title="Jump to GAP (beat 0)">GAP⏮</button>
        <button class="tool-btn" id="toggle-playback-btn" on:click={() => { console.log('[UI] togglePlayback'); togglePlayback(); }} title="Space">
          {isPlaying ? '⏸ Pause' : '▶ Play'}
        </button>
        {#if liveWordTokens.length > 0}
          <button class="tool-btn sm" class:active={liveWordsVisible}
            on:click={() => { liveWordsVisible = !liveWordsVisible; draw(); }}
            title={liveWordsVisible ? 'Hide analyzed words' : 'Show analyzed words'}>
            Words {liveWordsVisible ? '👁' : '🙈'}
          </button>
          <button class="tool-btn sm" on:click={() => { liveWordTokens = []; draw(); }} title="Clear analyzed words">🗑W</button>
        {/if}
        <button class="tool-btn" on:click={() => { console.log('[UI] togglePlayback'); toggleLoop(); }} class:active={loopEnabled} title="Loop (L)">
          <span class="mic-icon-wrap" class:mic-off={!loopEnabled}>🔁</span>
        </button>
        <button class="tool-btn" style="width: 62px;"
          on:click={toggleScrollMode}
          title={scrollMode ? 'Following playhead — click to pin' : 'View pinned — click to follow'}>
          {scrollMode ? 'Scroll' : 'Page'}
        </button>
      </div>
      <div id="time-display-wrapper">
        <span class="time-display">{formatTime(currentTimeSec)}</span>
      </div>
      <div class="speed-controls">
        <span class="speed-label">Speed</span>
        {#each [0.25, 0.5, 0.75, 1.0] as rate}
          <button
            class="tool-btn sm"
            class:active={playbackRate === rate}
            on:click={() => setPlaybackRate(rate)}
          >{rate}x</button>
        {/each}
      </div>
      <div id="audio-source-wrapper">
        <div class="audio-source-toggle" title="Audio source">
          <button class="tool-btn sm" class:active={audioSource === 'vocals'} class:disabled-audio={!hasVocalsAudio || segRecAudioSwitchLocked} disabled={segRecAudioSwitchLocked} on:click={() => { if (segRecAudioSwitchLocked) return; hasVocalsAudio ? switchAudioSource('vocals') : handleMissingAudio('vocals'); }} title={segRecAudioSwitchLocked ? 'Disabled while recording is active' : hasVocalsAudio ? 'Original vocals (unedited)' : 'No vocals — go to Step 1'}>Vocals 🎤</button>
          <button class="tool-btn sm" class:active={audioSource === 'edited'} class:disabled-audio={!hasVocalsAudio || (!cleanedAudioAvailable && segRecPatched.size === 0) || segRecAudioSwitchLocked} disabled={segRecAudioSwitchLocked} on:click={() => { if (segRecAudioSwitchLocked) return; if (!hasVocalsAudio) { handleMissingAudio('vocals'); return; } if (!cleanedAudioAvailable && segRecPatched.size === 0) return; switchAudioSource('edited'); }} title={segRecAudioSwitchLocked ? 'Disabled while recording is active' : cleanedAudioAvailable || segRecPatched.size > 0 ? (segRecPatched.size > 0 ? 'Edited vocals (with spliced recordings)' : 'Cleaned vocals (muted cleanup regions)') : 'No edits yet — add cleanup segments first'}>Edited 🎙</button>
          <button class="tool-btn sm" class:active={audioSource === 'original'} class:disabled-audio={!hasOriginalAudio || segRecAudioSwitchLocked} disabled={segRecAudioSwitchLocked} on:click={() => { if (segRecAudioSwitchLocked) return; hasOriginalAudio ? switchAudioSource('original') : handleMissingAudio('original'); }} title={segRecAudioSwitchLocked ? 'Disabled while recording is active' : hasOriginalAudio ? 'Full mix' : 'No full mix — go to Step 1 to upload'}>Full Mix 🎵</button>
        </div>
        <div class="volume-control" title="Audio volume">
          <span class="volume-icon" on:click={toggleMuteVocal}>
            {muteVocal ? '🔇' : audioVolume < 0.3 ? '🔈' : audioVolume < 0.7 ? '🔉' : '🔊'}
          </span>
          <input type="range" min="0" max="1" step="0.05" value={audioVolume} on:input={handleVolumeChange} class="volume-slider" />
        </div>
      </div>
      <div id="midi-wrapper">
        <button class="tool-btn" class:active={midiPlayback} on:click={() => { console.log('[UI] toggleMidi'); toggleMidiPlayback(); }} title="Toggle MIDI pitch tones during playback (9)">
          <span>MIDI</span><span style="padding-left: 4px">{midiPlayback ? ' 🔈' : ' 🔇'}</span>
        </button>
      </div>
      <div id="metronome-wrapper">
        <button class="tool-btn" class:active={metronomeEnabled} on:click={() => { console.log('[UI] toggleMetronome'); toggleMetronome(); }} title="Toggle Metronome click on each beat (0)">
          <span>Metronome</span><span style="padding-left: 4px">{metronomeEnabled ? ' 🔈' : ' 🔇'}</span>
        </button>
        {#if metronomeEnabled}
          <button class="tool-btn sm" class:active={metronomeToolOpen} on:click={() => { metronomeToolOpen = !metronomeToolOpen; if (!metronomeToolOpen) clearMetronomePickTarget(); }} title="Open metronome downbeat tool">
            ⚙️
          </button>
        {/if}
      </div>
    </div>
  </div>

  <div class="canvas-container">
    <canvas
      bind:this={canvasEl}
      on:mousedown={handleMouseDown}
      on:wheel|nonpassive={handleWheel}
      on:contextmenu={handleContextMenu}
    ></canvas>
    {#if micEnabled || vocalTraceEnabled}
      <div class="active-mode-badge" class:badge-mic={micEnabled} class:badge-vocal={vocalTraceEnabled}>
        <span class="badge-dot"></span>
        {micEnabled ? 'MIC' : 'VOCAL'}
      </div>
    {/if}
    {#if showWaveform}
      <input type="range" class="wave-height-slider wave-height-overlay" min="40" max="240" step="10"
             bind:value={waveformHeight}
             on:input={() => { saveEditorUiPrefs('waveform-height'); resizeCanvas(); draw(); }}
             title="Waveform height: {waveformHeight}px" />
    {/if}
    
    <input type="range" class="zoom-overlay-slider" min="5" max="60" step="0.5"
           value={zoom}
           on:input={(e) => {
             const oldZoom = zoom;
             const cw = canvasEl?.width || 800;
             const anchorBeat = (scrollX + cw / 2) / oldZoom;
             zoom = parseFloat(e.target.value);
             scrollX = Math.max(getMinBeat() * zoom, anchorBeat * zoom - cw / 2);
             draw();
           }}
           title="Zoom: {zoom.toFixed(1)}x" />
    {#if micStarting}
      <div class="mic-starting-overlay">
        <div class="mic-starting-box">
          🎙️ Starting microphone…
        </div>
      </div>
    {/if}
  </div>

  <!-- BPM Tapper modal -->
  {#if tapperOpen}
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div class="tapper-backdrop" on:click|self={closeTapper}>
      <div class="tapper-modal">
        <button class="tapper-close-btn" on:click={closeTapper} title="Close">✕</button>
        <h3 class="tapper-title">BPM Tapper</h3>
        <p class="tapper-hint">Tap the button or press <kbd>Enter</kbd> on every beat.<br><kbd>Space</kbd> still controls playback.</p>

        <div class="tapper-bpm">
          {#if tapBpm !== null}
            <span class="tapper-bpm-value">{tapBpm.toFixed(1)}</span>
            <span class="tapper-bpm-unit">BPM</span>
          {:else if tapTimes.length === 1}
            <span class="tapper-bpm-waiting">---</span>
          {:else}
            <span class="tapper-bpm-waiting">Tap to start…</span>
          {/if}
        </div>

        {#if tapTimes.length > 0}
          <div class="tapper-count">{tapTimes.length} tap{tapTimes.length !== 1 ? 's' : ''}</div>
        {/if}

        <button class="tapper-tap-btn" on:click={recordTap}>TAP</button>

        <div class="tapper-actions">
          <button class="tool-btn" on:click={() => seekToTime(0)} title="Jump to 0s">⏮⏮</button>
          <button class="tool-btn" on:click={() => seekToTime(gapMs / 1000)} title="Jump to GAP">GAP⏮</button>
          <button class="tool-btn" on:click={togglePlayback}>{isPlaying ? '⏸ Pause' : '▶ Play'}</button>
          <button class="tool-btn tapper-reset-btn" on:click={resetTapper}>Reset</button>
        </div>

        {#if tapBpm !== null}
          <div class="tapper-apply-row">
            {#each [1,2,3,4,5,6,7,8] as mult}
              <button class="tapper-apply-btn" class:primary={mult === 1}
                on:click={() => { bpm = Math.round(tapBpm * mult * 10) / 10; handleBpmChange(); closeTapper(); }}>
                <span class="tapper-mult">{mult}×</span>
                <span class="tapper-mult-bpm">{(tapBpm * mult).toFixed(1)}</span>
              </button>
            {/each}
          </div>
        {/if}
      </div>
    </div>
  {/if}

  <!-- Beat Marker Calibration overlay bar -->
  {#if beatMarkerMode}
    <div class="beatcal-mode-bar">
      <span class="beatcal-mode-text">BPM CAL — click any downbeats on waveform • right-click removes • Esc cancels</span>
      {#if beatMarkers.length > 0}
        <div class="beatcal-marker-list">
          {#each beatMarkers as m, i}
            <span class="beatcal-marker-item">
              bar <input class="beatcal-bar-input" type="number" min="1" step="1"
                value={m.bar}
                on:change={(e) => {
                  const v = parseInt(e.target.value);
                  if (!isNaN(v) && v >= 1) {
                    beatMarkers[i] = { ...m, bar: v };
                    beatMarkers = [...beatMarkers].sort((a, b) => a.t - b.t);
                    bpmCalcResult = calcBpmFromMarkers(beatMarkers);
                    draw();
                  }
                }} /><span class="beatcal-marker-time">@{m.t.toFixed(2)}s</span>
              <button class="beatcal-rm-btn" on:click={() => {
                beatMarkers = beatMarkers.filter((_, j) => j !== i);
                bpmCalcResult = calcBpmFromMarkers(beatMarkers);
                draw();
              }}>×</button>
            </span>
          {/each}
        </div>
      {/if}
      {#if bpmCalcResult}
        <span class="beatcal-result">
          BPM: <strong>{bpmCalcResult.bpm.toFixed(3)}</strong>
          &nbsp;·&nbsp; GAP: <strong>{bpmCalcResult.gapMs}ms</strong>
          &nbsp;({beatMarkers.length} markers)
        </span>
        <button class="beatcal-apply-btn" on:click={applyBpmCalibration}>✓ Apply</button>
      {:else}
        <span class="beatcal-hint">{beatMarkers.length < 2 ? 'Place ≥2 markers' : 'Check bar numbers'}</span>
      {/if}
      <button class="beatcal-cancel-btn" on:click={() => { exitBeatMarkerMode(); draw(); }}>✕ Cancel</button>
    </div>
  {/if}

  <!-- Set GAP mode overlay bar -->
  {#if toastMsg}
    <div class="toast-bar" class:toast-center={toastCenter}>{toastMsg}</div>
  {/if}

  {#if setGapMode}
    <div class="setgap-mode-bar">
      <span class="setgap-mode-text">
        SET GAP MODE — Click a grid line to set the GAP position, or press Esc to cancel
      </span>
      <button class="setgap-cancel-btn" on:click={cancelSetGapMode}>✕ Cancel</button>
    </div>
  {/if}

  <!-- Paste mode overlay bar -->
  {#if pasteMode}
    <div class="paste-mode-bar">
      <span class="paste-mode-text">
        {clipboard?.mode === 'cut' ? '✂️ CUT' : '📋 COPY'} MODE — Click on the canvas to place {clipboard?.notes.length || 0} note{clipboard?.notes.length !== 1 ? 's' : ''}, or press Esc to cancel
      </span>
      <button class="paste-cancel-btn" on:click={cancelPaste}>✕ Cancel</button>
    </div>
  {/if}

  <!-- Selection count indicator -->
  {#if selectedNotes.size > 1 && !pasteMode}
    <div class="selection-info-bar">
      <span>{selectedNotes.size} notes selected</span>
      <span class="selection-hint">Ctrl+X cut · Ctrl+C copy · Del delete · Esc deselect</span>
    </div>
  {/if}

  <!-- Context Menu -->
  {#if contextMenu.visible}
    {@const ctxNote = notes.find(n => n.id === contextMenu.noteId)}
    {@const isMultiCtx = selectedNotes.size > 1 && selectedNotes.has(contextMenu.noteId)}
    {@const canMergePrev = ctxNote && !isMultiCtx && canMergeWithPrevious(ctxNote.id)}
    {@const canMergeNext = ctxNote && !isMultiCtx && canMergeWithNext(ctxNote.id)}
    {#if ctxNote}
      <div
        class="context-menu"
        bind:this={contextMenuEl}
        style="left: {contextMenu.x}px; top: {contextMenu.y}px;"
      >
        {#if contextMenu.isBreak}
          <!-- Break context menu -->
          <div class="ctx-header">
            <span class="ctx-break-label">🔴 Break @ beat {ctxNote.startBeat}</span>
          </div>
          <div class="ctx-divider"></div>
          <button class="ctx-item" on:click={() => nudgeBreak(ctxNote.id, -1)}>
            ← Nudge Left <span class="ctx-shortcut">-1</span>
          </button>
          <button class="ctx-item" on:click={() => nudgeBreak(ctxNote.id, 1)}>
            → Nudge Right <span class="ctx-shortcut">+1</span>
          </button>
          <div class="ctx-divider"></div>
          <button class="ctx-item danger" on:click={() => deleteNote(ctxNote.id)}>
            🗑 Delete Break <span class="ctx-shortcut">Del</span>
          </button>
        {:else}
          <!-- Note context menu -->
          <div class="ctx-header">
            {#if isMultiCtx}
              <span class="ctx-multi-label">{selectedNotes.size} notes selected</span>
            {:else}
            <div class="ctx-syllable-wrapper">
              <div class="ctx-syllable-highlight" aria-hidden="true">{@html editingSyllable.replace(/ /g, '<span class="spc">·</span>')}</div>
              <input
                class="ctx-syllable-input"
                type="text"
                bind:value={editingSyllable}
                on:input={() => updateSyllable(ctxNote.id, editingSyllable)}
                on:keydown|stopPropagation={(e) => { if (e.key === 'Escape') closeContextMenu(); }}
                placeholder="syllable"
              />
            </div>
            <span class="ctx-pitch">{noteName(ctxNote.pitch)}</span>
            {/if}
          </div>
          {#if !isMultiCtx}
          <label class="ctx-checkbox" class:space-on={ctxNote.syllable.endsWith(' ')} class:space-off={!ctxNote.syllable.endsWith(' ')}>
            <input type="checkbox"
              checked={ctxNote.syllable.endsWith(' ')}
              on:change={(e) => toggleWordSpace(ctxNote.id, e.target.checked)}
            />
            Word space
          </label>
          {/if}
          <div class="ctx-divider"></div>
          {#if !isMultiCtx}
          <button class="ctx-item" on:click={() => playNotePitch(ctxNote.id)}>
            🔊 Play Pitch <span class="ctx-shortcut">P</span>
          </button>
          <button class="ctx-item" on:click={() => splitNote(ctxNote.id, contextMenu.beat)}>
            ✂️ Split Note <span class="ctx-shortcut">S</span>
          </button>
          <button class="ctx-item" on:click={() => openVibratoModal(ctxNote.id)}>
            〰️ Vibrato Tool
          </button>
          <button class="ctx-item" disabled={!canMergePrev} on:click={() => mergeWithPrevious(ctxNote.id)}>
            🔗 Join with Previous <span class="ctx-shortcut">Shift+J</span>
          </button>
          <button class="ctx-item" disabled={!canMergeNext} on:click={() => mergeWithNext(ctxNote.id)}>
            🔗 Join with Next <span class="ctx-shortcut">J</span>
          </button>
          <div class="ctx-divider"></div>
          {/if}
          <div class="ctx-type-group">
            <span class="ctx-type-label">Type:</span>
            <button
              class="ctx-type-btn" class:active={!ctxNote.isGolden && !ctxNote.isRap}
              on:click={() => setNoteType(ctxNote.id, 'normal')}
            >Normal</button>
            <button
              class="ctx-type-btn golden" class:active={ctxNote.isGolden}
              on:click={() => setNoteType(ctxNote.id, 'golden')}
            >★ Golden</button>
            <button
              class="ctx-type-btn rap" class:active={ctxNote.isRap}
              on:click={() => setNoteType(ctxNote.id, 'rap')}
            >F Rap</button>
          </div>
          <div class="ctx-divider"></div>
          <button class="ctx-item" on:click={clipboardCut}>
            ✂️ Cut {selectedNotes.size > 1 ? `(${selectedNotes.size} notes)` : ''} <span class="ctx-shortcut">Ctrl+X</span>
          </button>
          <button class="ctx-item" on:click={clipboardCopy}>
            📋 Copy {selectedNotes.size > 1 ? `(${selectedNotes.size} notes)` : ''} <span class="ctx-shortcut">Ctrl+C</span>
          </button>
          <div class="ctx-divider"></div>
          <button class="ctx-item danger" on:click={() => deleteNote(ctxNote.id)}>
            🗑 Delete {isMultiCtx ? `(${selectedNotes.size} notes)` : 'Note'} <span class="ctx-shortcut">Del</span>
          </button>
        {/if}
      </div>
    {:else if contextMenu.isPasteMenu}
      <!-- Paste mode context menu -->
      <div
        class="context-menu"
        bind:this={contextMenuEl}
        style="left: {contextMenu.x}px; top: {contextMenu.y}px;"
      >
        <div class="ctx-header">
          <span class="ctx-location-label">{clipboard?.mode === 'cut' ? '✂️ CUT' : '📋 COPY'} — {clipboard?.notes.length || 0} note{clipboard?.notes.length !== 1 ? 's' : ''}</span>
        </div>
        <div class="ctx-divider"></div>
        <button class="ctx-item" on:click={() => { finalizePaste(contextMenu.beat); closeContextMenu(); }}>
          📌 Paste here <span class="ctx-shortcut">V</span>
        </button>
        <div class="ctx-divider"></div>
        <button class="ctx-item" on:click={() => { cancelPaste(); closeContextMenu(); }}>
          ✕ Cancel <span class="ctx-shortcut">Esc</span>
        </button>
      </div>
    {:else if contextMenu.isCleanup}
      {@const seg = cleanupSegments.find(s => s.id === contextMenu.cleanupId)}
      {@const isPatchedSeg = seg ? segRecPatched.has(seg.id) : false}
      {@const joinNeighbors = seg ? getJoinableCleanupNeighborsForSegment(seg.id) : { left: null, right: null }}
      {#if seg}
        <div
          class="context-menu"
          bind:this={contextMenuEl}
          style="left: {contextMenu.x}px; top: {contextMenu.y}px;"
        >
          <div class="ctx-header">
            <span class="ctx-location-label">🧹 Cleanup {(seg.startMs/1000).toFixed(2)}s → {(seg.endMs/1000).toFixed(2)}s</span>
          </div>
          <div class="ctx-divider"></div>
          <button class="ctx-item" on:click={() => armSegmentRecording(seg.id)}>
            🎙 Record over this segment
          </button>
          <button class="ctx-item" on:click={() => openSegmentRegenerateFromCleanup(seg)}>
            🤖 AI Generate Notes In Segment
          </button>
          {#if isPatchedSeg}
            <button class="ctx-item" on:click={() => emptyRecordedCleanupSegment(seg.id)}>
              🧼 Make Segment Empty
            </button>
          {/if}
          <button class="ctx-item" on:click={() => splitCleanupSegmentAtMs(seg.id, contextMenu.ms)}>
            ✂️ Split Cleanup Segment
          </button>
          {#if joinNeighbors.left || joinNeighbors.right}
            <div class="ctx-divider"></div>
          {/if}
          {#if joinNeighbors.left}
            <button class="ctx-item" on:click={() => joinCleanupSegments(joinNeighbors.left.left.id, joinNeighbors.left.right.id)}>
              🔗 Join with Previous Segment
            </button>
          {/if}
          {#if joinNeighbors.right}
            <button class="ctx-item" on:click={() => joinCleanupSegments(joinNeighbors.right.left.id, joinNeighbors.right.right.id)}>
              🔗 Join with Next Segment
            </button>
          {/if}
          <button class="ctx-item danger" on:click={() => deleteCleanupSegment(seg.id)}>
            🗑 Delete Cleanup Segment
          </button>
        </div>
      {/if}
    {:else if contextMenu.isWaveformEmpty}
      {@const joinPair = getJoinableCleanupPairAtMs(contextMenu.ms)}
      <div
        class="context-menu"
        bind:this={contextMenuEl}
        style="left: {contextMenu.x}px; top: {contextMenu.y}px;"
      >
        <div class="ctx-header">
          <span class="ctx-location-label">Waveform @ {((contextMenu.ms ?? 0) / 1000).toFixed(3)}s</span>
        </div>
        <div class="ctx-divider"></div>
        <button class="ctx-item" on:click={() => addCleanupSegmentAtMs(contextMenu.ms ?? 0)}>
          🧹 Add Cleanup Segment
        </button>
        {#if isBeatInsideActiveLoop(contextMenu.beat)}
          <button class="ctx-item" on:click={openSegmentRegenerateFromLoopContext}>
            🤖 AI Generate Notes In Loop
          </button>
        {/if}
        {#if joinPair}
          <button class="ctx-item" on:click={() => joinCleanupSegments(joinPair.left.id, joinPair.right.id)}>
            🔗 Join Adjacent Cleanup Segments
          </button>
        {/if}
      </div>
    {:else if contextMenu.isFlag}
      <!-- Flag context menu -->
      <div
        class="context-menu"
        bind:this={contextMenuEl}
        style="left: {contextMenu.x}px; top: {contextMenu.y}px;"
      >
        <div class="ctx-header">
          <span class="ctx-location-label">🟢 Flag @ beat {contextMenu.beat}</span>
        </div>
        <div class="ctx-divider"></div>
        <button class="ctx-item" on:click={() => nudgeFlag(contextMenu.flagId, -1)}>
          ← Nudge Left <span class="ctx-shortcut">-1</span>
        </button>
        <button class="ctx-item" on:click={() => nudgeFlag(contextMenu.flagId, 1)}>
          → Nudge Right <span class="ctx-shortcut">+1</span>
        </button>
        <div class="ctx-divider"></div>
        <button class="ctx-item danger" on:click={() => deleteFlag(contextMenu.flagId)}>
          🗑 Delete Flag <span class="ctx-shortcut">Del</span>
        </button>
      </div>
    {:else if contextMenu.isEmpty}
      <!-- Empty space context menu -->
      <div
        class="context-menu"
        bind:this={contextMenuEl}
        style="left: {contextMenu.x}px; top: {contextMenu.y}px;"
      >
        <div class="ctx-header">
          <span class="ctx-location-label">Beat {contextMenu.beat} · {noteName(contextMenu.pitch)}</span>
        </div>
        <div class="ctx-divider"></div>
        {#if contextMenu.traceFrame}
          <button class="ctx-item ctx-item-trace" on:click={() => addNoteAt(contextMenu.traceFrame.beat, contextMenu.traceFrame.pitch, contextMenu.traceFrame.duration)}>
            🎵 Add note on <span class="ctx-trace-swatch"></span> <span class="ctx-trace-label">{noteName(contextMenu.traceFrame.pitch)}</span>
          </button>
        {/if}
        <button class="ctx-item" on:click={() => addNoteAt(contextMenu.beat, contextMenu.pitch)}>
          🎵 Add Note
        </button>
        <button class="ctx-item" on:click={() => addBreakAt(contextMenu.beat)}>
          🔴 Add Break
        </button>
        <button class="ctx-item" on:click={() => addFlagAt(contextMenu.beat)}>
          🟢 Add Flag
        </button>
        {#if isBeatInsideActiveLoop(contextMenu.beat)}
          <button class="ctx-item" on:click={openSegmentRegenerateFromLoopContext}>
            🤖 AI Generate Notes In Loop
          </button>
        {/if}
        {#if clipboard}
          <div class="ctx-divider"></div>
          <button class="ctx-item" on:click={() => { finalizePaste(contextMenu.beat); closeContextMenu(); }}>
            📌 Paste Here ({clipboard.notes.length} notes)
          </button>
        {/if}
        <div class="ctx-divider"></div>
        <button class="ctx-item" on:click={() => { seekToTime(beatToTime(contextMenu.beat)); closeContextMenu(); }}>
          ⏩ Seek Here
        </button>
      </div>
    {/if}
  {/if}

  <!-- Custom scrollbar -->
  <div class="scrollbar-container">
    <div class="scrollbar-track"
      bind:this={scrollTrackEl}
      on:pointerdown={onScrollTrackPointerDown}
    >
      <div class="scrollbar-cleanup-lane" aria-hidden="true">
        {#each cleanupSegments as seg (seg.id)}
          {@const startPct = ((timeToBeat(seg.startMs / 1000) - getMinBeat()) / scrollBeatRange * 100)}
          {@const endPct = ((timeToBeat(seg.endMs / 1000) - getMinBeat()) / scrollBeatRange * 100)}
          {@const widthPct = Math.max(0.2, endPct - startPct)}
          <div
            class="scrollbar-cleanup-seg"
            class:patched={segRecPatched.has(seg.id)}
            style="left: {startPct.toFixed(3)}%; width: {widthPct.toFixed(3)}%;"
          ></div>
        {/each}
      </div>
      <!-- playhead tick -->
      {#if !isPlaying}
        <div class="scrollbar-playhead" style="left: {playheadPct}%"></div>
      {/if}
      {#each flags as flag}
        <div class="scrollbar-flag" style="left: {((flag.beat - getMinBeat()) / scrollBeatRange * 100).toFixed(3)}%"></div>
      {/each}
      {#each notes as note (note.id)}
        {#if note.type === 'break'}
          <div class="scrollbar-break" style="left: {((note.startBeat - getMinBeat()) / scrollBeatRange * 100).toFixed(3)}%"></div>
        {/if}
      {/each}
      <!-- draggable handle -->
      <div class="scrollbar-handle" style="left: {scrollHandlePct}%"></div>
    </div>
  </div>

  <div class="legend">
    <span class="legend-item"><span class="dot blue"></span> Normal note</span>
    <span class="legend-item"><span class="dot yellow"></span> Edited note</span>
    <span class="legend-item"><span class="dot gold"></span> Golden note</span>
    <span class="legend-item"><span class="dot orange"></span> Rap note</span>
    <span class="legend-item"><span class="dot red-line"></span> Break line</span>
    <span class="legend-item"><span class="dot green-flag"></span> Flag</span>
    <span class="legend-item"><span class="dot cleanup-range"></span> Cleanup segment</span>
    <span class="legend-item"><span class="dot recorded-range"></span> Recorded segment</span>
  </div>

  <!-- Stats bar for debugging timing -->
  {#if notes.length > 0}
    {@const realNotes = notes.filter(n => n.type !== 'break')}
    {@const firstBeat = realNotes.length > 0 ? realNotes[0].startBeat : 0}
    {@const lastNote = realNotes.length > 0 ? realNotes[realNotes.length - 1] : null}
    {@const lastBeat = lastNote ? lastNote.startBeat + lastNote.duration : 0}
    {@const firstTimeSec = gapMs / 1000 + (firstBeat * 60) / bpm}
    {@const lastTimeSec = gapMs / 1000 + (lastBeat * 60) / bpm}
    <div class="stats-bar">
      <span>
        {bpmChanged ? '⚠ Modified: ' : 'Generated: '}BPM {(bpm ?? 0).toFixed(1)}{bpmChanged ? ` (was ${(initialBpm ?? 0).toFixed(1)})` : ''} | GAP {gapMs ?? 0}ms{bpmChanged ? ` (was ${initialGap ?? 0}ms)` : ''} | Notes {firstBeat}–{lastBeat} beats | {formatTime(firstTimeSec)}–{formatTime(lastTimeSec)} | Audio {formatTime(audioDuration)}
      </span>
    </div>
  {/if}

  <!-- Hidden audio element for playback -->
  <audio bind:this={audioEl} src={currentAudioUrl} preload="auto"
    on:canplay={() => { editedAudioLoading = false; }}
    on:error={() => { editedAudioLoading = false; }}
    on:ended={() => {
      isPlaying = false;
      cancelAnimationFrame(animFrame);
      stopAllMidiNotes();
      draw();
      console.log('[Audio] Reached end of track — stopped playback');
    }}
  ></audio>

  {#if metronomeEnabled && metronomeToolOpen}
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div
      class="metronome-tool-modal"
      style="left:{metronomeToolX}px;top:{metronomeToolY}px"
      on:mousedown={metronomeToolMouseDown}
      role="dialog"
      aria-label="Metronome downbeat tool"
    >
      <div class="metronome-tool-title">⏱ Metronome Downbeat Tool</div>

      <div class="metronome-tool-row">
        <button class="tool-btn sm" class:active={metronomePickTarget === 1} on:click={() => armMetronomeDownbeatPick(1)}>
          Set Downbeat
        </button>
      </div>

      {#if metronomeDownbeat1Beat === null}
        <div class="metronome-no-downbeat-hint">Set a downbeat first to configure signature and speed.</div>
      {/if}

      <div class="metronome-signature-row" class:metronome-disabled={metronomeDownbeat1Beat === null}>
        <select class="mic-select" bind:value={metronomeSigNumerator} disabled={metronomeDownbeat1Beat === null} on:change={(e) => { metronomeSigNumerator = Number(e.target.value); recalcMetronomeFromControls('signature-change'); markUnsaved(); }} title="Time signature numerator">
          {#each METRONOME_SIGNATURE_NUM_OPTIONS as num}
            <option value={num}>{num}</option>
          {/each}
        </select>
        <span class="metronome-signature-slash">/</span>
        <select class="mic-select" bind:value={metronomeSigDenominator} disabled={metronomeDownbeat1Beat === null} on:change={(e) => { metronomeSigDenominator = Number(e.target.value); recalcMetronomeFromControls('signature-change'); markUnsaved(); }} title="Time signature denominator">
          {#each METRONOME_SIGNATURE_DEN_OPTIONS as den}
            <option value={den}>{den}</option>
          {/each}
        </select>
      </div>

      {#if getMetronomeSignatureIntervalBeats() === null}
        <div class="metronome-signature-warning">
          Impossible value for this BPM grid. Choose another signature.
        </div>
      {:else}
        <div class="metronome-signature-hint">
          Measure size: {getMetronomeSignatureIntervalBeats()?.toFixed(3)} beats
        </div>
      {/if}

      <div class="metronome-signature-hint">
        Downbeat: {metronomeDownbeat1Beat === null ? 'not set' : metronomeDownbeat1Beat.toFixed(3)}
      </div>

      <div class="metronome-speed-row" class:metronome-disabled={metronomeDownbeat1Beat === null}>
        <button class="tool-btn sm" disabled={metronomeDownbeat1Beat === null} on:click={() => nudgeMetronomeSpeed('slower')} title="Half speed">−</button>
        <span class="metronome-speed-value">Speed x{metronomeSpeedFactor}</span>
        <button class="tool-btn sm" disabled={metronomeDownbeat1Beat === null} on:click={() => nudgeMetronomeSpeed('faster')} title="Double speed">+</button>
      </div>

      <div class="metronome-tool-row">
        <button class="tool-btn sm" on:click={clearMetronomeDownbeatReference}>
          Reset
        </button>
        <button class="tool-btn sm" on:click={() => { metronomeToolOpen = false; clearMetronomePickTarget(); }}>
          Close
        </button>
      </div>
    </div>
  {/if}

  <div class="shortcut-bar">
    <div class="shortcut-group">
      <span class="shortcut-label">Playback</span>
      <span class="shortcut"><kbd>Space</kbd> play/pause</span>
      <span class="shortcut"><kbd>←→</kbd> seek ±5s</span>
      <span class="shortcut"><kbd>Shift+←→</kbd> ±1s</span>
      <span class="shortcut"><kbd>L</kbd> loop</span>
      <span class="shortcut"><kbd>M</kbd> mic</span>
      <span class="shortcut"><kbd>V</kbd> vocal trace</span>
    </div>
    <div class="shortcut-group">
      <span class="shortcut-label">Navigate</span>
      <span class="shortcut"><kbd>Scroll</kbd> pan</span>
      <span class="shortcut"><kbd>Ctrl+Scroll</kbd> zoom</span>
    </div>
    <div class="shortcut-group">
      <span class="shortcut-label">Select</span>
      <span class="shortcut"><kbd>Click</kbd> select</span>
      <span class="shortcut"><kbd>Ctrl+Click</kbd> multi</span>
      <span class="shortcut"><kbd>Ctrl+Drag</kbd> box select</span>
    </div>
    <div class="shortcut-group">
      <span class="shortcut-label">Edit</span>
      <span class="shortcut"><kbd>Drag</kbd> move</span>
      <span class="shortcut"><kbd>Shift+Drag</kbd> move X only</span>
      <span class="shortcut"><kbd>Tab</kbd> next note</span>
      <span class="shortcut"><kbd>Shift+Tab</kbd> previous note</span>
      <span class="shortcut"><kbd>Option+←→</kbd> extend to adjacent</span>
      <span class="shortcut"><kbd>Shift+Option+←→</kbd> move boundary</span>
      <span class="shortcut"><kbd>↑↓</kbd> pitch ±1 semitone</span>
      <span class="shortcut"><kbd>Ctrl+↑↓</kbd> pitch ±1 octave</span>
      <span class="shortcut"><kbd>S</kbd> split</span>
      <span class="shortcut"><kbd>Del</kbd> delete</span>
      <span class="shortcut"><kbd>P</kbd> play pitch</span>
      <span class="shortcut"><kbd>Ctrl+←→</kbd> resize right edge</span>
      <span class="shortcut"><kbd>Shift+Ctrl+←→</kbd> resize left edge</span>
      <span class="shortcut"><kbd>Ctrl+X/C/V</kbd> cut/copy/paste</span>
    </div>
    <div class="shortcut-group">
      <span class="shortcut-label">Tools</span>
      <span class="shortcut"><kbd>Ctrl+Z</kbd> undo</span>
      <span class="shortcut"><kbd>Shift+Ctrl+Z</kbd> redo</span>
      <span class="shortcut"><kbd>Ctrl+S</kbd> save</span>
      <span class="shortcut"><kbd>Ctrl+G</kbd> set GAP</span>
      <span class="shortcut"><kbd>9</kbd> MIDI · <kbd>0</kbd> metronome</span>
    </div>
  </div>

  <!-- Text Editor Modal -->
  {#if showNotesModal}
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
    <div class="modal-overlay" on:click={() => { saveSessionNotes(); showNotesModal = false; }} on:keydown={(e) => e.key === 'Escape' && (saveSessionNotes(), showNotesModal = false)} role="dialog" aria-label="Session Notes" tabindex="-1">
      <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
      <div class="modal-content text-editor-modal" on:click|stopPropagation role="document">
        <div class="modal-header">
          <h3>🗒️ Session Notes</h3>
          <button class="modal-close" on:click={() => { saveSessionNotes(); showNotesModal = false; }}>✕</button>
        </div>
        <textarea
          class="text-editor-textarea"
          bind:value={sessionNotes}
          placeholder="Jot down anything you want to remember for next time…"
          spellcheck="true"
        ></textarea>
        <div class="modal-actions">
          <button class="btn btn-primary" on:click={() => { saveSessionNotes(); showNotesModal = false; }}>Save &amp; Close</button>
        </div>
      </div>
    </div>
  {/if}

  {#if showTextEditor}
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
    <div class="modal-overlay" on:click={() => showTextEditor = false} on:keydown={(e) => e.key === 'Escape' && (showTextEditor = false)} role="dialog" aria-label="Text Editor" tabindex="-1">
      <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
      <div class="modal-content text-editor-modal" on:click|stopPropagation role="document">
        <div class="modal-header">
          <h3>Ultrastar Text Editor</h3>
          <button class="modal-close" on:click={() => showTextEditor = false}>✕</button>
        </div>
        <textarea
          class="text-editor-textarea"
          bind:value={textEditorContent}
          spellcheck="false"
        ></textarea>
        <div class="modal-actions">
          <button class="btn btn-secondary" on:click={() => showTextEditor = false}>Cancel</button>
          <button class="btn btn-primary" on:click={applyTextEditorContent}>Apply Changes</button>
        </div>
      </div>
    </div>
  {/if}

  {#if segRegenModalOpen}
    {#if segRegenModalBlocking}
      <div class="seg-regen-global-blocker" aria-live="polite" aria-label={segRegenModalBlockingLabel}>
        <div class="loading-modal seg-regen-loading-modal">
          <span class="loading-spinner"></span>
          <span class="loading-label">{segRegenModalBlockingLabel}</span>
        </div>
      </div>
    {/if}

    <div
      class="seg-regen-modal"
      style="left:{segRegenModalX}px;top:{segRegenModalY}px"
      on:mousedown={segRegenModalMouseDown}
      role="dialog"
      aria-label="AI note generation"
      aria-busy={segRegenModalBlocking}
    >
      <div class="seg-regen-modal-title">🤖 AI Note Generation</div>
      <div class="seg-regen-modal-subtitle">
        Section · {formatTime(segRegenRange.startMs / 1000)} → {formatTime(segRegenRange.endMs / 1000)}
      </div>

      <div class="seg-regen-options-grid">
        <label>
          Language
          <select class="mic-select" bind:value={segRegenLanguage}>
            <option value="auto">Auto</option>
            {#each SUPPORTED_LANGUAGES as lang}
              <option value={lang.code}>{lang.label}</option>
            {/each}
          </select>
        </label>
        <label class="seg-regen-audio-label" title="important: select the adio source">
          <span class="seg-regen-audio-head">
            <span>Audio Source</span>
            <span class="seg-regen-audio-flag">!</span>
          </span>
          <select class="mic-select seg-regen-audio-select" bind:value={segRegenAudioSource}>
            <option value="vocals">Vocal{segRegenCurrentEditorSource === 'vocals' ? ' (current)' : ''}</option>
            <option value="edited" disabled={!cleanedAudioAvailable && segRecPatched.size === 0}>Edited{segRegenCurrentEditorSource === 'edited' ? ' (current)' : ''}</option>
          </select>
        </label>
      </div>

      <label class="seg-regen-toggle">
        <input type="checkbox" bind:checked={segRegenAutoHyphenate} />
        Auto-hyphenate after preview
      </label>
      <div class="seg-regen-actions">
        <button class="btn btn-primary" on:click={previewSegmentLyrics} disabled={segRegenPreviewLoading || segRegenHyphenateLoading || segRegenGenerateLoading}>Preview Lyrics</button>
        <button
          class="btn"
          disabled={segRegenPreviewLines.length === 0 || segRegenPreviewLoading || segRegenHyphenateLoading || segRegenGenerateLoading}
          on:click={async () => {
            if (segRegenPreviewLines.length === 0) {
              showToast('Run Preview Lyrics first');
              return;
            }
            segRegenPreviewLines = await hyphenateSegmentPreviewLines(segRegenPreviewLines);
          }}
        >Hyphenate</button>
        <button
          class="btn"
          disabled={segRegenPreviewLines.length === 0 || segRegenPreviewLoading || segRegenHyphenateLoading || segRegenGenerateLoading}
          on:click={generateNotesFromSegmentPreview}
        >{segRegenGenerateLoading ? 'Generating...' : 'Generate Notes'}</button>
      </div>
      <div class="seg-regen-preview">
        {#if segRegenPreviewLoading}
          <div class="seg-regen-preview-state">Recognizing lyrics...</div>
        {:else if segRegenHyphenateLoading}
          <div class="seg-regen-preview-state">Applying hyphenation...</div>
        {:else if segRegenPreviewError}
          <div class="seg-regen-preview-error">{segRegenPreviewError}</div>
        {:else if segRegenPreviewLines.length > 0}
          <div class="seg-regen-preview-head">
            <span>Preview {segRegenPreviewHyphenated ? '(hyphenated)' : '(raw)'}</span>
            {#if segRegenPreviewConfidence !== null}
              <span>Conf {(segRegenPreviewConfidence * 100).toFixed(0)}%</span>
            {/if}
          </div>
          <div class="seg-regen-preview-body">
            {#each segRegenPreviewLines as line}
              <div class="seg-regen-preview-line">{line}</div>
            {/each}
          </div>
        {:else}
          <div class="seg-regen-preview-state">No preview yet.</div>
        {/if}
      </div>

      <div class="seg-regen-footer">
        <button class="btn" on:click={closeSegmentRegenerateModal}>Close</button>
      </div>
    </div>
  {/if}

  {#if vibratoModalOpen}
    {@const vibratoNote = notes.find(n => n.id === vibratoNoteId && n.type !== 'break')}
    {#if vibratoNote}
      <div
        class="vibrato-modal"
        style="left:{vibratoModalX}px;top:{vibratoModalY}px"
        on:mousedown={vibratoModalMouseDown}
        role="dialog"
        aria-label="Vibrato tool"
      >
        <div class="seg-regen-modal-title">〰️ Vibrato Tool</div>
        <div class="seg-regen-modal-subtitle">
          Note · {formatTime(beatToTime(vibratoNote.startBeat))} → {formatTime(beatToTime(vibratoNote.startBeat + vibratoNote.duration))} · {noteName(vibratoNote.pitch)}
        </div>

        <label class="seg-regen-audio-label" title="important: select the audio source">
          <span class="seg-regen-audio-head">
            <span>Audio Source</span>
            <span class="seg-regen-audio-flag">!</span>
          </span>
          <select class="mic-select seg-regen-audio-select" bind:value={vibratoAudioSource}>
            <option value="vocals">Vocal{vibratoCurrentEditorSource === 'vocals' ? ' (current)' : ''}</option>
            <option value="edited" disabled={!cleanedAudioAvailable && segRecPatched.size === 0}>Edited{vibratoCurrentEditorSource === 'edited' ? ' (current)' : ''}</option>
          </select>
        </label>

        <label>
          Sensitivity
          <select class="mic-select" bind:value={vibratoSensitivity} disabled={vibratoLoading}>
            <option value="subtle">Subtle (capture small nuances)</option>
            <option value="balanced">Balanced</option>
            <option value="strict">Strict (clean-only)</option>
          </select>
        </label>

        <div class="seg-regen-actions">
          <button class="btn btn-primary" on:click={analyzeVibratoForSelectedNote} disabled={vibratoLoading}>
            {vibratoLoading ? 'Analyzing...' : 'Analyze Vibrato'}
          </button>
          <button class="btn" on:click={applyVibratoToSelectedNote} disabled={vibratoLoading || vibratoSegments.length < 2}>
            Apply Split
          </button>
        </div>

        <div class="seg-regen-preview">
          {#if vibratoLoading}
            <div class="seg-regen-preview-state">Analyzing pitch movement...</div>
          {:else if vibratoError}
            <div class="seg-regen-preview-error">{vibratoError}</div>
          {:else if vibratoSegments.length > 0}
            <div class="seg-regen-preview-head">
              <span>Preview ({vibratoSegments.length} slices)</span>
            </div>
            <div class="seg-regen-preview-body">
              {#each vibratoSegments as seg, i}
                <div class="seg-regen-preview-line">
                  {i + 1}. {formatTime(seg.start_sec)} → {formatTime(seg.end_sec)} · {noteName(seg.pitch)}
                </div>
              {/each}
            </div>
          {:else}
            <div class="seg-regen-preview-state">No vibrato analysis yet.</div>
          {/if}
        </div>

        <div class="seg-regen-footer">
          <button class="btn" on:click={closeVibratoModal}>Close</button>
        </div>
      </div>
    {/if}
  {/if}
</div>

<style>
  .step-content {
    max-width: 100%;
    margin: 0 auto;
  }

  .editor-busy-shield {
    position: fixed;
    inset: 0;
    z-index: 9800;
    background: rgba(0, 0, 0, 0.35);
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: all;
  }

  h2 { color: #4fc3f7; margin-bottom: 1rem; }

  /* ── Paste mode bar ── */
  .paste-mode-bar {
    display: flex;
    align-items: center;
    gap: 1rem;
    background: linear-gradient(90deg, #1a3a2a, #2a5a3a);
    border: 1px solid #4caf50;
    border-radius: 6px;
    padding: 6px 16px;
    margin: 4px 0;
    animation: paste-pulse 1.5s ease-in-out infinite alternate;
  }
  @keyframes paste-pulse {
    from { border-color: #4caf50; }
    to { border-color: #81c784; box-shadow: 0 0 8px rgba(76, 175, 80, 0.3); }
  }
  .paste-mode-text {
    color: #a5d6a7;
    font-size: 0.85rem;
    font-weight: 500;
  }
  .paste-cancel-btn {
    background: #c62828;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 3px 10px;
    font-size: 0.8rem;
    cursor: pointer;
    transition: background 0.2s;
  }
  .paste-cancel-btn:hover { background: #e53935; }

  /* ── Set GAP mode bar ── */
  .toast-bar {
    align-self: center;
    background: #1a2e1a;
    border: 1px solid #4caf50;
    border-radius: 6px;
    padding: 6px 16px;
    color: #a5d6a7;
    font-size: 0.85rem;
    animation: toast-fadein 0.15s ease;
  }
  .toast-bar.toast-center {
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    z-index: 9500;
    align-self: initial;
    background: rgba(15, 26, 15, 0.94);
    border: 1px solid #6bcf72;
    border-radius: 10px;
    padding: 10px 18px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
    font-size: 0.9rem;
    font-weight: 600;
    color: #d6f5d8;
    pointer-events: none;
  }
  @keyframes toast-fadein {
    from { opacity: 0; transform: translateY(4px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .setgap-mode-bar {
    display: flex;
    align-items: center;
    gap: 1rem;
    background: linear-gradient(90deg, #3a3a1a, #5a5a2a);
    border: 1px solid #ffd700;
    border-radius: 6px;
    padding: 6px 16px;
    margin: 4px 0;
    animation: setgap-pulse 1.5s ease-in-out infinite alternate;
  }
  @keyframes setgap-pulse {
    from { border-color: #ffd700; }
    to { border-color: #ffeb3b; box-shadow: 0 0 8px rgba(255, 215, 0, 0.3); }
  }
  .setgap-mode-text {
    color: #fff9c4;
    font-size: 0.85rem;
    font-weight: 500;
  }
  .setgap-cancel-btn {
    background: #c62828;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 0.8rem;
    cursor: pointer;
    transition: background 0.2s;
  }
  .setgap-cancel-btn:hover { background: #e53935; }

  /* ── BPM Tapper modal ── */
  .tapper-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.65);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .tapper-modal {
    background: #1e1e2e;
    border: 1px solid #444;
    border-radius: 12px;
    padding: 2rem 2.5rem;
    min-width: 320px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1rem;
    box-shadow: 0 8px 40px rgba(0,0,0,0.6);
    position: relative;
  }
  .tapper-close-btn {
    position: absolute;
    top: 0.6rem;
    right: 0.75rem;
    background: none;
    border: none;
    color: #888;
    font-size: 1rem;
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 4px;
    line-height: 1;
  }
  .tapper-close-btn:hover { color: #fff; background: #444; }
  .tapper-title {
    margin: 0;
    font-size: 1.2rem;
    color: #ccc;
    font-weight: 600;
  }
  .tapper-hint {
    margin: 0;
    font-size: 0.78rem;
    color: #888;
    text-align: center;
    line-height: 1.5;
  }
  .tapper-hint kbd {
    background: #333;
    border: 1px solid #555;
    border-radius: 3px;
    padding: 1px 5px;
    font-size: 0.75rem;
    color: #ccc;
  }
  .tapper-bpm {
    display: flex;
    align-items: baseline;
    gap: 0.4rem;
    min-height: 3.5rem;
  }
  .tapper-bpm-value {
    font-size: 3rem;
    font-weight: 700;
    color: #7ec8e3;
    line-height: 1;
  }
  .tapper-bpm-unit {
    font-size: 1.1rem;
    color: #888;
  }
  .tapper-bpm-waiting {
    font-size: 1.1rem;
    color: #555;
  }
  .tapper-count {
    font-size: 0.8rem;
    color: #666;
  }
  .tapper-tap-btn {
    width: 140px;
    height: 140px;
    border-radius: 50%;
    background: #2a4a6a;
    border: 3px solid #7ec8e3;
    color: #7ec8e3;
    font-size: 1.4rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    cursor: pointer;
    transition: background 0.08s, transform 0.08s;
    user-select: none;
  }
  .tapper-tap-btn:active {
    background: #3a6a9a;
    transform: scale(0.94);
  }
  .tapper-actions {
    display: flex;
    gap: 0.6rem;
    margin-top: 0.5rem;
  }
  .tapper-reset-btn { background: #6a3a00 !important; border-color: #ff8c00 !important; color: #ff8c00 !important; }
  .tapper-reset-btn:hover { background: #8a5000 !important; }

  .tapper-apply-row {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    justify-content: center;
    margin-top: 0.25rem;
  }
  .tapper-apply-btn {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 0.4rem 0.6rem;
    background: #2a3a4a;
    border: 1px solid #4a6a8a;
    border-radius: 6px;
    color: #9ec8e3;
    cursor: pointer;
    min-width: 56px;
    transition: background 0.1s;
  }
  .tapper-apply-btn:hover { background: #3a5a7a; }
  .tapper-apply-btn.primary {
    border-color: #7ec8e3;
    background: #2a4a6a;
  }
  .tapper-apply-btn.primary:hover { background: #3a6a9a; }
  .tapper-mult {
    font-size: 0.65rem;
    color: #7a9ab0;
    line-height: 1;
  }
  .tapper-mult-bpm {
    font-size: 0.9rem;
    font-weight: 600;
    line-height: 1.3;
  }

  /* ── Beat Marker Calibration bar ── */
  .beatcal-mode-bar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    background: linear-gradient(90deg, #1a2a1a, #2a3a2a);
    border: 1px solid #43a047;
    border-radius: 6px;
    padding: 6px 16px;
    margin: 4px 0;
    flex-wrap: wrap;
    animation: beatcal-pulse 1.5s ease-in-out infinite alternate;
  }
  @keyframes beatcal-pulse {
    from { border-color: #43a047; }
    to { border-color: #a5d6a7; box-shadow: 0 0 8px rgba(67,160,71,0.3); }
  }
  .beatcal-mode-text {
    color: #c8e6c9;
    font-size: 0.82rem;
    font-weight: 500;
  }
  .beatcal-result {
    color: #fff;
    font-size: 0.85rem;
    font-family: monospace;
  }
  .beatcal-result strong { color: #69f0ae; }
  .beatcal-hint {
    color: #81c784;
    font-size: 0.8rem;
    font-style: italic;
  }
  .beatcal-marker-list {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    align-items: center;
    max-width: 60vw;
  }
  .beatcal-marker-item {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    background: #1a2a1a;
    border: 1px solid #43a047;
    border-radius: 4px;
    padding: 1px 4px;
    font-size: 0.75rem;
    color: #a5d6a7;
    white-space: nowrap;
  }
  .beatcal-bar-input {
    width: 36px;
    padding: 0 2px;
    background: transparent;
    border: none;
    border-bottom: 1px solid #69f0ae;
    color: #69f0ae;
    font-family: monospace;
    font-size: 0.78rem;
    text-align: center;
    -moz-appearance: textfield;
    appearance: textfield;
  }
  .beatcal-bar-input::-webkit-inner-spin-button,
  .beatcal-bar-input::-webkit-outer-spin-button { -webkit-appearance: none; }
  .beatcal-marker-time { color: #888; font-size: 0.72rem; }
  .beatcal-rm-btn {
    background: none;
    border: none;
    color: #ef9a9a;
    cursor: pointer;
    padding: 0 2px;
    font-size: 0.85rem;
    line-height: 1;
  }
  .beatcal-rm-btn:hover { color: #ff5252; }
  .beatcal-apply-btn {
    background: #2e7d32;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 3px 12px;
    font-size: 0.8rem;
    cursor: pointer;
    transition: background 0.2s;
  }
  .beatcal-apply-btn:hover { background: #43a047; }
  .beatcal-cancel-btn {
    background: #c62828;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 0.8rem;
    cursor: pointer;
    transition: background 0.2s;
  }
  .beatcal-cancel-btn:hover { background: #e53935; }

  /* ── Grid Align mode bar ── */
  .gridalign-mode-bar {
    display: flex;
    align-items: center;
    gap: 1rem;
    background: linear-gradient(90deg, #2a1a3a, #3a2a5a);
    border: 1px solid #9c27b0;
    border-radius: 6px;
    padding: 6px 16px;
    margin: 4px 0;
    animation: gridalign-pulse 1.5s ease-in-out infinite alternate;
  }
  @keyframes gridalign-pulse {
    from { border-color: #9c27b0; }
    to { border-color: #ce93d8; box-shadow: 0 0 8px rgba(156, 39, 176, 0.3); }
  }
  .gridalign-mode-text {
    color: #e1bee7;
    font-size: 0.82rem;
    font-weight: 500;
  }
  .gridalign-mode-offset {
    color: #ffd700;
    font-size: 0.95rem;
    font-weight: bold;
    font-family: monospace;
    min-width: 80px;
    text-align: center;
  }
  .gridalign-confirm-btn {
    background: #2e7d32;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 3px 10px;
    font-size: 0.8rem;
    cursor: pointer;
    transition: background 0.2s;
  }
  .gridalign-confirm-btn:hover { background: #43a047; }
  .gridalign-cancel-btn {
    background: #c62828;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 3px 10px;
    font-size: 0.8rem;
    cursor: pointer;
    transition: background 0.2s;
  }
  .gridalign-cancel-btn:hover { background: #e53935; }

  /* ── Selection info bar ── */
  .selection-info-bar {
    display: flex;
    align-items: center;
    gap: 1.5rem;
    background: rgba(33, 150, 243, 0.1);
    border: 1px solid rgba(33, 150, 243, 0.3);
    border-radius: 6px;
    padding: 4px 16px;
    margin: 4px 0;
    color: #90caf9;
    font-size: 0.82rem;
  }
  .selection-hint {
    color: #607d8b;
    font-size: 0.75rem;
  }

  .toolbar {
    /* display: flex;
    align-items: center;
    gap: 1rem; */
    display: brock;
    padding: 1px;
    background: #1a1a2e;
    border: 1px solid #333;
    border-radius: 8px 8px 0 0;
    /* flex-wrap: wrap; */
  }

  .toolbar-toolset-wrapper {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    margin: 8px 8px 8px 8px;
  }

  .toolbar-toolset-wrapper > * {
    display: flex;
    align-items: center;
    flex-wrap: nowrap;
    gap: 10px;
  }

  /* .playback-controls, .zoom-controls {
    display: flex;
    align-items: center;
    gap: 0.25rem;
  } */

  #toggle-playback-btn {
    width: 82px;
  }

  .time-display {
    color: #4ade80;
    font-size: 14px;
    font-family: monospace;
    margin-left: 6px;
  }

  .seg-rec-modal {
    position: fixed;
    z-index: 9000;
    width: 260px;
    cursor: default;
    background: #1a2a1a;
    border: 2px solid #3a7a3a;
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.6);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .seg-rec-modal.seg-rec-recording {
    background: #2a1010;
    border-color: #c03030;
    animation: rec-pulse 1s ease-in-out infinite;
  }

  .seg-rec-modal-title {
    font-size: 0.95rem;
    font-weight: 700;
    color: #e0e0e0;
    cursor: grab;
    user-select: none;
  }

  .seg-rec-modal-info {
    font-size: 0.8rem;
    color: #9cba9c;
    font-family: monospace;
  }

  .seg-rec-mic-panel {
    display: flex;
    flex-direction: column;
    gap: 6px;
    background: rgba(8, 14, 8, 0.45);
    border: 1px solid #315531;
    border-radius: 8px;
    padding: 8px;
  }

  .seg-rec-mic-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .seg-rec-mic-label {
    min-width: 36px;
    font-size: 0.75rem;
    color: #b6d0b6;
    font-weight: 600;
  }

  .seg-rec-mic-select {
    flex: 1;
    max-width: none;
  }

  .seg-rec-mic-meter {
    position: relative;
    flex: 1;
    height: 10px;
    border-radius: 6px;
    border: 1px solid #506050;
    background: linear-gradient(90deg, #1b5e20 0 65%, #ef6c00 65% 85%, #b71c1c 85% 100%);
    overflow: hidden;
  }

  .seg-rec-mic-meter-fill {
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 0;
    background: rgba(180, 220, 180, 0.45);
    transition: width 80ms linear;
  }

  .seg-rec-mic-meter-fill.seg-rec-mic-meter-warm {
    background: rgba(255, 214, 118, 0.5);
  }

  .seg-rec-mic-meter-fill.seg-rec-mic-meter-hot {
    background: rgba(255, 105, 97, 0.58);
  }

  .seg-rec-mic-meter-live {
    position: absolute;
    top: -2px;
    bottom: -2px;
    width: 2px;
    background: #fff;
    box-shadow: 0 0 5px rgba(255, 255, 255, 0.8);
    transform: translateX(-1px);
  }

  .seg-rec-mic-status {
    width: 32px;
    text-align: center;
    font-size: 0.7rem;
    font-weight: 700;
    color: #9dd69d;
    letter-spacing: 0.03em;
  }

  .seg-rec-mic-status.seg-rec-mic-status-hot {
    color: #ff6b6b;
  }

  .seg-rec-mic-slider {
    flex: 1;
    width: auto;
  }

  .seg-rec-mic-gain-readout {
    width: 36px;
    text-align: right;
    font-size: 0.72rem;
    color: #d5e5d5;
    font-family: monospace;
  }

  .seg-rec-mic-warning {
    color: #ff9696;
    font-size: 0.72rem;
    line-height: 1.3;
    background: rgba(120, 0, 0, 0.2);
    border: 1px solid rgba(255, 120, 120, 0.35);
    border-radius: 5px;
    padding: 4px 6px;
  }

  .seg-rec-countdown-overlay {
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    z-index: 9100;
    font-size: 8rem;
    font-weight: 900;
    color: #f0c040;
    text-shadow: 0 0 40px rgba(240,192,64,0.8), 0 2px 8px rgba(0,0,0,0.9);
    pointer-events: none;
    line-height: 1;
  }

  .seg-rec-hint {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .seg-rec-modal-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 8px;
  }

  .seg-rec-modal-actions .tool-btn {
    flex: 1 1 auto;
  }

  .seg-rec-primary-action {
    border-color: #5da65d;
    box-shadow: inset 0 0 0 1px rgba(93, 166, 93, 0.2);
  }

  .seg-rec-primary-action:hover:not(:disabled) {
    background: #253625;
    border-color: #79bd79;
  }

  .seg-rec-hint {
    font-size: 0.75rem;
    color: #778;
    font-style: italic;
  }

  .seg-rec-panel {
    display: none; /* legacy — replaced by modal */
  }

  @keyframes rec-pulse {
    0%, 100% { border-color: #c03030; }
    50% { border-color: #ff6060; }
  }

  .seg-rec-label {
    color: #ccc;
    font-size: 0.85rem;
    font-weight: 600;
    white-space: nowrap;
  }

  .seg-rec-hint {
    color: #888;
    font-size: 0.78rem;
    font-style: italic;
  }

  #mic-controls-wrapper {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 400px;
    border: 1px solid #333;
    border-radius: 4px;
  }

  #_outer_wrapper {
    border-left: 1px solid #8c8c8c;
    padding-left: 10px;
  }

  #edit-controls-wrapper {
    display: flex;
    align-items: center;
    gap: 10px;
    border-left: 1px solid #8c8c8c;
    padding-left: 10px;
  }

  .mic-icon-wrap {
    position: relative;
    display: inline-block;
  }
  .mic-icon-wrap.mic-off::after {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: 130%;
    height: 2px;
    background: #f44;
    transform: translate(-50%, -50%) rotate(-45deg);
    border-radius: 1px;
    pointer-events: none;
  }

  .tool-btn {
    padding: 6px 8px;
    border: 1px solid #444;
    border-radius: 4px;
    background: #222;
    color: #ccc;
    cursor: pointer;
    font-size: 14px;
    outline: none;
  }

  .tool-btn:hover { background: #333; }

  #vocal_trace-controls-wrapper {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 175px;
    border: 1px solid #333;
    border-radius: 4px;
  }

  .zoom-label {
    color: #888;
    font-size: 0.8rem;
    min-width: 60px;
    text-align: center;
  }

  .info {
    flex: 1;
    text-align: right;
  }

  .note-info {
    color: #4fc3f7;
    font-family: 'Courier New', monospace;
    font-size: 0.8rem;
  }

  .canvas-container {
    position: relative;
    border: 1px solid #333;
    border-top: none;
    overflow: hidden;
    cursor: crosshair;
  }

  .active-mode-badge {
    position: absolute;
    top: 8px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 3px 8px 3px 6px;
    border-radius: 12px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    pointer-events: none;
    user-select: none;
  }
  .badge-mic {
    background: rgba(220, 30, 30, 0.18);
    color: #ff4444;
    border: 1px solid rgba(220, 30, 30, 0.4);
  }
  .badge-vocal {
    background: rgba(255, 80, 180, 0.18);
    color: rgba(255, 80, 180, 1);
    border: 1px solid rgba(255, 80, 180, 0.4);
  }
  .badge-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    animation: badge-pulse 1.2s ease-in-out infinite;
  }
  .badge-mic .badge-dot  { background: #ff4444; }
  .badge-vocal .badge-dot { background: rgba(255, 80, 180, 1); }
  @keyframes badge-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%       { opacity: 0.4; transform: scale(0.7); }
  }

  canvas {
    display: block;
    width: 100%;
  }

  .mic-starting-overlay {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    z-index: 10;
  }

  .mic-starting-box {
    background: rgba(13, 17, 23, 0.85);
    border: 1px solid #4fc3f7;
    border-radius: 8px;
    padding: 12px 24px;
    color: #ccc;
    font-size: 0.9rem;
    animation: mic-pulse 1.2s ease-in-out infinite;
  }

  @keyframes mic-pulse {
    0%, 100% { opacity: 0.7; }
    50% { opacity: 1; }
  }

  .scrollbar-container {
    padding: 0 0 3px 0;
    background: #12121e;
    border: 1px solid #333;
    border-top: none;
  }

  .scrollbar-track {
    position: relative;
    height: 38px;
    cursor: pointer;
    /* visible rail in the vertical center */
    background: linear-gradient(
      to bottom,
      transparent 6px,
      #1a1a2e      6px,
      #1a1a2e      13px,
      transparent  13px
    );
  }

  .scrollbar-cleanup-lane {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 1px;
    height: 8px;
    pointer-events: none;
    z-index: 1;
  }

  .scrollbar-cleanup-seg {
    position: absolute;
    top: 0;
    bottom: 0;
    background: #ff6b6b;
  }

  .scrollbar-cleanup-seg.patched {
    background: #80e080;
  }

  .scrollbar-handle {
    position: absolute;
    top: 38%;
    width: 14px;
    height: 14px;
    background: #4fc3f7;
    border-radius: 50%;
    transform: translate(-50%, -50%);
    cursor: grab;
    box-shadow: 0 0 4px rgba(79, 195, 247, 0.6);
    pointer-events: none; /* track handles the pointer events */
    z-index: 4;
  }

  .scrollbar-playhead {
    position: absolute;
    top: 0;
    bottom: 10px;
    width: 2px;
    background: #ff4444;
    pointer-events: none;
    transform: translateX(-50%);
    opacity: 0.85;
    z-index: 3;
  }

  .scrollbar-flag {
    position: absolute;
    top: 0;
    bottom: 10px;
    width: 2px;
    background: repeating-linear-gradient(
      to bottom,
      #4ade80 0 2px,
      transparent 2px 5px
    );
    pointer-events: none;
    transform: translateX(-50%);
    opacity: 0.75;
    z-index: 2;
  }

  .scrollbar-break {
    position: absolute;
    top: 0;
    bottom: 10px;
    width: 2px;
    background: repeating-linear-gradient(
      to bottom,
      #ff6b6b 0 2px,
      transparent 2px 5px
    );
    pointer-events: none;
    transform: translateX(-50%);
    opacity: 0.85;
    z-index: 2;
  }

  .legend {
    display: flex;
    gap: 1.5rem;
    padding: 0.5rem;
    background: #1a1a2e;
    border: 1px solid #333;
    border-top: none;
    border-radius: 0 0 8px 8px;
    font-size: 0.8rem;
    color: #888;
  }

  .legend-item {
    display: flex;
    align-items: center;
    gap: 0.3rem;
  }

  .dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 2px;
  }

  .dot.blue { background: #4fc3f7; }
  .dot.yellow { background: #fdd835; }
  .dot.gold { background: #ffd700; }
  .dot.orange { background: #ff9800; }
  .dot.red-line {
    background: repeating-linear-gradient(
      to bottom,
      #c62828 0 2px,
      transparent 2px 5px
    );
    width: 2px;
    height: 12px;
  }
  .dot.green-flag {
    background: repeating-linear-gradient(
      to bottom,
      #4ade80 0 2px,
      transparent 2px 5px
    );
    width: 2px;
    height: 12px;
  }
  .dot.cleanup-range { background: #ff6b6b; }
  .dot.recorded-range { background: #80e080; }

  .save-controls {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    border-left: 1px solid #333;
    padding-left: 0.5rem;
  }

  /* .save-btn {
    background: #2e7d32 !important;
  }
  .save-btn:hover:not(:disabled) {
    background: #388e3c !important;
  }

  .unsaved-indicator {
    color: #ffa726;
    font-size: 0.75rem;
    font-weight: bold;
  }

  .saved-indicator {
    color: #66bb6a;
    font-size: 0.7rem;
  } */

  .mode-controls {
    font-size: 14px;
    color: #aaa;
    border-left: 1px solid #333;
    padding-left: 8px;
  }

  .mode-controls label {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  #time-display-wrapper {
    height: 28px;
    border-left: 1px solid #8c8c8c;
    padding-left: 4px;
    padding-top: 5px;
  }

  .speed-controls {
    display: flex;
    height: 28px;
    align-items: center;
    gap: 10px;
    border-left: 1px solid #8c8c8c;
    padding-left: 8px;
  }

  .speed-label {
    color: #e0e0e0;
    font-size: 14px;
    font-weight: 400;
    letter-spacing: 0.5px;
  }

  .tool-btn.sm.active {
    /* background: #4fc3f7;
    color: #0d1117; */
    border-color: #4fc3f7;
  }

  .tool-btn.active {
    /* background: #4fc3f7;
    color: #0d1117; */
    border-color: #4fc3f7;
  }

  .bpm-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    padding-left: 10px;
    padding-right: 6px;
    border-left: 1px solid #8c8c8c;
    height: 28px;
  }

  #gap-controls {
    border-left: 1px solid #8c8c8c;
    padding-left: 0px;
    padding-top: 2px;
    height: 28px;
  }

  .bpm-label {
    color: #aaa;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.5px;
  }

  .gap-label {
    margin-left: 10px;
  }

  .bpm-input, .gap-input {
    /* width: 120px; */
    padding: 4px 6px;
    background: #1a1a2e;
    border: 1px solid #444;
    border-radius: 4px;
    color: #4fc3f7;
    font-family: monospace;
    font-size: 12px;
    text-align: center;
    -moz-appearance: textfield;
    appearance: textfield;
  }

  .gap-display {
    cursor: pointer;
    user-select: none;
    display: inline-block;
  }
  .gap-display:hover {
    color: #81d4fa;
    border-color: #4fc3f7;
  }

  .bpm-input::-webkit-inner-spin-button,
  .bpm-input::-webkit-outer-spin-button,
  .gap-input::-webkit-inner-spin-button,
  .gap-input::-webkit-outer-spin-button {
    -webkit-appearance: none;
    margin: 0;
  }

  .bpm-input:focus, .gap-input:focus {
    outline: none;
    border-color: #4fc3f7;
  }

  .tool-btn.sm {
    padding: 4px 6px;
    font-size: 14px;
    min-width: 22px;
  }

  .tool-btn.sm.nudge {
    opacity: 0.75;
    font-size: 10px;
    padding: 2px 4px;
    min-width: 28px;
  }
  .tool-btn.sm.nudge:hover { opacity: 1; }

  .wave-height-slider {
    width: 64px;
    height: 4px;
    cursor: pointer;
    accent-color: #4fc3f7;
    pointer-events: all;
    opacity: 1;
    -webkit-appearance: none;
    appearance: none;
    background: rgba(79, 195, 247, 0.25);
    border-radius: 2px;
    outline: none;
  }
  .wave-height-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #4fc3f7;
    cursor: pointer;
    border: none;
    box-shadow: 0 1px 3px rgba(0,0,0,0.4);
    margin-top: -5px;
  }
  .wave-height-slider::-webkit-slider-runnable-track {
    height: 4px;
    background: rgba(79, 195, 247, 0.25);
    border-radius: 2px;
  }

  .wave-height-overlay {
    position: absolute;
    top: 20px;
    right: 10px;
    pointer-events: all;
    cursor: pointer;
    outline: none;
  }

  .zoom-overlay-slider {
    position: absolute;
    bottom: 34px;
    right: 10px;
    width: 160px;
    height: 4px;
    cursor: pointer;
    accent-color: #4fc3f7;
    pointer-events: all;
    opacity: 1;
    -webkit-appearance: none;
    appearance: none;
    background: rgba(79, 195, 247, 0.25);
    border-radius: 2px;
    outline: none;
  }
  .zoom-overlay-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #4fc3f7;
    cursor: pointer;
    box-shadow: 0 0 4px rgba(79, 195, 247, 0.6);
  }
  .zoom-overlay-slider::-moz-range-thumb {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #4fc3f7;
    border: none;
    cursor: pointer;
    box-shadow: 0 0 4px rgba(79, 195, 247, 0.6);
  }

  .wave-height-row {
    display: flex;
    justify-content: flex-end;
    padding: 2px 4px 0;
  }

  .shortcut-bar {
    display: flex;
    justify-content: center;
    gap: 1.5rem;
    margin-top: 0.5rem;
    padding: 0.5rem 1rem;
    background: #0a0e14;
    border: 1px solid #1e2433;
    border-radius: 8px;
    flex-wrap: wrap;
  }

  .shortcut-group {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .shortcut-label {
    color: #4fc3f7;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-right: 0.15rem;
  }

  .shortcut {
    color: #667;
    font-size: 0.75rem;
    white-space: nowrap;
  }

  .shortcut kbd {
    display: inline-block;
    background: #1a1f2b;
    border: 1px solid #2a3040;
    border-radius: 3px;
    padding: 0px 4px;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 0.7rem;
    color: #aab;
    margin-right: 2px;
    line-height: 1.5;
  }

  .stats-bar {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 0.5rem 0.75rem;
    background: #111122;
    border: 1px solid #333;
    border-top: none;
    font-family: 'Courier New', monospace;
    font-size: 0.75rem;
    color: #888;
  }
  .stats-bar span:first-child { color: #4fc3f7; }
  .stats-bar span:nth-child(2) { color: #66bb6a; }

  /* ── Context Menu ── */
  .context-menu {
    position: fixed;
    z-index: 1000;
    min-width: 200px;
    background: #1a1a2e;
    border: 1px solid #444;
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
    padding: 4px 0;
    font-size: 0.85rem;
  }

  .ctx-multi-label {
    color: #4fc3f7;
    font-size: 0.8rem;
    font-weight: 600;
    padding: 2px 0;
  }

  .ctx-header {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    padding: 6px 10px;
  }

  .ctx-syllable-wrapper {
    flex: 1;
    position: relative;
    display: flex;
    background: #0d1117;
    border-radius: 4px;
  }
  .ctx-syllable-highlight {
    position: absolute;
    inset: 0;
    padding: 4px 6px;
    font-family: monospace;
    font-size: 0.85rem;
    color: transparent;
    white-space: pre;
    pointer-events: none;
    border: 1px solid transparent;
    border-radius: 4px;
    overflow: hidden;
  }
  .ctx-syllable-highlight :global(.spc) {
    color: #7ecbf7;
  }
  .ctx-syllable-input {
    flex: 1;
    background: transparent;
    border: 1px solid #444;
    border-radius: 4px;
    color: #eee;
    font-family: monospace;
    font-size: 0.85rem;
    padding: 4px 6px;
    outline: none;
    position: relative;
    caret-color: #eee;
  }

  .ctx-syllable-input:focus {
    border-color: #4fc3f7;
  }

  .ctx-pitch {
    color: #888;
    font-family: monospace;
    font-size: 0.75rem;
    white-space: nowrap;
  }

  .ctx-divider {
    height: 1px;
    background: #333;
    margin: 2px 0;
  }

  .ctx-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 6px 10px;
    background: transparent;
    border: none;
    color: #ccc;
    cursor: pointer;
    font-size: 0.83rem;
    text-align: left;
  }

  .ctx-item:hover {
    background: #2a2a4e;
  }

  .ctx-item:disabled {
    opacity: 0.45;
    cursor: default;
    pointer-events: none;
  }

  .ctx-item:disabled .ctx-shortcut {
    opacity: 0.65;
  }

  .ctx-item.danger {
    color: #ef5350;
  }

  .ctx-item.danger:hover {
    background: #3a1a1a;
  }

  .ctx-shortcut {
    color: #666;
    font-size: 0.75rem;
    font-family: monospace;
    margin-left: 1rem;
  }

  .ctx-type-group {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 4px 10px;
  }

  .ctx-checkbox {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    color: #ccc;
    font-size: 0.8rem;
    cursor: pointer;
    border-radius: 4px;
  }
  .ctx-checkbox.space-on {
    background: #1b5e2022;
    color: #66bb6a;
  }
  .ctx-checkbox.space-off {
    background: #b7161622;
    color: #ef5350;
  }
  .ctx-checkbox.space-on input[type="checkbox"] {
    accent-color: #66bb6a;
    cursor: pointer;
  }
  .ctx-checkbox.space-off input[type="checkbox"] {
    accent-color: #ef5350;
    cursor: pointer;
  }

  .ctx-type-label {
    color: #888;
    font-size: 0.75rem;
    margin-right: 4px;
  }

  .ctx-type-btn {
    padding: 3px 8px;
    border: 1px solid #444;
    border-radius: 4px;
    background: #222;
    color: #ccc;
    cursor: pointer;
    font-size: 0.75rem;
  }

  .ctx-type-btn:hover {
    background: #333;
  }

  .ctx-type-btn.active {
    background: #4fc3f7;
    color: #0d1117;
    border-color: #4fc3f7;
    font-weight: bold;
  }

  .ctx-type-btn.golden.active {
    background: #ffd700;
    border-color: #ffd700;
  }

  .ctx-type-btn.rap.active {
    background: #ff9800;
    border-color: #ff9800;
  }

  .ctx-break-label {
    color: #ef5350;
    font-family: monospace;
    font-size: 0.8rem;
    font-weight: 600;
  }

  .ctx-location-label {
    color: #aaa;
    font-family: monospace;
    font-size: 0.8rem;
  }

  .ctx-trace-swatch {
    display: inline-block;
    width: 10px;
    height: 10px;
    background: rgba(255, 80, 180, 0.75);
    border-radius: 2px;
    vertical-align: middle;
    margin: 0 2px;
  }
  .ctx-trace-label {
    font-family: monospace;
    font-size: 0.85em;
    color: rgba(255, 80, 180, 0.9);
  }
  .ctx-item-trace {
    font-weight: 500;
  }

  #audio-source-wrapper {
    display: block;
    padding: 1px 1px 1px 8px;
    border-left: 1px solid #8c8c8c;
  }

  /* Audio source toggle */
  .audio-source-toggle {
    display: inline-flex;
    gap: 10px;
    padding-left: 10px;
    border-radius: 6px;
    padding: 1px;
  }

  #midi-wrapper {
    display: block;
    padding: 1px 1px 1px 10px;
    border-left: 1px solid #8c8c8c;
  }
  #midi-wrapper>button {
    padding: 4px 6px;
    font-size: 14px;
    border: 1px solid #444;
    border-radius: 4px;
    background: #222;
    color: #ccc;
    cursor: pointer;
    outline: none;
  }

  #midi-wrapper>button.active {
    border-color: #4fc3f7;
  }

  #midi-wrapper>button:hover {
    background: #333;
  }

  #metronome-wrapper {
    display: block;
    padding: 1px 1px 1px 10px;
    border-left: 1px solid #8c8c8c;
  }

  #metronome-wrapper>button {
    padding: 4px 6px;
    font-size: 14px;
    border: 1px solid #444;
    border-radius: 4px;
    background: #222;
    color: #ccc;
    cursor: pointer;
    outline: none;
  }

  #metronome-wrapper>button.active {
    border-color: #4fc3f7;
  }

  #metronome-wrapper>button:hover {
    background: #333;
  }

  .volume-control {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    margin-left: 4px;
  }

  .volume-icon {
    font-size: 14px;
    cursor: default;
    user-select: none;
  }

  .volume-slider {
    width: 60px;
    height: 4px;
    -webkit-appearance: none;
    appearance: none;
    background: #333;
    border-radius: 2px;
    outline: none;
    cursor: pointer;
  }

  .volume-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: #4fc3f7;
    cursor: pointer;
  }

  .volume-slider::-moz-range-thumb {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: #4fc3f7;
    cursor: pointer;
    border: none;
  }

  .mic-level {
    display: inline-block;
    width: 8px;
    height: 20px;
    background: #333;
    border: 1px solid #555;
    border-radius: 3px;
    position: relative;
    overflow: hidden;
    vertical-align: middle;
  }

  .mic-level-bar {
    position: absolute;
    bottom: 0;
    left: 0;
    width: 100%;
    background: #4caf50;
    border-radius: 2px;
    transition: height 0.05s linear;
  }

  .mic-level-warm {
    background: #ff9800;
  }

  .mic-level-hot {
    background: #f44336;
  }

  .mic-gain-slider {
    width: 50px;
    height: 4px;
    -webkit-appearance: none;
    appearance: none;
    background: #444;
    border-radius: 2px;
    outline: none;
    vertical-align: middle;
    cursor: pointer;
  }

  .mic-gain-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #4fc3f7;
    cursor: pointer;
  }

  .mic-select {
    background: #222;
    color: #ccc;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 2px 4px;
    font-size: 12px;
    max-width: 80px;
    cursor: pointer;
    outline: none;
    appearance: none;
    -webkit-appearance: none;
  }

  .mic-opt {
    font-size: 12px;
    color: #ccc;
    cursor: pointer;
    user-select: none;
  }

  .tool-btn.disabled-audio,
  .tool-btn:disabled,
  .bpm-input.disabled-audio,
  .bpm-input:disabled,
  .gap-display.disabled-audio {
    opacity: 0.4;
    cursor: not-allowed !important;
    filter: saturate(0.45);
  }

  .tool-btn.disabled-audio:hover,
  .tool-btn:disabled:hover {
    opacity: 0.4;
    background: #222;
  }

  .gap-display.disabled-audio:hover {
    color: #4fc3f7;
    border-color: #444;
  }

  label.disabled-label {
    opacity: 0.35;
    cursor: not-allowed;
  }

  /* Text editor modal */
  .loading-modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }

  .loading-modal {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 1.5rem 2rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1rem;
    min-width: 220px;
  }

  .loading-spinner {
    width: 32px;
    height: 32px;
    border: 3px solid #30363d;
    border-top-color: #58a6ff;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .loading-label {
    color: #c9d1d9;
    font-size: 0.9rem;
  }

  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.7);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }

  .modal-content {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 1.5rem;
    max-width: 90vw;
    max-height: 90vh;
    display: flex;
    flex-direction: column;
  }

  .text-editor-modal {
    width: 800px;
    height: 80vh;
  }

  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
  }

  .modal-header h3 {
    margin: 0;
    color: #4fc3f7;
    font-size: 1.1rem;
  }

  .modal-close {
    background: none;
    border: none;
    color: #888;
    font-size: 1.2rem;
    cursor: pointer;
    padding: 0.25rem 0.5rem;
    border-radius: 4px;
  }

  .modal-close:hover {
    color: #ef5350;
    background: rgba(239, 83, 80, 0.1);
  }

  .text-editor-textarea {
    flex: 1;
    width: 100%;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 0.85rem;
    line-height: 1.5;
    background: #0d1117;
    color: #e0e0e0;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 1rem;
    resize: none;
    outline: none;
    tab-size: 4;
  }

  .text-editor-textarea:focus {
    border-color: #4fc3f7;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 1rem;
  }

  .seg-regen-modal {
    position: fixed;
    z-index: 9050;
    width: 390px;
    cursor: default;
    background: #111c22;
    border: 2px solid #2b7084;
    border-radius: 10px;
    padding: 12px 14px;
    box-shadow: 0 4px 22px rgba(0, 0, 0, 0.6);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .vibrato-modal {
    position: fixed;
    z-index: 9051;
    width: 390px;
    cursor: default;
    background: #111c22;
    border: 2px solid #2b7084;
    border-radius: 10px;
    padding: 12px 14px;
    box-shadow: 0 4px 22px rgba(0, 0, 0, 0.6);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .metronome-tool-modal {
    position: fixed;
    z-index: 9055;
    width: 286px;
    cursor: default;
    background: #161923;
    border: 2px solid #4f7aa1;
    border-radius: 10px;
    padding: 10px 12px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.55);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .metronome-tool-title {
    font-size: 0.9rem;
    font-weight: 700;
    color: #cfe8ff;
    cursor: grab;
    user-select: none;
  }

  .metronome-tool-row {
    display: flex;
    gap: 6px;
  }

  .metronome-tool-row .tool-btn {
    flex: 1;
    font-size: 0.78rem;
    padding: 4px 6px;
  }

  .metronome-signature-row {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }

  .metronome-signature-row .mic-select {
    min-width: 88px;
    text-align: center;
  }

  .metronome-signature-slash {
    color: #9fc2e6;
    font-weight: 700;
  }

  .metronome-signature-hint {
    font-size: 0.74rem;
    color: #9fc2e6;
  }

  .metronome-signature-warning {
    font-size: 0.74rem;
    color: #ffb4a8;
    background: rgba(179, 38, 30, 0.2);
    border: 1px solid rgba(255, 140, 127, 0.4);
    border-radius: 6px;
    padding: 4px 6px;
  }

  .metronome-speed-row {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }

  .metronome-disabled {
    opacity: 0.4;
    pointer-events: none;
  }

  .metronome-no-downbeat-hint {
    font-size: 0.72rem;
    color: #f0a040;
    text-align: center;
    margin: 2px 0 4px;
    line-height: 1.3;
  }

  .metronome-speed-value {
    min-width: 92px;
    text-align: center;
    font-size: 0.78rem;
    color: #cfe8ff;
    font-weight: 600;
  }

  .seg-regen-global-blocker {
    position: fixed;
    inset: 0;
    z-index: 9100;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(7, 13, 16, 0.62);
    pointer-events: all;
  }

  .seg-regen-loading-modal {
    padding: 1rem 1.2rem;
    min-width: 200px;
  }

  .seg-regen-modal-title {
    font-size: 0.95rem;
    font-weight: 700;
    color: #b6e8ff;
    cursor: grab;
    user-select: none;
  }

  .seg-regen-modal-subtitle {
    font-size: 0.78rem;
    color: #8cc7da;
    font-family: monospace;
  }

  .seg-regen-mode-row {
    display: flex;
    gap: 8px;
  }

  .seg-regen-options-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .seg-regen-options-grid label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 0.75rem;
    color: #8ab8c7;
  }

  .seg-regen-audio-label {
    background: linear-gradient(180deg, rgba(255, 187, 0, 0.14), rgba(255, 187, 0, 0.06));
    border: 1px solid rgba(255, 187, 0, 0.45);
    border-radius: 7px;
    padding: 6px;
  }

  .seg-regen-audio-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: #ffd666;
    font-weight: 700;
  }

  .seg-regen-audio-flag {
    width: 16px;
    height: 16px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.72rem;
    font-weight: 800;
    background: #ffb300;
    color: #261a00;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35);
  }

  .seg-regen-audio-select {
    border-color: #ffb300;
    box-shadow: 0 0 0 1px rgba(255, 187, 0, 0.25) inset;
    max-width: 110px;
  }

  .seg-regen-mode-help {
    font-size: 0.77rem;
    color: #9ab5bf;
    line-height: 1.35;
    background: rgba(0, 0, 0, 0.18);
    border: 1px solid rgba(139, 197, 214, 0.2);
    border-radius: 6px;
    padding: 7px 8px;
  }

  .seg-regen-toggle {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 0.77rem;
    color: #9ec1cd;
    user-select: none;
  }

  .seg-regen-toggle input {
    accent-color: #4fc3f7;
  }

  .seg-regen-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .seg-regen-preview {
    border: 1px solid rgba(139, 197, 214, 0.25);
    border-radius: 6px;
    background: rgba(7, 13, 16, 0.75);
    padding: 8px;
    min-height: 82px;
  }

  .seg-regen-preview-head {
    display: flex;
    justify-content: space-between;
    color: #8cc7da;
    font-size: 0.74rem;
    margin-bottom: 6px;
  }

  .seg-regen-preview-body {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 120px;
    overflow: auto;
  }

  .seg-regen-preview-line {
    color: #d5e7ee;
    font-size: 0.8rem;
    line-height: 1.25;
  }

  .seg-regen-preview-state {
    color: #8faab5;
    font-size: 0.78rem;
  }

  .seg-regen-preview-error {
    color: #ff9f9f;
    font-size: 0.78rem;
    line-height: 1.25;
  }

  .seg-regen-footer {
    display: flex;
    justify-content: flex-end;
  }

  .btn {
    padding: 4px 12px;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    cursor: pointer;
    transition: all 0.15s;
  }

  .btn-secondary {
    background: #21262d;
    color: #ccc;
    border: 1px solid #30363d;
  }

  .btn-secondary:hover {
    background: #30363d;
  }

  .btn-primary {
    background: #238636;
    color: #fff;
  }

  .btn-primary:hover {
    background: #2ea043;
  }
</style>
