<script>
  import { sessionId, lyricsData, currentStep, isProcessing, processingStatus, errorMessage } from '../stores/appStore.js';
  import { submitLyrics, getTestLyrics, loadTestSession, hyphenateLyrics } from '../services/api.js';

  // If coming from test session, lyrics may already be loaded
  let lyricsText = '';
  let artist = '';
  let title = '';
  let language = 'en';
  let hyphenationResult = null;

  // Sync from store if test session loaded
  $: if ($lyricsData.text) {
    lyricsText = $lyricsData.text;
    artist = $lyricsData.artist || artist;
    title = $lyricsData.title || title;
  }

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

  async function handleFileUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    
    const text = await file.text();
    lyricsText = text;
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

  async function handleSubmit() {
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
    } catch (err) {
      errorMessage.set(err.message);
    } finally {
      isProcessing.set(false);
    }
  }

  // Check if we came from a test session with lyrics pre-loaded
  async function checkTestSession() {
    if ($sessionId && $sessionId.startsWith('test-')) {
      try {
        const result = await getTestLyrics();
        lyricsText = result.lyrics;
        artist = 'U2';
        title = 'Beautiful Day';
        // Auto-submit
        await handleSubmit();
      } catch (e) {
        // Test lyrics not available, user can enter manually
      }
    }
  }

  $: if ($currentStep === 2 && $sessionId) {
    checkTestSession();
  }
</script>

<div class="step-content">
  <h2>Step 2: Lyrics</h2>

  <div class="form-row">
    <div class="form-group half">
      <label for="artist">Artist</label>
      <input id="artist" type="text" bind:value={artist} placeholder="Artist name" />
    </div>
    <div class="form-group half">
      <label for="title">Title</label>
      <input id="title" type="text" bind:value={title} placeholder="Song title" />
    </div>
  </div>

  <div class="form-group">
    <label for="language">Language</label>
    <select id="language" bind:value={language}>
      <option value="en">English</option>
      <option value="de">German</option>
      <option value="fr">French</option>
      <option value="es">Spanish</option>
      <option value="it">Italian</option>
    </select>
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
    <label class="btn btn-secondary small">
      📂 Upload .txt
      <input type="file" accept=".txt" on:change={handleFileUpload} hidden />
    </label>
    <button class="btn btn-hyphen small" on:click={handleAutoHyphenate} disabled={$isProcessing || !lyricsText.trim()}>
      ✂️ Auto-Hyphenate
    </button>
    <button class="btn btn-test small" on:click={handleLoadTestLyrics}>
      🧪 Load Test Lyrics
    </button>
    <button class="btn btn-primary" on:click={handleSubmit} disabled={$isProcessing || !lyricsText.trim()}>
      Validate & Continue
    </button>
  </div>

  {#if hyphenationResult}
    <div class="hyphenation-info">
      <p>Auto-hyphenated with <strong>{hyphenationResult.method}</strong> ({hyphenationResult.language})</p>
      <p class="hint">Review and correct the hyphens above, then click "Validate & Continue".</p>
    </div>
  {/if}

  {#if $lyricsData.preview.length > 0}
    <div class="preview-section">
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

  .action-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
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
  .btn-test { background: #2e7d32; color: white; }
  .btn-test:hover { background: #388e3c; }
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
</style>
