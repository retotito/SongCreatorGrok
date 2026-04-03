<script>
  import { sessionId, generationResult, errorMessage, isProcessing } from '../stores/appStore.js';
  import { getDownloadUrl, exportFiles } from '../services/api.js';
  import { resetSession } from '../stores/appStore.js';

  let exported = false;

  async function handleExport() {
    console.log('[Step5] handleExport, session:', $sessionId);
    isProcessing.set(true);
    errorMessage.set('');

    try {
      await exportFiles($sessionId);
      console.log('[Step5] Export complete');
      exported = true;
    } catch (err) {
      console.error('[Step5] Export error:', err);
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  function downloadFile(type) {
    const url = getDownloadUrl($sessionId, type);
    console.log('[Step5] Download:', type, url);
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function startOver() {
    resetSession();
  }
</script>

<div class="step-content">
  <h2>Step 5: Export & Download</h2>

  {#if $generationResult}
    <div class="summary-card">
      <h3>Generation Summary</h3>
      <div class="summary-grid">
        <div class="summary-item">
          <span class="label">BPM</span>
          <span class="value">{$generationResult.bpm}</span>
        </div>
        <div class="summary-item">
          <span class="label">GAP</span>
          <span class="value">{$generationResult.gap_ms}ms</span>
        </div>
        <div class="summary-item">
          <span class="label">Syllables</span>
          <span class="value">{$generationResult.syllable_count}</span>
        </div>
        <div class="summary-item">
          <span class="label">Duration</span>
          <span class="value">{$generationResult.audio_duration}s</span>
        </div>
        <div class="summary-item">
          <span class="label">Pitch</span>
          <span class="value">{$generationResult.pitch_method}</span>
        </div>
        <div class="summary-item">
          <span class="label">Alignment</span>
          <span class="value">{$generationResult.alignment_method}</span>
        </div>
      </div>
    </div>

    <div class="download-section">
      <h3>Download Files</h3>

      <div class="download-grid">
        <button class="download-btn" on:click={() => downloadFile('txt')}>
          <span class="file-icon">📄</span>
          <span class="file-name">Ultrastar .txt</span>
          <span class="file-desc">Note file for Ultrastar</span>
        </button>

        <button class="download-btn" on:click={() => downloadFile('midi')}>
          <span class="file-icon">🎵</span>
          <span class="file-name">MIDI</span>
          <span class="file-desc">Pitch data</span>
        </button>

        <button class="download-btn" on:click={() => downloadFile('summary')}>
          <span class="file-icon">📋</span>
          <span class="file-name">Summary</span>
          <span class="file-desc">Processing details</span>
        </button>
      </div>
    </div>

    <div class="actions">
      <button class="btn btn-secondary" on:click={startOver}>
        ↩ Start Over
      </button>
    </div>
  {:else}
    <p class="no-result">No generation result yet. Go back to Step 3 to generate files.</p>
  {/if}

  {#if $errorMessage}
    <div class="error-bar">❌ {$errorMessage}</div>
  {/if}
</div>

<style>
  .step-content {
    max-width: 600px;
    margin: 0 auto;
  }

  h2 { color: #4fc3f7; margin-bottom: 1rem; }
  h3 { color: #aaa; margin: 1.5rem 0 0.75rem; font-size: 0.95rem; }

  .summary-card {
    background: #1a1a2e;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 1rem;
  }

  .summary-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.75rem;
  }

  .summary-item {
    text-align: center;
  }

  .label {
    display: block;
    color: #666;
    font-size: 0.75rem;
    text-transform: uppercase;
  }

  .value {
    display: block;
    color: #4fc3f7;
    font-size: 1rem;
    font-weight: 600;
  }

  .download-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.75rem;
  }

  .download-btn {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.3rem;
    padding: 1.25rem;
    border: 1px solid #444;
    border-radius: 8px;
    background: #1a1a2e;
    color: #ccc;
    cursor: pointer;
    transition: all 0.2s;
  }

  .download-btn:hover {
    border-color: #4fc3f7;
    background: #1a2e4a;
  }

  .file-icon { font-size: 2rem; }
  .file-name { font-weight: 600; font-size: 0.9rem; }
  .file-desc { font-size: 0.75rem; color: #666; }

  .actions {
    margin-top: 2rem;
    text-align: center;
  }

  .btn {
    padding: 0.75rem 1.5rem;
    border: none;
    border-radius: 8px;
    font-size: 0.9rem;
    cursor: pointer;
  }

  .btn-secondary {
    background: #333;
    color: #ccc;
    border: 1px solid #555;
  }
  .btn-secondary:hover { background: #444; }

  .no-result {
    color: #666;
    text-align: center;
    padding: 2rem;
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
