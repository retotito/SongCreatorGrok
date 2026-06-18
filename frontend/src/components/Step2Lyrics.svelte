<script>
  async function handleLoadTestLyrics() {
    try {
      const result = await getTestLyrics();
      lyricsText = result.lyrics;
      artist = 'U2';
      title = 'Beautiful Day';
    } catch (err) {
      errorMessage.set(err.message);
    }
  }

  async function handleAutoHyphenate() {
    if (!lyricsText.trim()) {
      errorMessage.set('Enter lyrics first');
      return;
    }
    errorMessage.set('');
    isProcessing.set(true);
    processingStatus.set('Auto-hyphenating lyrics...');
    try {
      const result = await hyphenateLyrics(lyricsText, language);
      hyphenationResult = result;
      lyricsText = result.hyphenated;
      processingStatus.set(`✅ Auto-hyphenated: ${result.total_syllables} syllables (${result.method})`);
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  let emptyGenerating = false;

  async function handleGenerateEmpty() {
    if (!$sessionId) { errorMessage.set('No session. Please upload audio first.'); return; }
    if (!artist.trim() || !title.trim()) { errorMessage.set('Please enter Artist and Title before generating.'); return; }
    isProcessing.set(true);
    emptyGenerating = true;
    processingStatus.set('Creating empty Ultrastar file…');
    try {
      const result = await generateEmptyUltrastar($sessionId);
      generationResult.set(result);
      currentStep.set(4);
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
      emptyGenerating = false;
    }
  }

  async function handleSubmit(useCleaned = false) {
    if (!lyricsText.trim()) {
      errorMessage.set('Please enter lyrics');
      return;
    }
    if (!$sessionId) {
      errorMessage.set('No session. Please upload audio first.');
      return;
    }
    errorMessage.set('');
    isProcessing.set(true);
    processingStatus.set('Validating lyrics...');
    try {
      const result = await submitLyrics($sessionId, lyricsText, artist, title, language);
      lyricsData.set({
        text: lyricsText,
        artist,
        title,
        language,
        syllableCount: result.syllable_count,
        lineCount: result.line_count,
        preview: result.preview,
      });
      processingStatus.set(`✅ ${result.syllable_count} syllables across ${result.line_count} lines`);
      generationUseCleaned.set(useCleaned);
      generationModalOpen.set(true);
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  // Restore checkTestSession function
  async function checkTestSession() {
    if ($sessionId && $sessionId.startsWith('test-')) {
      try {
        const result = await getTestLyrics();
        lyricsText = result.lyrics;
        artist = 'U2';
        title = 'Beautiful Day';
        // Optionally auto-submit or set more fields here
      } catch (e) {
        // Test lyrics not available, user can enter manually
      }
    }
  }
  import { onDestroy } from 'svelte';
  import { sessionId, lyricsData, uploadData, currentStep, isProcessing, processingStatus, errorMessage, generationModalOpen, generationUseCleaned, generationResult } from '../stores/appStore.js';
  import { SUPPORTED_LANGUAGES } from '../lib/languages';
  import { submitLyrics, getTestLyrics, loadTestSession, hyphenateLyrics, transcribeAudio, cancelTranscribe, getAudioUrl, updateMetadata, getEditorData, generateCleanedAudio, generateEmptyUltrastar } from '../services/api.js';

  async function syncMetadata() {
    if (!$sessionId) return;
    if (!artist.trim() || !title.trim()) return;
    // Always persist artist/title to the store so navigation doesn't lose them
    lyricsData.update(s => ({ ...s, artist: artist.trim(), title: title.trim() }));
    try {
      await updateMetadata($sessionId, artist.trim(), title.trim());
    } catch (e) {
      // non-critical, ignore
    }
  }


  // If coming from test session, lyrics may already be loaded
  let lyricsText = $lyricsData.text || '';
  let artist = $lyricsData.artist || '';
  let title = $lyricsData.title || '';
  let language = $lyricsData.language || '';
  let hyphenationResult = null;
  let isTranscribing = false;
  let transcribeInfo = null;
  let transcribeStatus = '';
  let transcribePhase = '';  // 'loading' | 'transcribing' | 'done' | 'error'
  let transcribeElapsed = 0;
  let transcribeModalOpen = false;
  let whisperFallbackWarning = false;
  let transcribeAbortController = null;
  let transcribeTicker = null;
  const transcribeModelPreset = 'balanced';

  // Cleanup segments
  let cleanupSegments = [];
  let isGeneratingCleaned = false;
  let cleanedAudioAvailable = false;
  let cleanedAudioFilename = '';
  let hasOriginalDemucs = false; // legacy compat
  let hasEditedVocal = false; // true when edited_vocal exists (new design)

  function startTranscribeTicker() {
    transcribeElapsed = 0;
    transcribeTicker = setInterval(() => { transcribeElapsed += 1; }, 1000);
  }
  function stopTranscribeTicker() {
    if (transcribeTicker) { clearInterval(transcribeTicker); transcribeTicker = null; }
  }

  // Load cleanup segments from editor when entering Step 2
  async function loadCleanupSegments() {
    if (!$sessionId || !$generationResult) return; // no result yet — nothing to load
    try {
      const editorData = await getEditorData($sessionId);
      const newSegments = editorData.cleanup_segments || [];
      cleanupSegments = newSegments;
      // Restore cleaned audio state from backend (only reset if segments changed)
      cleanedAudioAvailable = editorData.cleaned_audio_available || false;
      if (!cleanedAudioAvailable) cleanedAudioFilename = '';
      hasOriginalDemucs = editorData.has_original_demucs || false; // legacy
      hasEditedVocal = editorData.has_edited_vocal || false;
    } catch (e) {
      console.error('[Step2] Failed to load cleanup segments:', e);
      cleanupSegments = [];
    }
  }

  // Handler for "Generate Lyrics from Cleaned Vocals" button
  async function handleTranscribeFromCleaned() {
    if (!$sessionId) return;
    errorMessage.set('');
    isTranscribing = true;
    transcribePhase = 'loading';
    transcribeStatus = 'Loading Whisper model…';
    transcribeElapsed = 0;
    transcribeModalOpen = true;
    transcribeAbortController = new AbortController();
    try {
      transcribePhase = 'transcribing';
      transcribeStatus = 'Transcribing cleaned vocals with Whisper…';
      startTranscribeTicker();
      const result = await transcribeAudio($sessionId, language, transcribeAbortController.signal, true, transcribeModelPreset);
      stopTranscribeTicker();
      lyricsText = result.text;
      transcribeInfo = result;
      transcribePhase = 'done';
      transcribeStatus = `${result.lines} lines, ${result.words} words (${result.language_name}, ${result.model})`;
      processingStatus.set('✅ Transcription from cleaned vocals complete');
      if (result.model && !result.model.startsWith('whisperx-')) {
        whisperFallbackWarning = true;
      }
      setTimeout(() => { transcribeModalOpen = false; }, 1800);
    } catch (err) {
      stopTranscribeTicker();
      if (err.name === 'AbortError') return;
      transcribePhase = 'error';
      transcribeStatus = err.message;
      errorMessage.set(err.message);
    } finally {
      isTranscribing = false;
    }
  }

  // Handler for "Generate Cleaned Preview" button
  async function handleGenerateCleanedAudio() {
    if (!$sessionId || cleanupSegments.length === 0) {
      errorMessage.set('No cleanup segments to process');
      return;
    }
    errorMessage.set('');
    isGeneratingCleaned = true;
    processingStatus.set('Generating cleaned audio preview...');
    try {
      const result = await generateCleanedAudio($sessionId, cleanupSegments);
      cleanedAudioFilename = result.cleaned_audio_file;
      cleanedAudioAvailable = true;
      processingStatus.set('✅ Cleaned audio preview generated');
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isGeneratingCleaned = false;
    }
  }

  // Keep lyricsData in sync with local fields
  $: lyricsData.set({
    text: lyricsText,
    artist,
    title,
    language,
    syllableCount: $lyricsData.syllableCount,
    lineCount: $lyricsData.lineCount,
    preview: $lyricsData.preview
  });

  // Handle file upload for .txt lyrics
  function handleFileUpload(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      lyricsText = e.target.result;
    };
    reader.readAsText(file);
  }

  // Audio
  $: hasVocals = $uploadData.hasVocals;
  // Always use /vocals (original unedited vocal) for the Step 2 preview.
  // With new design: /vocals is always the original. Legacy: use /demucs when has_original_demucs.
  $: audioSrc = $sessionId && hasVocals
    ? getAudioUrl($sessionId, hasOriginalDemucs && !hasEditedVocal ? 'demucs' : 'vocals')
    : '';

  // Sync from store ONCE on mount (e.g. when navigating back to Step 2)
  let initializedFromStore = false;
  $: if ($lyricsData.text && !initializedFromStore) {
    initializedFromStore = true;
    lyricsText = $lyricsData.text;
    artist = $lyricsData.artist || artist;
    // Removed invalid async/await/try/catch from reactive statement
  }

  $: if ($currentStep === 2 && $sessionId) {
    checkTestSession();
    loadCleanupSegments();
  }

  onDestroy(() => {
    if ($sessionId && lyricsText.trim()) {
      submitLyrics($sessionId, lyricsText, artist, title, language).catch(() => {});
    } else if ($sessionId && artist.trim() && title.trim()) {
      // No lyrics yet but names entered — persist them
      updateMetadata($sessionId, artist.trim(), title.trim()).catch(() => {});
    }
  });

  // ── Whisper transcription ──
  async function handleTranscribe() {
    if (!$sessionId) {
      errorMessage.set('No session. Upload audio first.');
      return;
    }
    errorMessage.set('');
    isTranscribing = true;
    transcribePhase = 'loading';
    transcribeStatus = 'Loading Whisper model…';
    transcribeElapsed = 0;
    transcribeModalOpen = true;
    transcribeAbortController = new AbortController();

    try {
      transcribePhase = 'transcribing';
      transcribeStatus = 'Transcribing vocals with Whisper…';
      startTranscribeTicker();
      const result = await transcribeAudio($sessionId, language, transcribeAbortController.signal, false, transcribeModelPreset);
      stopTranscribeTicker();
      console.log('[Step2] Whisper result:', result);
      lyricsText = result.text;
      transcribeInfo = result;
      transcribePhase = 'done';
      transcribeStatus = `${result.lines} lines, ${result.words} words (${result.language_name}, ${result.model})`;
      processingStatus.set('✅ Transcription complete');
      if (result.model && !result.model.startsWith('whisperx-')) {
        whisperFallbackWarning = true;
      }
      setTimeout(() => { transcribeModalOpen = false; }, 1800);
    } catch (err) {
      stopTranscribeTicker();
      if (err.name === 'AbortError') return; // cancelled silently
      console.error('[Step2] Transcription error:', err);
      transcribePhase = 'error';
      transcribeStatus = err.message;
      errorMessage.set(err.message);
    } finally {
      isTranscribing = false;
    }
  }

  function cancelTranscription() {
    stopTranscribeTicker();
    isTranscribing = false;
    transcribeModalOpen = false;
    transcribePhase = '';
    if (transcribeAbortController) transcribeAbortController.abort();
    cancelTranscribe($sessionId);
  }
</script>

<div class="step-content">
  <h2>Step 2: Lyrics</h2>

  <div class="form-group" style="margin-bottom:2rem;">
    <label for="language"><strong>Language (required)</strong></label>
    <select id="language" bind:value={language}>
      <option value="" disabled selected>Select language…</option>
      {#each SUPPORTED_LANGUAGES as lang}
        <option value={lang.code}>{lang.label}</option>
      {/each}
    </select>
    {#if !language}
      <div class="lang-warning">language of the song</div>
    {/if}
  </div>

  {#if language}
    {#if !hasVocals}
      <div class="no-vocals-warning">
        <p>⚠️ No vocal track found.</p>
        <p class="hint">Please go back to <button class="link-btn" on:click={() => currentStep.set(1)}>Step 1</button> to extract or upload vocals before generating lyrics.</p>
      </div>
      <!--
      <div class="back-btn-row">
        <button class="btn btn-secondary" on:click={() => currentStep.set(1)}>← Back to Step 1</button>
      </div>
      -->
    {:else}
      <div class="audio-section">
        <audio controls src={audioSrc}>
          Your browser does not support the audio element.
        </audio>
        <div class="transcribe-area">
          <button
            class="btn btn-transcribe"
            on:click={handleTranscribe}
            disabled={isTranscribing || $isProcessing}
          >
            🎙️ Generate Lyrics from Vocals
          </button>
          <p class="transcribe-hint">This will use AI to listen to your vocal track and automatically generate lyrics. You can review and edit the result below.</p>
        </div>
        {#if transcribeInfo}
          <div class="transcribe-info">
            Whisper ({transcribeInfo.model}): {transcribeInfo.words} words, {transcribeInfo.lines} lines — review and correct below
          </div>
        {/if}
      </div>
    {/if}

    {#if (hasEditedVocal || (cleanupSegments.length > 0 && cleanedAudioAvailable)) && $sessionId}
      <div class="cleanup-banner">
        <div class="cleanup-banner-content">
          <span class="cleanup-banner-icon">🧹</span>
          <div class="cleanup-banner-text">
            <strong>{cleanupSegments.length > 0 ? `${cleanupSegments.length} cleanup ${cleanupSegments.length === 1 ? 'section' : 'sections'} applied` : 'Edited vocals available'}</strong>
            <p class="cleanup-banner-hint">The edited vocal preview reflects your current cleanup sections from Step 4.</p>
          </div>
        </div>
        <div class="cleanup-audio-compare">
          <div class="audio-label">✨ Edited Vocals</div>
          <audio controls src={getAudioUrl($sessionId, hasEditedVocal ? 'edited' : 'cleaned')}>
            Your browser does not support the audio element.
          </audio>
          <div class="cleanup-transcribe-row">
            <button
              class="btn btn-transcribe"
              on:click={handleTranscribeFromCleaned}
              disabled={isTranscribing || $isProcessing}
            >
              🎙️ Generate Lyrics from Cleaned Vocals
            </button>
            <p class="transcribe-hint">Use AI to re-transcribe the cleaned audio. This will replace the current lyrics.</p>
          </div>
        </div>
      </div>
    {/if}

    <div class="form-row">
      <div class="form-group half">
        <label for="artist">Artist <span class="required">*</span></label>
        <input id="artist" type="text" bind:value={artist} placeholder="Artist name" class:input-missing={!artist.trim()} on:blur={syncMetadata} />
      </div>
      <div class="form-group half">
        <label for="title">Title <span class="required">*</span></label>
        <input id="title" type="text" bind:value={title} placeholder="Song title" class:input-missing={!title.trim()} on:blur={syncMetadata} />
      </div>
    </div>

    <div class="form-group">
      <label for="lyrics">
        Lyrics
        <span class="hint">(one line per phrase, use - for syllable splits: beau-ti-ful)</span>
      </label>
      <textarea
        id="lyrics"
        bind:value={lyricsText}
        rows="15"
        placeholder="The heart is a bloom&#10;Shoots up through the sto-ny ground&#10;There's no room&#10;..."
      ></textarea>
    </div>

    <div class="action-row">
      <button class="btn btn-hyphen small" on:click={handleAutoHyphenate} disabled={$isProcessing || !lyricsText.trim()}>
        ✂️ Auto-Hyphenate
      </button>
    </div>
    <div class="generate-row">
      <button class="btn btn-primary btn-generate" on:click={() => handleSubmit(false)} disabled={$isProcessing || !lyricsText.trim() || !artist.trim() || !title.trim() || !$sessionId}>
        🚀 Generate Full Ultrastar Files
      </button>
      <button class="btn btn-secondary btn-generate" on:click={handleGenerateEmpty} disabled={$isProcessing || !$sessionId || !artist.trim() || !title.trim()} title="Skip note alignment — open editor with empty file">
        📄 Generate Empty Ultrastar File
      </button>
      {#if cleanedAudioAvailable}
        <button class="btn btn-generate-cleaned btn-generate" on:click={() => handleSubmit(true)} disabled={$isProcessing || !lyricsText.trim() || !artist.trim() || !title.trim() || !$sessionId}>
          ✨ Generate from Cleaned Vocals
        </button>
      {/if}
    </div>
    {#if !$isProcessing}
      {@const missing = [!artist.trim() && 'Artist', !title.trim() && 'Title', !lyricsText.trim() && 'Lyrics'].filter(Boolean)}
      {#if missing.length > 0}
        <p class="missing-hint">Required for full generate: {missing.join(', ')}</p>
      {/if}
    {/if}
  {/if}
  <style>
    .lang-warning {
      color: #b00;
      font-weight: bold;
      margin-top: 0.5rem;
    }
  </style>

  {#if $lyricsData.preview.length > 0 && !$generationModalOpen}
    <!-- <div class="preview-section">
      <h3>Syllable Preview ({$lyricsData.syllableCount} syllables, {$lyricsData.lineCount} lines)</h3>
      <div class="preview-lines">
        {#each $lyricsData.preview as line}
          <div class="preview-line">
            <span class="line-num">L{line.line}</span>
            <div class="syllables">
              {#each line.syllables as syl}
                <span class="syllable">{syl}</span>
              {/each}
            </div>
          </div>
        {/each}
      </div>
    </div> -->
  {/if}

  {#if $processingStatus}
    <div class="status-bar">{$processingStatus}</div>
  {/if}
  {#if $errorMessage}
    <div class="error-bar">❌ {$errorMessage}</div>
  {/if}
</div>

      {#if false}{/if}

{#if whisperFallbackWarning}
  <div class="warning-modal-overlay" on:click={() => whisperFallbackWarning = false}>
    <div class="warning-modal" on:click|stopPropagation>
      <h2>⚠️ WhisperX unavailable</h2>
      <p class="warning-modal-msg">WhisperX failed and vanilla Whisper was used instead. Word timestamps are less precise and char-level sync is not available — results may need more manual correction in the editor.</p>
      <p class="warning-modal-hint">Check the backend log for details. You can still proceed.</p>
      <button on:click={() => whisperFallbackWarning = false}>Dismiss</button>
    </div>
  </div>
{/if}

{#if transcribeModalOpen}
  <div class="transcribe-modal-backdrop">
    <div class="transcribe-modal-box">
      <div class="transcribe-modal-header">
        {#if transcribePhase === 'done'}
          <span class="phase-icon">✅</span>
          <h2>Transcription Complete</h2>
        {:else if transcribePhase === 'error'}
          <span class="phase-icon">❌</span>
          <h2>Transcription Failed</h2>
        {:else}
          <span class="t-spinner"></span>
          <h2>Generating Lyrics…</h2>
        {/if}
      </div>
      <p class="transcribe-modal-status">{transcribeStatus}</p>
      {#if transcribePhase === 'transcribing'}
        <div class="transcribe-elapsed">⏱ {transcribeElapsed}s elapsed</div>
        <div class="transcribe-hint">
          Whisper listens to the full vocal track.<br>
          Typical songs take <strong>30–120 seconds</strong>.
        </div>
      {/if}
      <div class="transcribe-modal-footer">
        <button class="btn btn-cancel" on:click={cancelTranscription}>
          {isTranscribing ? '✕ Cancel' : '← Close'}
        </button>
      </div>
    </div>
  </div>
{/if}

{#if emptyGenerating}
  <div class="empty-gen-overlay">
    <div class="empty-gen-card">
      <div class="empty-gen-spinner"></div>
      <p>Creating empty Ultrastar file…</p>
    </div>
  </div>
{/if}

<style>
  .warning-modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 9999;
  }

  .warning-modal {
    background: #1e2330;
    border: 1px solid #f5a623;
    border-radius: 10px;
    padding: 1.5rem 2rem;
    max-width: 520px;
    width: 90%;
    display: flex;
    flex-direction: column;
    gap: 0.8rem;
  }

  .warning-modal h2 {
    color: #f5a623;
    margin: 0;
    font-size: 1.1rem;
  }

  .warning-modal-msg {
    color: #ffd180;
    font-size: 0.88rem;
    line-height: 1.5;
    margin: 0;
  }

  .warning-modal-hint {
    color: #888;
    font-size: 0.8rem;
    margin: 0;
  }

  .warning-modal button {
    align-self: flex-end;
    background: #f5a623;
    color: #1a1a1a;
    border: none;
    border-radius: 6px;
    padding: 0.4rem 1.2rem;
    cursor: pointer;
    font-size: 0.9rem;
    font-weight: 600;
  }

  h2 { color: #4fc3f7; margin-bottom: 1rem; }
  h3 { color: #aaa; margin: 1rem 0 0.5rem; font-size: 0.95rem; }

  .form-row {
    display: flex;
    gap: 1rem;
  }

  .form-group {
    margin-bottom: 1rem;
  }

  .form-group.half {
    flex: 1;
  }

  label {
    display: block;
    color: #aaa;
    font-size: 0.85rem;
    margin-bottom: 0.3rem;
  }

  .hint {
    color: #666;
    font-size: 0.75rem;
  }

  .required {
    color: #e57373;
    font-size: 0.8rem;
  }

  .input-missing {
    border-color: #555 !important;
    background: #1e1a1a !important;
  }

  .missing-hint {
    font-size: 0.8rem;
    color: #888;
    margin: 0.3rem 0 0;
    text-align: right;
  }

  input, select, textarea {
    width: 100%;
    padding: 0.6rem;
    border: 1px solid #444;
    border-radius: 6px;
    background: #1a1a2e;
    color: #eee;
    font-size: 0.9rem;
    font-family: inherit;
    box-sizing: border-box;
  }

  textarea {
    resize: vertical;
    font-family: 'Courier New', monospace;
    line-height: 1.5;
  }

  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: #4fc3f7;
  }

  .btn-transcribe { background: #6a1b9a; color: white; font-size: 0.85rem; padding: 0.5rem 1rem; }
  .btn-transcribe { white-space: nowrap; }
  .btn-transcribe:hover:not(:disabled) { background: #8e24aa; }
  .btn-transcribe:disabled { opacity: 0.5; cursor: not-allowed; }

  .audio-section {
    background: linear-gradient(135deg, #1a3a3a 0%, #1a2e3a 100%);
    border: 1px solid #2a7a7a;
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1.5rem;
  }

  .audio-section audio {
    width: 100%;
    margin-bottom: 0.75rem;
  }

  .cleanup-audio-compare {
    margin-top: 1rem;
    border-top: 1px solid #2a4a2a;
    padding-top: 0.75rem;
  }

  .cleanup-audio-compare audio {
    width: 100%;
    margin-bottom: 0.5rem;
  }

  .cleanup-transcribe-row {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    margin-top: 0.5rem;
  }

  .cleanup-transcribe-row .transcribe-hint {
    margin: 0;
    padding-top: 0.6rem;
  }

  .transcribe-area {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    margin-bottom: 16px;
  }

  .transcribe-hint {
    color: #888;
    font-size: 0.8rem;
    line-height: 1.4;
    margin: 0;
  }

  .no-audio-warning, .no-vocals-warning {
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin-bottom: 0.75rem;
    font-size: 0.85rem;
  }

  .no-audio-warning {
    background: #3e1a1a;
    border: 1px solid #c62828;
    color: #ef9a9a;
  }

  .no-vocals-warning {
    background: #3e2e1a;
    border: 1px solid #e65100;
    color: #ffcc80;
  }

  .no-audio-warning p, .no-vocals-warning p {
    margin: 0;
  }

  .link-btn {
    background: none;
    border: none;
    color: #4fc3f7;
    cursor: pointer;
    text-decoration: underline;
    font-size: inherit;
    padding: 0;
  }
  .link-btn:hover { color: #81d4fa; }

  .transcribe-info {
    background: #1a0e2e;
    border: 1px solid #6a1b9a;
    border-radius: 6px;
    padding: 0.5rem 0.75rem;
    color: #ce93d8;
    font-size: 0.8rem;
    margin-bottom: 1rem;
  }

  .cleanup-banner {
    background: linear-gradient(135deg, #1a3a3a 0%, #1a2e3a 100%);
    border: 1px solid #2a7a7a;
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1.5rem;
  }

  .cleanup-banner-content {
    display: flex;
    align-items: flex-start;
    gap: 1rem;
  }

  .cleanup-banner-icon {
    font-size: 1.5rem;
    flex-shrink: 0;
    line-height: 1;
  }

  .cleanup-banner-text {
    flex: 1;
  }

  .cleanup-banner-text strong {
    display: block;
    color: #4fc3f7;
    font-size: 0.95rem;
    margin-bottom: 0.3rem;
  }

  .cleanup-banner-hint {
    color: #aaa;
    font-size: 0.8rem;
    line-height: 1.4;
    margin: 0;
  }

  .btn-cleanup {
    background: #00897b;
    color: white;
    padding: 0.5rem 1rem;
    font-size: 0.85rem;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .btn-cleanup:hover:not(:disabled) {
    background: #00a89a;
  }

  .btn-cleanup:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .cleanup-status {
    display: flex;
    align-items: center;
  }

  .cleanup-status-badge {
    padding: 0.4rem 1rem;
    border-radius: 6px;
    font-size: 0.85rem;
    font-weight: 600;
    flex-shrink: 0;
  }

  .cleanup-status-badge.generating {
    background: #1a3a1a;
    color: #4fc3f7;
    border: 1px solid #2a7a2a;
  }

  .cleanup-status-badge.ready {
    background: #1a3a1a;
    color: #81c784;
    border: 1px solid #2a7a2a;
  }

  .audio-label {
    color: #aaa;
    font-size: 0.85rem;
    font-weight: 600;
    margin-bottom: 0.4rem;
    margin-top: 1rem;
  }

  .audio-label:first-child {
    margin-top: 0;
  }

  .action-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }

  .generate-row {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 28px;
    flex-wrap: wrap;
  }

  .btn-generate {
    padding: 0.9rem 2rem;
    font-size: 1rem;
  }

  .btn-generate-cleaned {
    background: #1a3a1a;
    color: #81c784;
    border: 1px solid #2a7a2a;
  }

  .btn-generate-cleaned:hover:not(:disabled) {
    background: #2a4a2a;
  }

  .btn {
    display: inline-block;
    padding: 0.75rem 1.5rem;
    border: none;
    border-radius: 8px;
    font-size: 0.9rem;
    cursor: pointer;
    transition: all 0.2s;
  }
  .btn.small { font-size: 0.8rem; padding: 0.5rem 1rem; }
  .btn-primary { background: #1976d2; color: white; }
  .btn-primary:hover:not(:disabled) { background: #1565c0; }
  .btn-secondary { background: #333; color: #ccc; border: 1px solid #555; }
  .btn-secondary:hover:not(:disabled) { background: #444; }
  .btn-hyphen { background: #e65100; color: white; }
  .btn-hyphen:hover:not(:disabled) { background: #f57c00; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .hyphenation-info {
    margin-top: 0.75rem;
    padding: 0.75rem;
    background: #2e1a00;
    border: 1px solid #e65100;
    border-radius: 8px;
    color: #ffcc80;
    font-size: 0.85rem;
  }

  .hyphenation-info .hint {
    color: #999;
    margin-top: 0.3rem;
  }

  .preview-section {
    margin-top: 1rem;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 1rem;
    background: #111;
    max-height: 300px;
    overflow-y: auto;
  }

  .preview-line {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    margin-bottom: 0.4rem;
  }

  .line-num {
    color: #666;
    font-size: 0.75rem;
    min-width: 2rem;
    padding-top: 0.2rem;
  }

  .syllables {
    display: flex;
    flex-wrap: wrap;
    gap: 0.2rem;
  }

  .syllable {
    background: #1a2e4a;
    border: 1px solid #2a4a6e;
    border-radius: 4px;
    padding: 0.15rem 0.4rem;
    font-size: 0.8rem;
    color: #4fc3f7;
    font-family: 'Courier New', monospace;
  }

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

  /* Transcription modal */
  .transcribe-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.75);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }

  .transcribe-modal-box {
    background: #1a1a2e;
    border: 1px solid #333;
    border-radius: 12px;
    padding: 2rem;
    width: 90%;
    max-width: 480px;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  .transcribe-modal-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }

  .transcribe-modal-header h2 {
    margin: 0;
  }

  .phase-icon {
    font-size: 1.4rem;
    line-height: 1;
  }

  .t-spinner {
    width: 20px;
    height: 20px;
    border: 3px solid #333;
    border-top-color: #4fc3f7;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .transcribe-modal-status {
    color: #aaa;
    font-size: 0.9rem;
    margin: 0;
  }

  .transcribe-elapsed {
    font-size: 0.85rem;
    color: #4fc3f7;
    font-variant-numeric: tabular-nums;
  }

  .transcribe-hint {
    font-size: 0.82rem;
    color: #666e7a;
    line-height: 1.5;
  }

  .transcribe-hint strong {
    color: #aaa;
  }

  .transcribe-modal-footer {
    display: flex;
    justify-content: flex-start;
    margin-top: 0.5rem;
  }

  .empty-gen-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .empty-gen-card {
    background: #1e2230;
    border: 1px solid #3a3f5c;
    border-radius: 12px;
    padding: 2rem 3rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1rem;
    color: #e0e0e0;
  }
  .empty-gen-spinner {
    width: 40px;
    height: 40px;
    border: 4px solid #3a3f5c;
    border-top-color: #7c6af7;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
