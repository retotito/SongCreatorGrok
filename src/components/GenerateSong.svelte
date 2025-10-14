<script>
  import {
    currentStep,
    progress,
    status,
    result,
    correctedVocal,
    file,
    youtubeUrl,
    lyrics,
    artist,
    title,
    referenceVocal,
    voiceType,
    language,
    skipDemucs,
    skipCrepe,
    skipWhisper
  } from '../stores/generateStore.js';

  function handleFileChange(event) {
    file.set(event.target.files?.[0] || null);
  }

  function handleReferenceChange(event) {
    referenceVocal.set(event.target.files?.[0] || null);
  }

  function handleCorrectedChange(event) {
    correctedVocal.set(event.target.files?.[0] || null);
  }

  // Stage 1: Extract vocals for manual cleanup
  async function extractVocals() {
    if (!$file && !$youtubeUrl.trim()) {
      alert('Please upload a file or enter a YouTube URL');
      return;
    }

    currentStep.set('processing');
    progress.set(0);
    status.set('Extracting vocals... This may take a few minutes.');

    const formData = new FormData();
    if ($file) formData.append('file', $file);
    if ($youtubeUrl.trim()) formData.append('youtube_url', $youtubeUrl.trim());
    if ($lyrics.trim()) formData.append('lyrics', $lyrics.trim());
    if ($artist.trim()) formData.append('artist', $artist.trim());
    if ($title.trim()) formData.append('title', $title.trim());
    formData.append('language', $language);
    formData.append('voice_type', $voiceType);

    try {
      console.log('Sending extract vocals request...');
      
      // Create AbortController for timeout
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000); // 2 minute timeout
      
      const response = await fetch('http://localhost:8001/extract_vocals', {
        method: 'POST',
        body: formData,
        signal: controller.signal
      });
      
      clearTimeout(timeoutId);
      console.log('Extract vocals response status:', response.status);
      if (response.ok) {
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        result.set({ extracted_vocals_url: url });
        status.set('Vocals extracted! Please download, clean up the audio, and upload the corrected version.');
        currentStep.set('vocals_extracted');
      } else {
        const error = await response.text();
        status.set(`Error extracting vocals: ${error}`);
        currentStep.set('upload');
      }
    } catch (error) {
      console.error('Extract vocals error:', error);
      if (error.name === 'AbortError') {
        status.set('Request timeout - vocal extraction took too long. Please try with a shorter audio file.');
      } else {
        status.set(`Network error: ${error.message}`);
      }
      currentStep.set('upload');
    }
  }

  // Stage 2: Generate final files with corrected vocals
  async function generateFinalFiles() {
    if (!$correctedVocal) {
      alert('Please upload the corrected vocal file');
      return;
    }

    currentStep.set('processing');
    progress.set(0);
    status.set('Generating final Ultrastar files...');

    const formData = new FormData();
    formData.append('corrected_vocal', $correctedVocal);
    if ($lyrics.trim()) formData.append('lyrics', $lyrics.trim());
    if ($artist.trim()) formData.append('artist', $artist.trim());
    if ($title.trim()) formData.append('title', $title.trim());
    formData.append('language', $language);
    formData.append('voice_type', $voiceType);

    try {
      console.log('Sending generate final files request...');
      
      // Create AbortController for timeout
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000); // 2 minute timeout
      
      const response = await fetch('http://localhost:8001/generate_final_files', {
        method: 'POST',
        body: formData,
        signal: controller.signal
      });
      
      clearTimeout(timeoutId);
      console.log('Generate final files response status:', response.status);
      if (response.ok) {
        const resultData = await response.json();
        result.set(resultData);
        status.set('Processing complete! Download your Ultrastar files.');
        currentStep.set('result');
      } else {
        const error = await response.text();
        status.set(`Error generating files: ${error}`);
        currentStep.set('vocals_extracted');
      }
    } catch (error) {
      console.error('Generate final files error:', error);
      if (error.name === 'AbortError') {
        status.set('Request timeout - file generation took too long. Please try again.');
      } else {
        status.set(`Network error: ${error.message}`);
      }
      currentStep.set('vocals_extracted');
    }
  }

  // Test button: Load test files from frontendTest folder
  async function loadTestFiles() {
    currentStep.set('processing');
    progress.set(0);
    status.set('Loading test files...');

    try {
      console.log('Loading test files...');
      // Use the direct URL to the backend endpoint
      const directUrl = 'http://localhost:8001/test_files';
      
      // Test that the endpoint is working
      const testResponse = await fetch(directUrl);
      if (!testResponse.ok) {
        throw new Error(`Test files endpoint returned ${testResponse.status}`);
      }
      
      // Try to load test lyrics from the new endpoint
      try {
        const lyricsResponse = await fetch('http://localhost:8001/test_lyrics');
        if (lyricsResponse.ok) {
          const testLyrics = await lyricsResponse.text();
          lyrics.set(testLyrics);
        }
      } catch (e) {
        console.log('Test lyrics endpoint not available, using default lyrics');
        lyrics.set('Test lyrics from frontendTest folder - replace with actual lyrics for your vocal file');
      }
      
      result.set({ 
        extracted_vocals_url: 'http://localhost:8001/test_vocal',
        is_test_mode: true  // Flag to indicate this is test mode
      });
      artist.set('Test Artist');
      title.set('Test Song');
      status.set('Test files loaded! You can proceed directly to step 2.');
      currentStep.set('vocals_extracted');
    } catch (error) {
      console.error('Load test files error:', error);
      status.set(`Network error loading test files: ${error.message}`);
      currentStep.set('upload');
    }
  }

  // Download file handler
  async function downloadFile(url, filename) {
    try {
      const response = await fetch(url);
      const blob = await response.blob();
      
      // Create a temporary URL for the blob
      const downloadUrl = window.URL.createObjectURL(blob);
      
      // Create a temporary link element
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = filename;
      
      // Append to body, click, and remove
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      // Clean up the object URL
      window.URL.revokeObjectURL(downloadUrl);
    } catch (error) {
      console.error('Failed to download file:', error);
    }
  }

  // Legacy function for backward compatibility
  async function handleSubmit() {
    extractVocals();
  }

  async function reprocessWithCorrected() {
    generateFinalFiles();
  }
