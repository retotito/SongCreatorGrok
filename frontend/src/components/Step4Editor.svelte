<script>
  import { onMount, onDestroy } from 'svelte';
  import { sessionId, generationResult, editorState, referenceData, errorMessage } from '../stores/appStore.js';
  import { getEditorData, getAudioUrl, getReferenceNotes, applyBpm } from '../services/api.js';

  // Canvas refs
  let canvasEl;
  let ctx;

  // Data
  let notes = [];
  let referenceNotes = [];
  let showReference = true;
  let bpm = 272;
  let gapMs = 0;
  let audioDuration = 0;
  let vocalUrl = '';

  // Reference metadata (for coordinate conversion)
  let refBpm = 0;
  let refGapMs = 0;

  // Raw ms timings for BPM re-quantization
  let rawTimings = [];   // syllable_timings from backend (start/end in seconds)
  let pitchMap = [];     // midi pitch per syllable (extracted from initial Ultrastar parse)
  let initialBpm = 0;    // original detected BPM
  let initialGap = 0;    // original detected GAP
  let bpmChanged = false; // track if user modified BPM/GAP
  let applyingBpm = false; // loading state for Apply button

  // View state
  let scrollX = 0;
  let zoom = 20;          // pixels per beat (default zoomed in)
  let viewHeight = 460;
  let noteHeight = 8;

  // Pitch range (MIDI)
  let minPitch = 36;     // C2
  let maxPitch = 96;     // C7

  // Interaction
  let selectedNote = null;
  let dragMode = null;     // 'move' | 'resize-left' | 'resize-right'
  let dragStart = { x: 0, y: 0 };
  let isDragging = false;
  let scrollBarEl;

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

  // Waveform
  let waveformPeaks = [];   // pre-computed peaks array
  let showWaveform = true;
  const waveformHeight = 60; // px reserved at top of canvas for waveform

  // Total beats for scrollbar
  let totalBeats = 0;

  // Parse Ultrastar content into notes array
  function parseUltrastar(content) {
    const lines = content.split('\n');
    const parsed = [];
    let id = 0;

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith(':') || trimmed.startsWith('F:')) {
        const isRap = trimmed.startsWith('F:');
        const prefix = isRap ? 'F:' : ':';
        const parts = trimmed.substring(prefix.length).trim().split(/\s+/);
        
        if (parts.length >= 4) {
          const startBeat = parseInt(parts[0]);
          const duration = parseInt(parts[1]);
          const pitch = parseInt(parts[2]);
          const syllable = parts.slice(3).join(' ');

          parsed.push({
            id: id++,
            startBeat,
            duration,
            pitch,
            syllable,
            isRap,
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
    // Also include reference pitches (after offset normalization) for range calc
    if (referenceNotes.length > 0) {
      const refPitches = referenceNotes.filter(n => n.pitch !== undefined && n.type !== 'break').map(n => n.pitch);
      pitches.push(...refPitches);
    }
    minPitch = Math.max(24, Math.min(...pitches) - 6);
    maxPitch = Math.min(108, Math.max(...pitches) + 6);
  }

  // Compute median of a numeric array
  function median(arr) {
    if (arr.length === 0) return 0;
    const sorted = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
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
    return showWaveform ? waveformHeight : 0;
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
    return `${names[midi % 12]}${Math.floor(midi / 12) - 1}`;
  }

  // Beat to time (seconds) conversion
  // Standard Ultrastar: beats are quarter-beats, so time = gap + beat * 15 / BPM
  function beatToTime(beat) {
    const gapSec = gapMs / 1000;
    return gapSec + (beat * 15) / bpm;
  }

  // Time to beat conversion
  function timeToBeat(timeSec) {
    const gapSec = gapMs / 1000;
    return ((timeSec - gapSec) * bpm) / 15;
  }

  // Format seconds as m:ss
  function formatTime(seconds) {
    if (seconds < 0) seconds = 0;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
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
    if (scrollBarEl) {
      scrollBarEl.value = scrollX / zoom;
    }
  }

  // ──── BPM Re-quantization ────────────────────
  // Rebuild notes from raw ms timings using the current bpm/gapMs,
  // preserving pitches from the original Ultrastar parse.
  function requantizeFromMs() {
    if (!rawTimings || rawTimings.length === 0) {
      console.log('[Requantize] No rawTimings, skipping');
      return;
    }
    console.log(`[Requantize] bpm=${bpm} gap=${gapMs}ms, ${rawTimings.length} timings`);

    const gapSec = gapMs / 1000;
    let id = 0;
    let prevLineIndex = null;
    let lastEndBeat = 0;
    const newNotes = [];

    for (let i = 0; i < rawTimings.length; i++) {
      const timing = rawTimings[i];
      const startSec = timing.start;
      const endSec = timing.end;
      const lineIndex = timing.line_index ?? 0;

      // Insert break between phrases
      if (prevLineIndex !== null && lineIndex !== prevLineIndex) {
        const breakStart = lastEndBeat + 2;
        const nextStartBeat = Math.max(0, Math.round(((startSec - gapSec) * bpm) / 15));
        const breakEnd = Math.max(breakStart + 1, nextStartBeat - 2);
        if (breakEnd > breakStart) {
          newNotes.push({ id: id++, type: 'break', startBeat: breakStart, endBeat: breakEnd });
        } else {
          newNotes.push({ id: id++, type: 'break', startBeat: breakStart, endBeat: null });
        }
      }

      let startBeat = Math.max(0, Math.round(((startSec - gapSec) * bpm) / 15));
      let endBeat   = Math.max(startBeat + 1, Math.round(((endSec - gapSec) * bpm) / 15));

      // Prevent overlap with previous note
      if (startBeat <= lastEndBeat && newNotes.some(n => n.type !== 'break')) {
        startBeat = lastEndBeat + 1;
        endBeat = Math.max(startBeat + 1, endBeat);
      }

      const duration = Math.max(1, endBeat - startBeat);
      const pitch = pitchMap[i] ?? 60;

      newNotes.push({
        id: id++,
        startBeat,
        duration,
        pitch,
        syllable: timing.syllable,
        isRap: timing.is_rap ?? false,
        confidence: timing.confidence ?? 1.0,
        original: { startBeat, duration, pitch },
      });

      lastEndBeat = startBeat + duration;
      prevLineIndex = lineIndex;
    }

    notes = newNotes;
    console.log(`[Requantize] Built ${newNotes.filter(n => n.type !== 'break').length} notes, ${newNotes.filter(n => n.type === 'break').length} breaks`);
    updatePitchRange();
    computeTotalBeats();
    draw();
  }

  // Handle BPM or GAP adjustment — re-quantize visually
  function handleBpmGapChange() {
    console.log(`[BPM/GAP] bpm=${bpm} gap=${gapMs} (initial: bpm=${initialBpm} gap=${initialGap})`);
    bpmChanged = (bpm !== initialBpm || gapMs !== initialGap);
    // Recalculate playback cursor position with new BPM/GAP
    if (currentTimeSec > 0) {
      const gapSec = gapMs / 1000;
      playbackBeat = Math.max(0, ((currentTimeSec - gapSec) * bpm) / 15);
    }
    requantizeFromMs();
  }

  // Apply BPM to backend (regenerate Ultrastar file)
  async function handleApplyBpm() {
    if (!$sessionId) return;
    applyingBpm = true;
    try {
      const result = await applyBpm($sessionId, bpm, gapMs);
      // Update generationResult store so Step 5 gets correct BPM/GAP
      generationResult.update(r => ({ ...r, bpm, gap_ms: gapMs }));
      initialBpm = bpm;
      initialGap = gapMs;
      bpmChanged = false;
      // Re-parse the returned Ultrastar content to get updated notes
      if (result.ultrastar_content) {
        notes = parseUltrastar(result.ultrastar_content);
        updatePitchRange();
        draw();
      }
    } catch (err) {
      console.error('[Step4] Apply BPM error:', err);
      errorMessage.set(err.message);
    } finally {
      applyingBpm = false;
    }
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
    // Waveform uses INITIAL BPM/GAP so it stays anchored to the actual audio.
    // Notes use CURRENT BPM/GAP. This lets the user see misalignment when tuning.
    if (showWaveform && waveformPeaks.length > 0 && initialBpm > 0) {
      ctx.fillStyle = '#0a0a18';
      ctx.fillRect(0, 0, w, wt);

      const sampleRate = waveformPeaks.length / (audioDuration || 1);
      const waveGapSec = initialGap / 1000;
      const waveBpm = initialBpm;
      const midY = wt / 2;

      ctx.fillStyle = '#4fc3f744';
      for (let px = 0; px < w; px++) {
        const beat = xToBeat(px);  // pixel → beat in CURRENT beat-space
        // Convert beat to absolute time using INITIAL BPM/GAP
        const t = waveGapSec + (beat * 15) / waveBpm;
        const idx = Math.floor(t * sampleRate);
        if (idx < 0 || idx >= waveformPeaks.length) continue;
        const amp = waveformPeaks[idx] * (wt / 2) * 0.9;
        ctx.fillRect(px, midY - amp, 1, amp * 2);
      }

      // Separator line
      ctx.strokeStyle = '#333';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, wt);
      ctx.lineTo(w, wt);
      ctx.stroke();
    }

    // Reserve bottom strip for time axis
    const pianoH = h - timeAxisHeight;

    // Grid lines (pitch)
    const pitchRange = maxPitch - minPitch;
    for (let p = minPitch; p <= maxPitch; p++) {
      const y = pitchToY(p);
      if (y > pianoH) continue; // don't draw into time axis
      const isC = p % 12 === 0;
      
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

    // Grid lines (beats) + time axis labels
    const startBeat = Math.floor(xToBeat(0));
    const endBeat = Math.ceil(xToBeat(w));
    
    for (let b = startBeat; b <= endBeat; b++) {
      const x = beatToX(b);
      const isMeasure = b % Math.round(bpm / 60) === 0;
      
      ctx.strokeStyle = isMeasure ? '#2a2a4e' : '#161625';
      ctx.lineWidth = isMeasure ? 1 : 0.5;
      ctx.beginPath();
      ctx.moveTo(x, wt);
      ctx.lineTo(x, pianoH);
      ctx.stroke();
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

    const startTimeSec = Math.max(0, beatToTime(xToBeat(0)));
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

    // Draw reference notes (ghost overlay)
    if (showReference && referenceNotes.length > 0) {
      for (const note of referenceNotes) {
        if (note.type === 'break') continue;

        const x = beatToX(note.start_beat);
        const y = pitchToY(note.pitch);
        const width = note.duration * zoom;

        // Ghost outline style
        ctx.strokeStyle = '#66bb6a88';
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.strokeRect(x, y - noteHeight / 2, width, noteHeight);
        ctx.setLineDash([]);

        // Faint fill
        ctx.fillStyle = '#66bb6a11';
        ctx.fillRect(x, y - noteHeight / 2, width, noteHeight);

        // Syllable text for reference notes
        if (zoom > 1 && width > 10 && note.syllable) {
          ctx.fillStyle = '#66bb6a99';
          ctx.font = '9px sans-serif';
          ctx.fillText(note.syllable.trim(), x + 2, y - noteHeight / 2 - 2);
        }
      }
    }

    // Draw notes
    for (const note of notes) {
      if (note.type === 'break') {
        // Draw break line
        const x = beatToX(note.startBeat);
        ctx.strokeStyle = '#c6282855';
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
        ctx.setLineDash([]);
        continue;
      }

      const x = beatToX(note.startBeat);
      const y = pitchToY(note.pitch);
      const width = note.duration * zoom;

      // Note rectangle
      const isSelected = selectedNote === note.id;
      const hasChanged = note.original && (
        note.startBeat !== note.original.startBeat ||
        note.duration !== note.original.duration ||
        note.pitch !== note.original.pitch
      );

      if (note.isRap) {
        ctx.fillStyle = isSelected ? '#ff980088' : '#ff980044';
        ctx.strokeStyle = '#ff9800';
      } else if (hasChanged) {
        ctx.fillStyle = isSelected ? '#fdd83588' : '#fdd83544';
        ctx.strokeStyle = '#fdd835';
      } else {
        ctx.fillStyle = isSelected ? '#4fc3f788' : '#4fc3f744';
        ctx.strokeStyle = '#4fc3f7';
      }

      ctx.lineWidth = isSelected ? 2 : 1;
      ctx.fillRect(x, y - noteHeight / 2, width, noteHeight);
      ctx.strokeRect(x, y - noteHeight / 2, width, noteHeight);

      // Syllable text
      if (zoom > 1 && width > 10) {
        ctx.fillStyle = '#eee';
        ctx.font = '10px sans-serif';
        ctx.fillText(note.syllable.trim(), x + 2, y + 3);
      }
    }

    // Playback cursor (show when playing OR when paused at non-zero position)
    if (isPlaying || playbackBeat > 0) {
      const cx = beatToX(playbackBeat);
      const pianoBottom = h - timeAxisHeight;
      ctx.strokeStyle = isPlaying ? '#ff5252' : '#ff8a80';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx, 0);
      ctx.lineTo(cx, pianoBottom);
      ctx.stroke();
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

    // Find clicked note
    let found = null;
    for (const note of notes) {
      if (note.type === 'break') continue;
      
      const nx = beatToX(note.startBeat);
      const ny = pitchToY(note.pitch);
      const nw = note.duration * zoom;

      if (mx >= nx && mx <= nx + nw && my >= ny - noteHeight / 2 && my <= ny + noteHeight / 2) {
        found = note;

        // Detect resize zones (edges)
        if (mx - nx < 5) dragMode = 'resize-left';
        else if (nx + nw - mx < 5) dragMode = 'resize-right';
        else dragMode = 'move';

        break;
      }
    }

    if (found) {
      selectedNote = found.id;
      isDragging = true;
      dragStart = { x: mx, y: my, beat: found.startBeat, pitch: found.pitch, duration: found.duration };
      console.log(`[Mouse] Selected note id=${found.id} '${found.syllable}' mode=${dragMode}`);
    } else {
      selectedNote = null;
      console.log('[Mouse] No note selected');
    }

    draw();
  }

  function handleMouseMove(event) {
    if (!isDragging || selectedNote === null) return;

    const rect = canvasEl.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;

    const note = notes.find(n => n.id === selectedNote);
    if (!note || note.type === 'break') return;

    const dx = mx - dragStart.x;
    const dy = my - dragStart.y;

    if (dragMode === 'move') {
      note.startBeat = Math.max(0, Math.round(dragStart.beat + dx / zoom));
      note.pitch = Math.max(minPitch, Math.min(maxPitch, yToPitch(dragStart.y + dy)));
    } else if (dragMode === 'resize-right') {
      note.duration = Math.max(1, Math.round(dragStart.duration + dx / zoom));
    } else if (dragMode === 'resize-left') {
      const newStart = Math.max(0, Math.round(dragStart.beat + dx / zoom));
      const diff = note.startBeat - newStart;
      note.startBeat = newStart;
      note.duration = Math.max(1, note.duration + diff);
    }

    editorState.update(s => ({ ...s, hasChanges: true }));
    notes = [...notes]; // trigger reactivity
    draw();
  }

  function handleMouseUp() {
    if (isDragging) console.log('[Mouse] mouseUp, drag ended');
    isDragging = false;
    dragMode = null;
  }

  function handleWheel(event) {
    event.preventDefault();
    
    if (event.ctrlKey || event.metaKey) {
      // Zoom
      const oldZoom = zoom;
      zoom = Math.max(0.5, Math.min(100, zoom + event.deltaY * -0.01));
      console.log(`[Wheel] Zoom ${oldZoom.toFixed(1)} → ${zoom.toFixed(1)}`);
    } else {
      // Scroll
      scrollX = Math.max(0, scrollX + event.deltaX + event.deltaY);
    }

    draw();
  }

  // Scrollbar input handler
  function handleScrollbar(event) {
    const beat = parseFloat(event.target.value);
    scrollX = beat * zoom;
    draw();
  }

  // ──── Playback ───────────────────────────────
  function togglePlayback() {
    if (!audioEl) {
      console.log('[Play] No audioEl');
      return;
    }

    if (isPlaying) {
      console.log(`[Play] Pausing at ${audioEl.currentTime.toFixed(2)}s, beat=${playbackBeat.toFixed(1)}`);
      audioEl.pause();
      currentTimeSec = audioEl.currentTime;
      isPlaying = false;
      cancelAnimationFrame(animFrame);
      draw(); // Redraw to show paused cursor
    } else {
      // Resume from our tracked position
      audioEl.currentTime = currentTimeSec;
      audioEl.playbackRate = playbackRate;
      audioEl.preservesPitch = true;
      console.log(`[Play] Starting from ${currentTimeSec.toFixed(2)}s, beat=${playbackBeat.toFixed(1)}, rate=${playbackRate}`);
      audioEl.play();
      isPlaying = true;
      updatePlayback();
    }
  }

  function stopPlayback() {
    console.log('[Stop] Resetting to 0');
    if (audioEl) {
      audioEl.pause();
      audioEl.currentTime = 0;
    }
    isPlaying = false;
    playbackBeat = 0;
    currentTimeSec = 0;
    cancelAnimationFrame(animFrame);
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
    audioEl.currentTime = newTime;
    currentTimeSec = newTime;
    const gapSec = gapMs / 1000;
    playbackBeat = Math.max(0, ((newTime - gapSec) * bpm) / 15);
    // Update scroll position to follow
    const canvasWidth = canvasEl?.width || 800;
    if (scrollMode) {
      scrollX = Math.max(0, playbackBeat * zoom - canvasWidth * 0.3);
    } else {
      const cursorX = beatToX(playbackBeat);
      if (cursorX < 0 || cursorX > canvasWidth) {
        scrollX = Math.max(0, (playbackBeat * zoom) - canvasWidth * 0.3);
      }
    }
    draw();
  }

  function handleKeydown(e) {
    console.log(`[Key] ${e.code} shift=${e.shiftKey} ctrl=${e.ctrlKey}`);
    // Spacebar: toggle play/pause
    if (e.code === 'Space') {
      e.preventDefault();
      togglePlayback();
    }
    // Left arrow: move cursor (seek) — 5s or 1s with Shift
    if (e.code === 'ArrowLeft') {
      e.preventDefault();
      seekPlayback(e.shiftKey ? -1 : -5);
    }
    // Right arrow: move cursor (seek) — 5s or 1s with Shift
    if (e.code === 'ArrowRight') {
      e.preventDefault();
      seekPlayback(e.shiftKey ? 1 : 5);
    }
  }

  function updatePlayback() {
    if (!isPlaying) return;

    const currentTime = audioEl.currentTime;
    currentTimeSec = currentTime;
    const gapSec = gapMs / 1000;
    playbackBeat = Math.max(0, ((currentTime - gapSec) * bpm) / 15);

    // Scroll logic
    const canvasWidth = canvasEl?.width || 800;
    if (scrollMode) {
      // Fixed cursor: cursor stays at 30%, notes scroll
      scrollX = Math.max(0, playbackBeat * zoom - canvasWidth * 0.3);
    } else {
      // Classic: auto-scroll when cursor reaches 70%
      const cursorX = beatToX(playbackBeat);
      if (cursorX > canvasWidth * 0.7) {
        scrollX = (playbackBeat * zoom) - canvasWidth * 0.3;
      }
    }

    draw();
    animFrame = requestAnimationFrame(updatePlayback);
  }

  // Set playback speed
  function setPlaybackRate(rate) {
    console.log(`[Speed] Set playback rate: ${rate}x`);
    playbackRate = rate;
    if (audioEl) {
      audioEl.playbackRate = rate;
      audioEl.preservesPitch = true;
    }
  }

  // Resize canvas to fit container
  function resizeCanvas() {
    if (!canvasEl) return;
    canvasEl.width = canvasEl.parentElement.clientWidth;
    canvasEl.height = viewHeight;
    console.log(`[Resize] Canvas ${canvasEl.width}x${canvasEl.height}`);
    draw();
  }

  // Load waveform peaks from audio URL via Web Audio API
  async function loadWaveform(url) {
    console.log('[Waveform] Loading from', url);
    try {
      const resp = await fetch(url);
      const arrayBuffer = await resp.arrayBuffer();
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const decoded = await audioCtx.decodeAudioData(arrayBuffer);
      audioCtx.close();

      // Downsample to ~1000 peaks per second (plenty for visual)
      const rawData = decoded.getChannelData(0);
      const peaksPerSec = 200;
      const totalPeaks = Math.ceil(decoded.duration * peaksPerSec);
      const samplesPerPeak = Math.floor(rawData.length / totalPeaks);
      const peaks = new Float32Array(totalPeaks);

      for (let i = 0; i < totalPeaks; i++) {
        let max = 0;
        const start = i * samplesPerPeak;
        const end = Math.min(start + samplesPerPeak, rawData.length);
        for (let j = start; j < end; j++) {
          const abs = Math.abs(rawData[j]);
          if (abs > max) max = abs;
        }
        peaks[i] = max;
      }

      waveformPeaks = peaks;
      console.log(`[Waveform] Loaded ${totalPeaks} peaks for ${decoded.duration.toFixed(1)}s audio`);
      draw();
    } catch (err) {
      console.warn('[Waveform] Failed to load:', err);
      waveformPeaks = [];
    }
  }

  // ──── Lifecycle ──────────────────────────────
  async function loadData() {
    console.log('[Step4] loadData, session:', $sessionId, 'hasResult:', !!$generationResult);
    if (!$generationResult) return;

    try {
      const data = await getEditorData($sessionId);
      console.log('[Step4] Editor data:', { bpm: data.bpm, gap: data.gap_ms, duration: data.audio_duration, contentLen: data.ultrastar_content?.length });
      
      notes = parseUltrastar(data.ultrastar_content);
      console.log('[Step4] Parsed', notes.length, 'notes/breaks');
      bpm = data.bpm;
      gapMs = data.gap_ms;

      // Store initial values and raw timings for BPM re-quantization
      initialBpm = data.bpm;
      initialGap = data.gap_ms;
      bpmChanged = false;
      rawTimings = data.syllable_timings || [];

      // Extract pitches from parsed notes (non-break, in order) for re-quantization
      pitchMap = notes.filter(n => n.type !== 'break').map(n => n.pitch);
      console.log('[Step4] Stored', rawTimings.length, 'raw timings,', pitchMap.length, 'pitches for re-quantization');
      audioDuration = data.audio_duration;
      vocalUrl = data.vocal_url;
      console.log('[Step4] Vocal URL for playback:', vocalUrl);
      computeTotalBeats();

      // Load waveform
      if (vocalUrl) {
        loadWaveform(vocalUrl);
      }

      // Always try to load reference notes (don't gate on store state)
      {
        try {
          const refData = await getReferenceNotes($sessionId);
          refBpm = refData.bpm || bpm;
          refGapMs = refData.gap || 0;

          // Convert reference beats from reference coordinate space to generated coordinate space
          // Both use standard Ultrastar quarter-beat convention:
          // refTime = refGapSec + (refBeat * 15 / refBpm)   → real time in seconds
          // genBeat = (refTime - genGapSec) * genBpm / 15    → beat in generated space
          const refGapSec = refGapMs / 1000;
          const genGapSec = gapMs / 1000;

          referenceNotes = (refData.notes || []).map(n => {
            const realTimeSec = refGapSec + (n.start_beat * 15) / refBpm;
            const endTimeSec = refGapSec + ((n.start_beat + n.duration) * 15) / refBpm;
            const genStartBeat = Math.round(((realTimeSec - genGapSec) * bpm) / 15);
            const genEndBeat = Math.round(((endTimeSec - genGapSec) * bpm) / 15);
            return {
              ...n,
              start_beat: genStartBeat,
              duration: Math.max(1, genEndBeat - genStartBeat),
              original_start_beat: n.start_beat,
              original_duration: n.duration,
            };
          });

          console.log('[Step4] Loaded', referenceNotes.length, 'reference notes (converted from BPM', refBpm, 'GAP', refGapMs, 'to BPM', bpm, 'GAP', gapMs, ')');

          // Auto-detect and normalize pitch offset between AI notes and reference
          // AI uses MIDI pitches (e.g., 46-71), reference may use Ultrastar relative (e.g., 5-22)
          const aiPitchNotes = notes.filter(n => n.pitch !== undefined && n.type !== 'break');
          const refPitchNotes = referenceNotes.filter(n => n.pitch !== undefined && n.type !== 'break');
          if (aiPitchNotes.length > 0 && refPitchNotes.length > 0) {
            const aiMed = median(aiPitchNotes.map(n => n.pitch));
            const refMed = median(refPitchNotes.map(n => n.pitch));
            const rawOffset = aiMed - refMed;
            // Round to nearest octave (12 semitones)
            const octaveOffset = Math.round(rawOffset / 12) * 12;
            if (octaveOffset !== 0) {
              console.log(`[Step4] Shifting reference pitches by ${octaveOffset} semitones (AI median: ${aiMed}, ref median: ${refMed})`);
              referenceNotes = referenceNotes.map(n => ({
                ...n,
                pitch: n.pitch + octaveOffset,
                original_pitch: n.original_pitch || n.pitch,
              }));
            }
          }        } catch (e) {
          console.warn('[Step4] Failed to load reference notes:', e);
          referenceNotes = [];
        }
      }

      updatePitchRange();
      console.log('[Step4] Pitch range:', minPitch, '-', maxPitch);
      draw();
    } catch (err) {
      console.error('[Step4] loadData error:', err);
      errorMessage.set(err.message);
    }
  }

  onMount(() => {
    console.log('[Step4] onMount');
    if (canvasEl) {
      ctx = canvasEl.getContext('2d');
      resizeCanvas();
      loadData();
    }
    window.addEventListener('keydown', handleKeydown);
    window.addEventListener('resize', resizeCanvas);
  });

  onDestroy(() => {
    cancelAnimationFrame(animFrame);
    window.removeEventListener('keydown', handleKeydown);
    window.removeEventListener('resize', resizeCanvas);
  });

  // Reload when we enter this step
  $: if ($generationResult && canvasEl) {
    loadData();
  }
</script>

<div class="step-content">
  <h2>Step 4: Piano Roll Editor</h2>

  <div class="toolbar">
    <div class="playback-controls">
      <button class="tool-btn" on:click={() => { console.log('[UI] seek -5'); seekPlayback(-5); }} title="Back 5s (←)">⏪</button>
      <button class="tool-btn" on:click={() => { console.log('[UI] togglePlayback'); togglePlayback(); }} title="Space">
        {isPlaying ? '⏸ Pause' : '▶ Play'}
      </button>
      <button class="tool-btn" on:click={() => { console.log('[UI] seek +5'); seekPlayback(5); }} title="Forward 5s (→)">⏩</button>
      <button class="tool-btn" on:click={() => { console.log('[UI] stop'); stopPlayback(); }}>⏹ Stop</button>
      <span class="time-display">{formatTime(currentTimeSec)}</span>
    </div>

    <div class="zoom-controls">
      <button class="tool-btn" on:click={() => { zoom = Math.max(0.5, zoom - 1); console.log('[UI] zoom-', zoom); draw(); }}>−</button>
      <span class="zoom-label">Zoom: {zoom.toFixed(1)}x</span>
      <button class="tool-btn" on:click={() => { zoom = Math.min(100, zoom + 1); console.log('[UI] zoom+', zoom); draw(); }}>+</button>
    </div>

    <div class="mode-controls">
      <label>
        <input type="checkbox" bind:checked={scrollMode} on:change={() => console.log('[UI] scrollMode', scrollMode)} />
        Scroll
      </label>
      <label>
        <input type="checkbox" bind:checked={showWaveform} on:change={() => { console.log('[UI] waveform', showWaveform); draw(); }} />
        Wave
      </label>
    </div>

    <div class="speed-controls">
      <span class="speed-label">Speed</span>
      {#each [0.25, 0.5, 0.75, 1.0, 1.5, 2.0] as rate}
        <button
          class="tool-btn sm"
          class:active={playbackRate === rate}
          on:click={() => setPlaybackRate(rate)}
        >{rate}x</button>
      {/each}
    </div>

    <div class="bpm-controls">
      <span class="bpm-label">BPM</span>
      <button class="tool-btn sm" on:click={() => { bpm = Math.max(10, bpm - 1); console.log('[UI] bpm-', bpm); handleBpmGapChange(); }}>−</button>
      <input type="number" class="bpm-input" bind:value={bpm} on:change={() => { console.log('[UI] bpm input', bpm); handleBpmGapChange(); }} step="1" min="10" max="1000" />
      <button class="tool-btn sm" on:click={() => { bpm = bpm + 1; console.log('[UI] bpm+', bpm); handleBpmGapChange(); }}>+</button>

      <span class="bpm-label gap-label">GAP</span>
      <button class="tool-btn sm" on:click={() => { gapMs = Math.max(0, gapMs - 100); console.log('[UI] gap-', gapMs); handleBpmGapChange(); }}>−</button>
      <input type="number" class="gap-input" bind:value={gapMs} on:change={() => { console.log('[UI] gap input', gapMs); handleBpmGapChange(); }} step="100" min="0" />
      <button class="tool-btn sm" on:click={() => { gapMs = gapMs + 100; console.log('[UI] gap+', gapMs); handleBpmGapChange(); }}>+</button>
      <span class="bpm-label">ms</span>

      {#if bpmChanged}
        <button class="tool-btn apply-btn" on:click={handleApplyBpm} disabled={applyingBpm}>
          {applyingBpm ? '⏳' : '✓'} Apply
        </button>
      {/if}
    </div>

    {#if referenceNotes.length > 0}
      <div class="ref-toggle">
        <label>
          <input type="checkbox" bind:checked={showReference} on:change={draw} />
          📚 Reference
        </label>
      </div>
    {/if}

    <div class="info">
      {#if selectedNote !== null}
        {@const note = notes.find(n => n.id === selectedNote)}
        {#if note && note.type !== 'break'}
          <span class="note-info">
            {note.syllable.trim()} | Beat {note.startBeat} | Dur {note.duration} | {noteName(note.pitch)}
          </span>
        {/if}
      {/if}
    </div>
  </div>

  <div class="canvas-container">
    <canvas
      bind:this={canvasEl}
      on:mousedown={handleMouseDown}
      on:mousemove={handleMouseMove}
      on:mouseup={handleMouseUp}
      on:mouseleave={handleMouseUp}
      on:wheel={handleWheel}
    ></canvas>
  </div>

  <!-- Scrollbar -->
  <div class="scrollbar-container">
    <input
      type="range"
      class="scroll-range"
      bind:this={scrollBarEl}
      min="0"
      max={totalBeats}
      step="1"
      value={scrollX / zoom}
      on:input={handleScrollbar}
    />
  </div>

  <div class="legend">
    <span class="legend-item"><span class="dot blue"></span> Normal note</span>
    <span class="legend-item"><span class="dot yellow"></span> Edited note</span>
    <span class="legend-item"><span class="dot orange"></span> Rap note</span>
    <span class="legend-item"><span class="dot red-line"></span> Break line</span>
    {#if referenceNotes.length > 0}
      <span class="legend-item"><span class="dot green-dash"></span> Reference note</span>
    {/if}
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
        {#if bpmChanged}<em style="color: #fdd835"> — click Apply to save</em>{/if}
      </span>
      {#if referenceNotes.length > 0}
        {@const refRealNotes = referenceNotes.filter(n => !n.type)}
        {@const refFirst = refRealNotes.length > 0 ? refRealNotes[0] : null}
        {@const refLast = refRealNotes.length > 0 ? refRealNotes[refRealNotes.length - 1] : null}
        {@const refFirstTimeSec = refFirst ? (refGapMs / 1000 + (refFirst.original_start_beat * 60) / refBpm) : 0}
        {@const refLastTimeSec = refLast ? (refGapMs / 1000 + ((refLast.original_start_beat + refLast.original_duration) * 60) / refBpm) : 0}
        <span>Reference: BPM {(refBpm ?? 0).toFixed(1)} | GAP {refGapMs ?? 0}ms | {formatTime(refFirstTimeSec)}–{formatTime(refLastTimeSec)}</span>
      {/if}
    </div>
  {/if}

  <!-- Hidden audio element for playback -->
  {#if vocalUrl}
    <audio bind:this={audioEl} src={vocalUrl} preload="auto"></audio>
  {/if}

  <div class="help">
    <p><strong>Controls:</strong> Click note to select • Drag to move • Drag edges to resize • Ctrl+Scroll to zoom • Scrollbar to pan • ←→: move cursor ±5s (Shift: ±1s) • Space: play/pause • Toggle Scroll / Wave</p>
  </div>
</div>

<style>
  .step-content {
    max-width: 100%;
    margin: 0 auto;
  }

  h2 { color: #4fc3f7; margin-bottom: 1rem; }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.5rem;
    background: #1a1a2e;
    border: 1px solid #333;
    border-radius: 8px 8px 0 0;
    flex-wrap: wrap;
  }

  .playback-controls, .zoom-controls {
    display: flex;
    align-items: center;
    gap: 0.25rem;
  }

  .time-display {
    color: #aaa;
    font-size: 0.8rem;
    font-family: monospace;
    min-width: 40px;
    margin-left: 0.25rem;
  }

  .tool-btn {
    padding: 0.4rem 0.8rem;
    border: 1px solid #444;
    border-radius: 4px;
    background: #222;
    color: #ccc;
    cursor: pointer;
    font-size: 0.85rem;
  }

  .tool-btn:hover { background: #333; }

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
    border: 1px solid #333;
    border-top: none;
    overflow: hidden;
    cursor: crosshair;
  }

  canvas {
    display: block;
    width: 100%;
  }

  .scrollbar-container {
    padding: 0;
    background: #12121e;
    border: 1px solid #333;
    border-top: none;
  }

  .scroll-range {
    width: 100%;
    height: 18px;
    -webkit-appearance: none;
    appearance: none;
    background: transparent;
    cursor: pointer;
    margin: 0;
    display: block;
  }

  .scroll-range::-webkit-slider-runnable-track {
    height: 6px;
    background: #1a1a2e;
    border-radius: 3px;
  }

  .scroll-range::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 40px;
    height: 14px;
    background: #4fc3f7;
    border-radius: 4px;
    margin-top: -4px;
    cursor: grab;
  }

  .scroll-range::-moz-range-track {
    height: 6px;
    background: #1a1a2e;
    border-radius: 3px;
  }

  .scroll-range::-moz-range-thumb {
    width: 40px;
    height: 14px;
    background: #4fc3f7;
    border-radius: 4px;
    border: none;
    cursor: grab;
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
  .dot.orange { background: #ff9800; }
  .dot.red-line { background: #c62828; width: 2px; height: 12px; }
  .dot.green-dash { background: #66bb6a; width: 10px; height: 10px; border: 1px dashed #66bb6a; background: transparent; }

  .ref-toggle {
    font-size: 0.8rem;
    color: #66bb6a;
  }

  .ref-toggle input[type="checkbox"] {
    margin-right: 0.3rem;
  }

  .mode-controls {
    font-size: 0.8rem;
    color: #aaa;
    border-left: 1px solid #333;
    padding-left: 0.5rem;
  }

  .mode-controls label {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 0.3rem;
  }

  .speed-controls {
    display: flex;
    align-items: center;
    gap: 0.2rem;
    border-left: 1px solid #333;
    padding-left: 0.5rem;
  }

  .speed-label {
    color: #aaa;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.5px;
  }

  .tool-btn.sm.active {
    background: #4fc3f7;
    color: #0d1117;
    border-color: #4fc3f7;
  }

  .bpm-controls {
    display: flex;
    align-items: center;
    gap: 0.2rem;
    padding: 0 0.4rem;
    border-left: 1px solid #333;
  }

  .bpm-label {
    color: #aaa;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.5px;
  }

  .gap-label {
    margin-left: 0.6rem;
  }

  .bpm-input, .gap-input {
    width: 60px;
    padding: 0.25rem 0.3rem;
    background: #1a1a2e;
    border: 1px solid #444;
    border-radius: 4px;
    color: #4fc3f7;
    font-family: monospace;
    font-size: 0.8rem;
    text-align: center;
    -moz-appearance: textfield;
    appearance: textfield;
  }

  .gap-input {
    width: 70px;
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
    padding: 0.2rem 0.4rem;
    font-size: 0.75rem;
    min-width: 22px;
  }

  .apply-btn {
    background: #2e7d32 !important;
    color: #fff !important;
    margin-left: 0.4rem;
    font-weight: 600;
  }

  .apply-btn:hover {
    background: #388e3c !important;
  }

  .apply-btn:disabled {
    opacity: 0.6;
    cursor: wait;
  }

  .help {
    margin-top: 0.75rem;
    color: #666;
    font-size: 0.8rem;
    text-align: center;
  }

  .help strong { color: #888; }

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
</style>
