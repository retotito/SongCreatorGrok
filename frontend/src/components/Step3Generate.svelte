<script>
  import { sessionId, generationResult, referenceData, currentStep, isProcessing, processingStatus, errorMessage, generationLog, generationComparison, generationShowPreview } from '../stores/appStore.js';
  import { generateUltrastar, compareReference } from '../services/api.js';

  // Use store-backed variables so data survives navigation
  $: logMessages = $generationLog;
  $: showPreview = $generationShowPreview;
  $: comparisonResult = $generationComparison;

  async function handleGenerate() {
    console.log('[Step3] handleGenerate, session:', $sessionId);
    errorMessage.set('');
    isProcessing.set(true);
    generationLog.set([]);
    generationShowPreview.set(false);
    generationComparison.set(null);

    addLog('Starting generation pipeline...');

    try {
      addLog('Step 1/4: Detecting BPM...');
      addLog('Step 2/4: Running pitch detection...');
      addLog('Step 3/4: Aligning syllables to audio...');
      addLog('Step 4/4: Generating Ultrastar files...');

      const result = await generateUltrastar($sessionId);
      console.log('[Step3] Generation result:', result);

      generationResult.set(result);

      addLog(`✅ Done in ${result.elapsed_seconds}s`);
      addLog(`   BPM: ${result.bpm} | GAP: ${result.gap_ms}ms`);
      addLog(`   Syllables: ${result.syllable_count}`);
      addLog(`   Pitch: ${result.pitch_method} | Alignment: ${result.alignment_method}`);
      addLog(`   Audio: ${result.audio_duration}s`);

      showPreview = true;
      generationShowPreview.set(true);
      processingStatus.set('Generation complete!');

      // Auto-compare with reference if available
      if ($referenceData.uploaded) {
        addLog('📚 Comparing with reference file...');
        try {
          const comp = await compareReference($sessionId);
          comparisonResult = comp.comparison;
          generationComparison.set(comp.comparison);
          referenceData.update(d => ({ ...d, comparison: comp.comparison }));
          
          const s = comp.comparison.summary;
          addLog(`📊 Reference comparison:`);
          addLog(`   Matched: ${s.matched_notes}/${s.total_notes_ref} notes`);
          addLog(`   Pitch bias: ${s.avg_pitch_diff > 0 ? '+' : ''}${s.avg_pitch_diff} semitones (${s.pitch_bias})`);
          addLog(`   Duration bias: ${s.avg_duration_diff > 0 ? '+' : ''}${s.avg_duration_diff} beats (${s.duration_bias})`);
          addLog(`   Timing bias: ${s.avg_start_diff > 0 ? '+' : ''}${s.avg_start_diff} beats (${s.timing_bias})`);
          addLog(`   GAP diff: ${comp.comparison.ai_gap - comp.comparison.ref_gap}ms`);
          
          // Lyrics comparison
          if (comp.comparison.lyrics_comparison) {
            const lc = comp.comparison.lyrics_comparison;
            if (lc.exact_match) {
              addLog(`📝 Lyrics: ✅ match reference exactly`);
            } else {
              addLog(`📝 Lyrics: ⚠️ ${Math.round(lc.similarity * 100)}% similar (${lc.matching_lines}/${lc.total_lines_ref} lines match, ${lc.differences.length} differences)`);
            }
          }
        } catch (err) {
          addLog(`⚠️ Reference comparison failed: ${err.message}`);
        }
      }
    } catch (err) {
      addLog(`❌ Error: ${err.message}`);
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  function addLog(msg) {
    generationLog.update(logs => [...logs, { time: new Date().toLocaleTimeString(), text: msg }]);
  }
</script>

<div class="step-content">
  <h2>Step 3: Generate Ultrastar Files</h2>

  <div class="info-box">
    <p>This will run the full processing pipeline:</p>
    <ol>
      <li>Detect BPM from audio</li>
      <li>Detect pitches with CREPE (deep learning)</li>
      <li>Align syllables to audio with MFA</li>
      <li>Generate Ultrastar .txt + MIDI files</li>
    </ol>
  </div>

  <button
    class="btn btn-primary big"
    on:click={handleGenerate}
    disabled={$isProcessing || !$sessionId}
  >
    {$isProcessing ? '⏳ Processing...' : '🚀 Generate Ultrastar Files'}
  </button>

  {#if logMessages.length > 0}
    <div class="log-panel">
      <h3>Processing Log</h3>
      <div class="log-scroll">
        {#each logMessages as msg}
          <div class="log-line">
            <span class="log-time">{msg.time}</span>
            <span class="log-text">{msg.text}</span>
          </div>
        {/each}
      </div>
    </div>
  {/if}

  {#if showPreview && $generationResult}
    <div class="preview-panel">
      <h3>Ultrastar Preview</h3>
      <div class="stats-row">
        <div class="stat">
          <span class="stat-label">BPM</span>
          <span class="stat-value">{$generationResult.bpm}</span>
        </div>
        <div class="stat">
          <span class="stat-label">GAP</span>
          <span class="stat-value">{$generationResult.gap_ms}ms</span>
        </div>
        <div class="stat">
          <span class="stat-label">Syllables</span>
          <span class="stat-value">{$generationResult.syllable_count}</span>
        </div>
        <div class="stat">
          <span class="stat-label">Duration</span>
          <span class="stat-value">{$generationResult.audio_duration}s</span>
        </div>
      </div>

      <div class="ultrastar-preview">
        <pre>{$generationResult.ultrastar_preview}</pre>
      </div>
    </div>
  {/if}

  {#if comparisonResult}
    <div class="comparison-panel">
      <h3>📚 Reference Comparison</h3>
      <div class="stats-row">
        <div class="stat">
          <span class="stat-label">Pitch Bias</span>
          <span class="stat-value" class:positive={comparisonResult.summary.avg_pitch_diff > 0} class:negative={comparisonResult.summary.avg_pitch_diff < 0}>
            {comparisonResult.summary.avg_pitch_diff > 0 ? '+' : ''}{comparisonResult.summary.avg_pitch_diff}
          </span>
          <span class="stat-unit">semitones</span>
        </div>
        <div class="stat">
          <span class="stat-label">Duration Bias</span>
          <span class="stat-value" class:positive={comparisonResult.summary.avg_duration_diff > 0} class:negative={comparisonResult.summary.avg_duration_diff < 0}>
            {comparisonResult.summary.avg_duration_diff > 0 ? '+' : ''}{comparisonResult.summary.avg_duration_diff}
          </span>
          <span class="stat-unit">beats</span>
        </div>
        <div class="stat">
          <span class="stat-label">Timing Bias</span>
          <span class="stat-value" class:positive={comparisonResult.summary.avg_start_diff > 0} class:negative={comparisonResult.summary.avg_start_diff < 0}>
            {comparisonResult.summary.avg_start_diff > 0 ? '+' : ''}{comparisonResult.summary.avg_start_diff}
          </span>
          <span class="stat-unit">beats</span>
        </div>
        <div class="stat">
          <span class="stat-label">Matched</span>
          <span class="stat-value">{comparisonResult.summary.matched_notes}/{comparisonResult.summary.total_notes_ref}</span>
          <span class="stat-unit">notes</span>
        </div>
      </div>
      <div class="comparison-details">
        <p>BPM: AI {comparisonResult.ai_bpm} vs Ref {comparisonResult.ref_bpm} (diff: {comparisonResult.summary.bpm_diff})</p>
        <p>GAP: AI {comparisonResult.ai_gap}ms vs Ref {comparisonResult.ref_gap}ms (diff: {comparisonResult.summary.gap_diff}ms)</p>
        <p>Exact pitch matches: {comparisonResult.summary.exact_pitch_matches} | Within 1 semitone: {comparisonResult.summary.close_pitch_matches}</p>
        <p class="hint">This comparison has been stored for future learning.</p>
      </div>
    </div>
  {/if}

  {#if $errorMessage}
    <div class="error-bar">❌ {$errorMessage}</div>
  {/if}
</div>

<style>
  .step-content {
    max-width: 700px;
    margin: 0 auto;
  }

  h2 { color: #4fc3f7; margin-bottom: 1rem; }
  h3 { color: #aaa; margin: 1rem 0 0.5rem; font-size: 0.95rem; }

  .info-box {
    background: #1a1a2e;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1rem;
    color: #aaa;
    font-size: 0.9rem;
  }

  .info-box ol {
    margin: 0.5rem 0 0 1.2rem;
    padding: 0;
  }

  .info-box li {
    margin-bottom: 0.3rem;
  }

  .btn {
    padding: 0.75rem 1.5rem;
    border: none;
    border-radius: 8px;
    font-size: 0.9rem;
    cursor: pointer;
    transition: all 0.2s;
  }

  .btn.big {
    width: 100%;
    padding: 1rem;
    font-size: 1.1rem;
  }

  .btn-primary { background: #1976d2; color: white; }
  .btn-primary:hover:not(:disabled) { background: #1565c0; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .log-panel {
    background: #0d1117;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 1rem;
    margin-top: 1rem;
  }

  .log-scroll {
    max-height: 200px;
    overflow-y: auto;
  }

  .log-line {
    font-family: 'Courier New', monospace;
    font-size: 0.8rem;
    padding: 0.15rem 0;
    display: flex;
    gap: 0.5rem;
  }

  .log-time { color: #666; }
  .log-text { color: #aaa; }

  .preview-panel {
    border: 1px solid #333;
    border-radius: 8px;
    padding: 1rem;
    margin-top: 1rem;
  }

  .stats-row {
    display: flex;
    gap: 1rem;
    margin-bottom: 1rem;
  }

  .stat {
    flex: 1;
    background: #1a1a2e;
    border-radius: 8px;
    padding: 0.75rem;
    text-align: center;
  }

  .stat-label {
    display: block;
    color: #666;
    font-size: 0.75rem;
    text-transform: uppercase;
  }

  .stat-value {
    display: block;
    color: #4fc3f7;
    font-size: 1.2rem;
    font-weight: 700;
  }

  .ultrastar-preview {
    background: #0d1117;
    border-radius: 6px;
    padding: 0.75rem;
    max-height: 300px;
    overflow: auto;
  }

  .ultrastar-preview pre {
    margin: 0;
    font-family: 'Courier New', monospace;
    font-size: 0.75rem;
    color: #aaa;
    white-space: pre;
  }

  .comparison-panel {
    border: 1px solid #7b1fa2;
    border-radius: 8px;
    padding: 1rem;
    margin-top: 1rem;
    background: #1a1025;
  }

  .comparison-panel h3 {
    color: #ce93d8;
  }

  .comparison-details {
    font-size: 0.8rem;
    color: #aaa;
    margin-top: 0.75rem;
  }

  .comparison-details p {
    margin: 0.2rem 0;
  }

  .comparison-details .hint {
    color: #666;
    margin-top: 0.5rem;
    font-style: italic;
  }

  .stat-unit {
    display: block;
    color: #555;
    font-size: 0.65rem;
  }

  .stat-value.positive { color: #ef5350; }
  .stat-value.negative { color: #42a5f5; }

  .error-bar {
    background: #3e1a1a;
    border: 1px solid #c62828;
    border-radius: 8px;
    padding: 0.75rem;
    margin-top: 1rem;
    color: #ef9a9a;
    text-align: center;
  }
</style>