</script>

{#if $currentStep === 'upload'}
  <div>
    <h2>Stage 1: Extract Vocals</h2>
    <p>Upload your MP3 file and lyrics to extract vocals for manual cleanup.</p>
    
    <form on:submit|preventDefault={extractVocals}>
      <div>
        <label for="file">Upload MP3:</label>
        <input type="file" id="file" accept=".mp3" on:change={handleFileChange} disabled={$currentStep === 'processing'} />
      </div>

      <div>
        <label for="youtube">YouTube URL:</label>
        <input type="url" id="youtube" bind:value={$youtubeUrl} placeholder="https://www.youtube.com/watch?v=..." disabled={$currentStep === 'processing'} />
      </div>

      <div>
        <label for="artist">Artist (optional):</label>
        <input type="text" id="artist" bind:value={$artist} placeholder="e.g., The B-52's" disabled={$currentStep === 'processing'} />
      </div>

      <div>
        <label for="title">Title (optional):</label>
        <input type="text" id="title" bind:value={$title} placeholder="e.g., Love Shack" disabled={$currentStep === 'processing'} />
      </div>

      <div>
        <label for="lyrics">Lyrics (required):</label>
        <textarea id="lyrics" bind:value={$lyrics} placeholder="Paste lyrics here..." rows="10" disabled={$currentStep === 'processing'}></textarea>
      </div>

      <div>
        <label for="language">Language:</label>
        <select id="language" bind:value={$language}>
          <option value="en">English</option>
          <option value="es">Spanish</option>
          <option value="fr">French</option>
          <option value="de">German</option>
          <option value="it">Italian</option>
        </select>
      </div>

      <div>
        <label for="voiceTypeGen">Voice Type:</label>
        <select id="voiceTypeGen" bind:value={$voiceType}>
          <option value="solo">Solo Voice</option>
          <option value="background">With Background Singers</option>
          <option value="2voices">2 Voices</option>
          <option value="rap">Rap</option>
          <option value="rap_singing">Rap + Singing</option>
        </select>
      </div>

      <div style="margin: 20px 0;">
        <button type="submit" disabled={$currentStep === 'processing'}>Step 1: Extract Vocals</button>
        <button type="button" on:click={loadTestFiles} disabled={$currentStep === 'processing'} style="margin-left: 10px; background-color: #28a745; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer;">🧪 Load Test Files</button>
      </div>
    </form>
  </div>
{/if}

{#if $currentStep === 'processing'}
  <div>
    <p>{$status}</p>
    <progress value={$progress} max="100"></progress>
  </div>
{/if}

{#if $currentStep === 'vocals_extracted'}
  <div>
    <h2>Stage 1 Complete: Vocals {$result?.is_test_mode ? 'Test File Loaded' : 'Extracted'}</h2>
    {#if $result?.extracted_vocals_url}
      <div style="margin: 20px 0;">
        <a href={$result.extracted_vocals_url} download={$result?.is_test_mode ? "test_vocal.wav" : "extracted_vocals.wav"} style="display: inline-block; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 4px;">
          📥 Download {$result?.is_test_mode ? 'Test Vocal File' : 'Extracted Vocals'}
        </a>
      </div>
    {/if}
    
    {#if !$result?.is_test_mode}
      <div style="background-color: #f8f9fa; padding: 15px; margin: 20px 0; border-radius: 4px;">
        <h3>Instructions:</h3>
        <ol>
          <li>Download the extracted vocals file above</li>
          <li>Open it in an audio editor (Audacity, etc.)</li>
          <li>Clean up the audio (remove background noise, fix timing, etc.)</li>
          <li>Export as WAV or MP3</li>
          <li>Upload the corrected file below</li>
        </ol>
      </div>
    {:else}
      <div style="background-color: #d4edda; padding: 15px; margin: 20px 0; border-radius: 4px; border: 1px solid #c3e6cb;">
        <h3>🧪 Test Mode:</h3>
        <p>You've loaded the test vocal file from the frontendTest folder. This file is already a clean vocal track, so you can either:</p>
        <ul>
          <li><strong>Use as-is:</strong> Upload the same file below to test the pipeline</li>
          <li><strong>Edit it:</strong> Download, make changes in an audio editor, then upload</li>
        </ul>
      </div>
    {/if}

    <h2>Stage 2: Generate Final Files</h2>
    <div>
      <label for="correctedVocal">Upload {$result?.is_test_mode ? 'Vocal File (test or edited)' : 'Corrected Vocal File'}:</label>
      <input type="file" id="correctedVocal" accept=".wav,.mp3" on:change={handleCorrectedChange} />
    </div>
    
    <div style="margin: 20px 0;">
      <button on:click={generateFinalFiles} disabled={!$correctedVocal || $currentStep === 'processing'}>Step 2: Generate Ultrastar Files</button>
    </div>
  </div>
{/if}

{#if $currentStep === 'result'}
  <div>
    <h2>🎉 Processing Complete!</h2>
    <div style="margin: 20px 0;">
      {#if $result?.txt_url}
        <button on:click={() => downloadFile($result.txt_url, 'ultrastar.txt')} style="display: inline-block; margin: 5px; padding: 10px 20px; background-color: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer;">📄 Download Ultrastar .txt</button>
      {/if}
      {#if $result?.vocals_url}
        <button on:click={() => downloadFile($result.vocals_url, 'vocals.wav')} style="display: inline-block; margin: 5px; padding: 10px 20px; background-color: #17a2b8; color: white; border: none; border-radius: 4px; cursor: pointer;">🎤 Download Vocals .wav</button>
      {/if}
      {#if $result?.midi_url}
        <button on:click={() => downloadFile($result.midi_url, 'pitches.mid')} style="display: inline-block; margin: 5px; padding: 10px 20px; background-color: #ffc107; color: black; border: none; border-radius: 4px; cursor: pointer;">🎵 Download Pitches .mid</button>
      {/if}
      {#if $result?.midi_summary_url}
        <button on:click={() => downloadFile($result.midi_summary_url, 'pitches_summary.txt')} style="display: inline-block; margin: 5px; padding: 10px 20px; background-color: #6f42c1; color: white; border: none; border-radius: 4px; cursor: pointer;">📊 Download Pitches Summary .txt</button>
      {/if}
    </div>
    
    <div style="margin: 20px 0;">
      <button on:click={() => { currentStep.set('upload'); result.set(null); correctedVocal.set(null); file.set(null); }} style="background-color: #6c757d; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer;">🔄 Start New Song</button>
    </div>
  </div>
{/if}