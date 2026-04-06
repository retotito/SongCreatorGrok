<script>
  import { sessionId, generationResult, currentStep, isProcessing, processingStatus, errorMessage, generationLog, generationShowPreview } from '../stores/appStore.js';
  import { generateUltrastar } from '../services/api.js';

  // Use store-backed variables so data survives navigation
  $: logMessages = $generationLog;
  $: showPreview = $generationShowPreview;

  async function handleGenerate() {
    console.log('[Step3] handleGenerate, session:', $sessionId);
    errorMessage.set('');
    isProcessing.set(true);
    generationLog.set([]);
    generationShowPreview.set(false);

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

      // Show ms-based timing comparison if returned from backend
      if (result.ms_comparison) {
        const mc = result.ms_comparison;
        addLog(`⏱️ Timing comparison (ms, BPM-independent):`);
        addLog(`   Matched: ${mc.matched}/${mc.total_ref} syllables`);
        addLog(`   Median error: ${(mc.median_error_sec * 1000).toFixed(0)}ms | Mean: ${(mc.mean_error_sec * 1000).toFixed(0)}ms`);
        addLog(`   ≤200ms: ${mc.within_200ms} (${mc.pct_within_200ms}%) | ≤500ms: ${mc.within_500ms} (${mc.pct_within_500ms}%)`);
        addLog(`   Drift: ${mc.mean_drift_sec > 0 ? '+' : ''}${(mc.mean_drift_sec * 1000).toFixed(0)}ms | Max error: ${(mc.max_error_sec * 1000).toFixed(0)}ms`);
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
      <li>Detect pitches with PYIN</li>
      <li>Align syllables to audio with WhisperX</li>
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

  {#if $generationResult && !$isProcessing}
    <button
      class="btn btn-secondary"
      on:click={() => { console.log('[Step3] Jump to editor'); currentStep.set(4); }}
    >
      🎹 Open in Piano Roll Editor
    </button>
  {/if}

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

  {#if $generationResult?.ms_comparison}
    {@const mc = $generationResult.ms_comparison}
    <div class="ms-comparison-panel">
      <h3>⏱️ Timing Accuracy (ms, BPM-independent)</h3>
      <div class="stats-row">
        <div class="stat">
          <span class="stat-label">Matched</span>
          <span class="stat-value">{mc.matched}/{mc.total_ref}</span>
          <span class="stat-unit">syllables</span>
        </div>
        <div class="stat">
          <span class="stat-label">Median Error</span>
          <span class="stat-value" class:good={mc.median_error_sec <= 0.2} class:warn={mc.median_error_sec > 0.2 && mc.median_error_sec <= 0.5} class:bad={mc.median_error_sec > 0.5}>
            {(mc.median_error_sec * 1000).toFixed(0)}ms
          </span>
        </div>
        <div class="stat">
          <span class="stat-label">≤200ms</span>
          <span class="stat-value" class:good={mc.pct_within_200ms >= 70} class:warn={mc.pct_within_200ms >= 40 && mc.pct_within_200ms < 70} class:bad={mc.pct_within_200ms < 40}>
            {mc.pct_within_200ms}%
          </span>
          <span class="stat-unit">{mc.within_200ms} notes</span>
        </div>
        <div class="stat">
          <span class="stat-label">Drift</span>
          <span class="stat-value">{mc.mean_drift_sec > 0 ? '+' : ''}{(mc.mean_drift_sec * 1000).toFixed(0)}ms</span>
        </div>
      </div>
      <div class="ms-details">
        <p>≤100ms: {mc.within_100ms} | ≤500ms: {mc.within_500ms} ({mc.pct_within_500ms}%) | >1s: {mc.over_1s} | >2s: {mc.over_2s}</p>
        <p>Mean error: {(mc.mean_error_sec * 1000).toFixed(0)}ms | Max: {(mc.max_error_sec * 1000).toFixed(0)}ms</p>
        {#if mc.ref_bpm}
          <p>Ref BPM: {mc.ref_bpm} | Ref GAP: {mc.ref_gap_ms}ms</p>
        {/if}
        {#if mc.details && mc.details.length > 0}
          <details>
            <summary>First {mc.details.length} note comparisons</summary>
            <div class="note-details-scroll">
              {#each mc.details as d}
                <div class="note-row" class:good-row={d.abs_dt <= 0.2} class:bad-row={d.abs_dt > 1}>
                  <span class="note-syl">{d.syllable}</span>
                  <span>AI: {d.ai_start.toFixed(3)}s</span>
                  <span>Ref: {d.ref_start.toFixed(3)}s</span>
                  <span class:positive={d.dt > 0} class:negative={d.dt < 0}>Δ{d.dt > 0 ? '+' : ''}{(d.dt * 1000).toFixed(0)}ms</span>
                </div>
              {/each}
            </div>
          </details>
        {/if}
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

  .btn-secondary {
    background: #2e7d32;
    color: white;
    width: 100%;
    padding: 0.75rem;
    font-size: 1rem;
    margin-top: 0.5rem;
  }

  .btn-secondary:hover { background: #388e3c; }

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

  .ms-comparison-panel {
    border: 1px solid #00897b;
    border-radius: 8px;
    padding: 1rem;
    margin-top: 1rem;
    background: #0d1f1a;
  }

  .ms-comparison-panel h3 {
    color: #4db6ac;
    margin: 0 0 0.75rem;
  }

  .ms-details {
    font-size: 0.8rem;
    color: #aaa;
    margin-top: 0.75rem;
  }

  .ms-details p {
    margin: 0.2rem 0;
  }

  .stat-value.good { color: #66bb6a; }
  .stat-value.warn { color: #ffa726; }
  .stat-value.bad { color: #ef5350; }

  .note-details-scroll {
    max-height: 200px;
    overflow-y: auto;
    margin-top: 0.5rem;
  }

  .note-row {
    font-family: 'Courier New', monospace;
    font-size: 0.75rem;
    padding: 0.1rem 0.5rem;
    display: flex;
    gap: 1rem;
    color: #999;
  }

  .note-row.good-row { color: #66bb6a; }
  .note-row.bad-row { color: #ef5350; }
  .note-syl { width: 80px; text-align: right; color: #ccc; }

  details summary {
    color: #4db6ac;
    cursor: pointer;
    font-size: 0.8rem;
    margin-top: 0.5rem;
  }

  .stat-unit {
    display: block;
    color: #555;
    font-size: 0.65rem;
  }

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
