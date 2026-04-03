<script>
  import { sessionId, uploadData, referenceData, currentStep, isProcessing, processingStatus, errorMessage } from '../stores/appStore.js';
  import { uploadAudio, extractVocals, uploadCorrectedVocals, getAudioUrl, loadTestSession, uploadReference } from '../services/api.js';

  let dragOver = false;
  let audioPlayer;

  async function handleFileSelect(event) {
    const file = event.target.files?.[0];
    if (file) await processUpload(file);
  }

  function handleDrop(event) {
    event.preventDefault();
    dragOver = false;
    const file = event.dataTransfer.files?.[0];
    if (file) processUpload(file);
  }

  async function processUpload(file) {
    errorMessage.set('');
    isProcessing.set(true);
    processingStatus.set('Uploading audio...');

    try {
      const result = await uploadAudio(file);
      sessionId.set(result.session_id);
      uploadData.update(d => ({ ...d, filename: result.filename }));
      processingStatus.set('Upload complete! Choose an option below.');
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  async function handleExtractVocals() {
    errorMessage.set('');
    isProcessing.set(true);
    processingStatus.set('Extracting vocals with Demucs (this may take a few minutes)...');

    try {
      const result = await extractVocals($sessionId);
      uploadData.update(d => ({
        ...d,
        hasVocals: true,
        vocalUrl: getAudioUrl($sessionId, 'vocals'),
      }));
      processingStatus.set('Vocals extracted! Preview below or continue.');
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  async function handleUploadVocals(event) {
    const file = event.target.files?.[0];
    if (!file) return;

    errorMessage.set('');
    isProcessing.set(true);
    processingStatus.set('Uploading corrected vocals...');

    try {
      await uploadCorrectedVocals($sessionId, file);
      uploadData.update(d => ({
        ...d,
        hasVocals: true,
        vocalUrl: getAudioUrl($sessionId, 'vocals'),
      }));
      processingStatus.set('Vocals uploaded! Preview below or continue.');
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  async function handleSkipToVocals(event) {
    // Upload audio directly as vocals (already isolated)
    const file = event.target.files?.[0];
    if (!file) return;

    errorMessage.set('');
    isProcessing.set(true);
    processingStatus.set('Uploading vocal audio...');

    try {
      const uploadResult = await uploadAudio(file);
      sessionId.set(uploadResult.session_id);
      await uploadCorrectedVocals(uploadResult.session_id, file);
      uploadData.update(d => ({
        ...d,
        filename: file.name,
        hasVocals: true,
        vocalUrl: getAudioUrl(uploadResult.session_id, 'vocals'),
      }));
      processingStatus.set('Vocal audio ready! Preview below or continue.');
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  async function handleLoadTest() {
    errorMessage.set('');
    isProcessing.set(true);
    processingStatus.set('Loading test files...');

    try {
      const result = await loadTestSession();
      sessionId.set(result.session_id);
      uploadData.set({
        filename: 'test_vocal.wav',
        hasVocals: true,
        vocalUrl: getAudioUrl(result.session_id, 'vocals'),
      });
      // Jump to step 2 since lyrics are also loaded
      currentStep.set(2);
      processingStatus.set('');
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  async function handleReferenceUpload(event) {
    const file = event.target.files?.[0];
    if (!file || !$sessionId) return;

    errorMessage.set('');
    try {
      const result = await uploadReference($sessionId, file);
      referenceData.set({
        uploaded: true,
        filename: result.filename,
        notesCount: result.notes_count,
        bpm: result.bpm,
        gap: result.gap,
        comparison: null,
      });
      processingStatus.set(`Reference uploaded: ${result.filename} (${result.notes_count} notes)`);
    } catch (err) {
      errorMessage.set(err.message);
    }
  }
</script>

<div class="step-content">
  <h2>Step 1: Upload Audio</h2>

  {#if !$sessionId}
    <!-- Upload area -->
    <div
      class="drop-zone"
      class:drag-over={dragOver}
      role="button"
      tabindex="0"
      on:dragover|preventDefault={() => (dragOver = true)}
      on:dragleave={() => (dragOver = false)}
      on:drop={handleDrop}
    >
      <div class="drop-icon">🎵</div>
      <p>Drag & drop your audio file here</p>
      <p class="hint">MP3, WAV, or other audio format</p>
      
      <div class="upload-options">
        <label class="btn btn-primary">
          Upload Full Song (needs separation)
          <input type="file" accept="audio/*" on:change={handleFileSelect} hidden />
        </label>
        
        <label class="btn btn-secondary">
          Upload Isolated Vocals (skip separation)
          <input type="file" accept="audio/*" on:change={handleSkipToVocals} hidden />
        </label>
      </div>
    </div>

    <div class="divider">or</div>

    <button class="btn btn-test" on:click={handleLoadTest} disabled={$isProcessing}>
      🧪 Load Test Files (Beautiful Day)
    </button>

  {:else if !$uploadData.hasVocals}
    <!-- File uploaded, choose extraction method -->
    <div class="uploaded-info">
      <p>✅ Uploaded: <strong>{$uploadData.filename}</strong></p>
    </div>

    <div class="action-buttons">
      <button class="btn btn-primary" on:click={handleExtractVocals} disabled={$isProcessing}>
        🎤 Extract Vocals (Demucs)
      </button>
      
      <label class="btn btn-secondary">
        📂 Upload Corrected Vocals Instead
        <input type="file" accept="audio/*" on:change={handleUploadVocals} hidden />
      </label>
    </div>

  {:else}
    <!-- Vocals ready -->
    <div class="vocals-ready">
      <p>✅ Vocals ready: <strong>{$uploadData.filename}</strong></p>
      
      {#if $uploadData.vocalUrl}
        <div class="audio-preview">
          <p>Preview vocals:</p>
          <audio bind:this={audioPlayer} controls src={$uploadData.vocalUrl}>
            Your browser does not support the audio element.
          </audio>
        </div>
      {/if}

      <div class="action-buttons">
        <label class="btn btn-secondary small">
          ↻ Replace with different vocals
          <input type="file" accept="audio/*" on:change={handleUploadVocals} hidden />
        </label>
      </div>

      <!-- Reference file upload (optional, for learning) -->
      <div class="reference-section">
        <h3>📚 Reference File (Optional)</h3>
        <p class="hint">Upload a verified Ultrastar .txt file for this song to help the AI learn.</p>
        {#if $referenceData.uploaded}
          <div class="reference-info">
            ✅ {$referenceData.filename} ({$referenceData.notesCount} notes, BPM: {$referenceData.bpm})
          </div>
        {:else}
          <label class="btn btn-reference small">
            📄 Upload Reference .txt
            <input type="file" accept=".txt" on:change={handleReferenceUpload} hidden />
          </label>
        {/if}
      </div>
    </div>
  {/if}

  {#if $processingStatus}
    <div class="status-bar">{$processingStatus}</div>
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

  h2 {
    color: #4fc3f7;
    margin-bottom: 1rem;
  }

  .drop-zone {
    border: 2px dashed #444;
    border-radius: 12px;
    padding: 2rem;
    text-align: center;
    transition: all 0.2s;
  }

  .drop-zone.drag-over {
    border-color: #4fc3f7;
    background: #1a2e4a22;
  }

  .drop-icon {
    font-size: 3rem;
    margin-bottom: 0.5rem;
  }

  .hint {
    color: #666;
    font-size: 0.85rem;
  }

  .upload-options {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-top: 1rem;
  }

  .btn {
    display: inline-block;
    padding: 0.75rem 1.5rem;
    border: none;
    border-radius: 8px;
    font-size: 0.9rem;
    cursor: pointer;
    transition: all 0.2s;
    text-align: center;
  }

  .btn-primary {
    background: #1976d2;
    color: white;
  }
  .btn-primary:hover:not(:disabled) { background: #1565c0; }

  .btn-secondary {
    background: #333;
    color: #ccc;
    border: 1px solid #555;
  }
  .btn-secondary:hover:not(:disabled) { background: #444; }

  .btn-test {
    background: #2e7d32;
    color: white;
    width: 100%;
  }
  .btn-test:hover:not(:disabled) { background: #388e3c; }

  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .btn.small { font-size: 0.8rem; padding: 0.5rem 1rem; }

  .divider {
    text-align: center;
    color: #666;
    margin: 1rem 0;
    position: relative;
  }
  .divider::before, .divider::after {
    content: '';
    position: absolute;
    top: 50%;
    width: 40%;
    height: 1px;
    background: #333;
  }
  .divider::before { left: 0; }
  .divider::after { right: 0; }

  .uploaded-info, .vocals-ready {
    background: #1a2e1a;
    border: 1px solid #2e7d32;
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1rem;
  }

  .action-buttons {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    margin-top: 1rem;
  }

  .audio-preview {
    margin: 1rem 0;
  }

  .audio-preview audio {
    width: 100%;
    margin-top: 0.5rem;
  }

  .reference-section {
    margin-top: 1.5rem;
    padding: 1rem;
    border: 1px dashed #555;
    border-radius: 8px;
    background: #1a1a2e;
  }

  .reference-section h3 {
    color: #aaa;
    font-size: 0.9rem;
    margin: 0 0 0.5rem 0;
  }

  .reference-info {
    color: #66bb6a;
    font-size: 0.85rem;
    padding: 0.5rem;
    background: #1a2e1a;
    border-radius: 6px;
  }

  .btn-reference {
    background: #4a148c;
    color: #ce93d8;
    border: 1px solid #7b1fa2;
  }
  .btn-reference:hover { background: #6a1b9a; }

  .status-bar {
    background: #1a2e4a;
    border: 1px solid #1976d2;
    border-radius: 8px;
    padding: 0.75rem;
    margin-top: 1rem;
    color: #4fc3f7;
    text-align: center;
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
