<script>
  import { trainingData, selectedItem, isLoading, loadTrainingData } from '../stores/trainingStore.js';

  let mp3File = null;
  let txtFile = null;
  let trainingLyrics = '';
  let trainingArtist = '';
  let trainingTitle = '';
  let trainingReferenceVocal = null;
  let voiceType = 'solo';
  let language = 'en';

  $: duplicateWarning = mp3File && $trainingData.some(item => item.name === mp3File[0].name.replace('.mp3', ''));

  async function uploadTrainingData() {
    if (!mp3File || !txtFile) {
      alert('Please select both MP3 and TXT files');
      return;
    }

    const formData = new FormData();
    formData.append('mp3_file', mp3File[0]);
    formData.append('txt_file', txtFile[0]);
    formData.append('lyrics', trainingLyrics);
    formData.append('artist', trainingArtist);
    formData.append('title', trainingTitle);
    formData.append('language', language);
    formData.append('voice_type', voiceType);
    if (trainingReferenceVocal) formData.append('reference_vocal', trainingReferenceVocal[0]);

    try {
      const response = await fetch('http://localhost:8001/upload_training_data', {
        method: 'POST',
        body: formData,
      });
      if (response.ok) {
        alert('Training data uploaded successfully!');
        loadTrainingData();
        mp3File = null;
        txtFile = null;
        trainingReferenceVocal = null;
      } else {
        alert('Error uploading training data');
      }
    } catch (error) {
      console.error('Upload error:', error);
    }
  }

  async function selectItem(item) {
    selectedItem.set(item);
    mp3File = null;
    txtFile = null;
    trainingReferenceVocal = null;
    if (item.json) {
      try {
        const response = await fetch(`http://localhost:8001/get_training_metadata/${item.name}`);
        if (response.ok) {
          const metadata = await response.json();
          trainingLyrics = metadata.lyrics || '';
          trainingArtist = metadata.artist || '';
          trainingTitle = metadata.title || '';
          language = metadata.language || 'en';
          voiceType = metadata.voice_type || 'solo';
        }
      } catch (error) {
        console.error('Get metadata error:', error);
      }
    } else {
      trainingLyrics = '';
      trainingArtist = '';
      trainingTitle = '';
      voiceType = 'solo';
      language = 'en';
    }
  }

  async function updateMetadata() {
    if (!$selectedItem) return;

    const formData = new FormData();
    formData.append('lyrics', trainingLyrics);
    formData.append('artist', trainingArtist);
    formData.append('title', trainingTitle);
    formData.append('language', language);
    formData.append('voice_type', voiceType);

    if (mp3File && mp3File[0]) formData.append('mp3_file', mp3File[0]);
    if (txtFile && txtFile[0]) formData.append('txt_file', txtFile[0]);
    if (trainingReferenceVocal && trainingReferenceVocal[0]) formData.append('reference_vocal', trainingReferenceVocal[0]);

    try {
      const response = await fetch(`http://localhost:8001/update_training_metadata/${$selectedItem.name}`, {
        method: 'POST',
        body: formData
      });
      if (response.ok) {
        alert('Training data updated!');
        selectedItem.set(null);
        loadTrainingData();
      }
    } catch (error) {
      console.error('Update error:', error);
    }
  }

  // Load on mount
  import { onMount } from 'svelte';
  onMount(() => {
    loadTrainingData();
  });
</script>

<h3>Existing Training Data</h3>
{#if $isLoading}
  <p>Loading...</p>
{:else}
  <ul>
    {#each $trainingData as item}
      <li>
        <strong>{item.name}</strong> - {item.artist} - {item.title} ({item.voice_type}, {item.language})
        {#if item.complete}
          <span style="color: green;">Complete</span>
        {:else}
          <span style="color: red;">Incomplete (missing {item.txt ? '' : 'TXT'} {item.json ? '' : 'JSON'})</span>
        {/if}
        <button on:click={() => selectItem(item)}>Edit</button>
      </li>
    {/each}
  </ul>
{/if}

{#if $selectedItem}
  <h3>{$selectedItem.name}</h3>
  {#if $selectedItem.json}
    <p><strong>Current:</strong> Artist: {trainingArtist}, Title: {trainingTitle}, Voice Type: {voiceType}, Language: {language}</p>
    <form on:submit|preventDefault={updateMetadata}>
      <div>
        <label for="editMp3File">Upload MP3 (optional, replaces existing):</label>
        <input type="file" id="editMp3File" accept=".mp3" bind:files={mp3File} />
      </div>

      <div>
        <label for="editTxtFile">Upload TXT (Ultrastar file, optional, replaces existing):</label>
        <input type="file" id="editTxtFile" accept=".txt" bind:files={txtFile} />
      </div>

      <div>
        <label for="editLyrics">Lyrics:</label>
        <textarea id="editLyrics" bind:value={trainingLyrics} placeholder="Paste full lyrics..." rows="5" required></textarea>
      </div>

      <div>
        <label for="editArtist">Artist:</label>
        <input id="editArtist" bind:value={trainingArtist} required />
      </div>

      <div>
        <label for="editTitle">Title:</label>
        <input id="editTitle" bind:value={trainingTitle} required />
      </div>

      <div>
        <label for="editReferenceVocal">Reference Vocal (optional, upload humming/singing audio):</label>
        <input type="file" id="editReferenceVocal" accept=".wav,.mp3" bind:files={trainingReferenceVocal} />
      </div>

      <div>
        <label for="editVoiceType">Voice Type:</label>
        <select id="editVoiceType" bind:value={voiceType}>
          <option value="solo">Solo Voice</option>
          <option value="background">With Background Singers</option>
          <option value="2voices">2 Voices</option>
          <option value="rap">Rap</option>
          <option value="rap_singing">Rap + Singing</option>
        </select>
      </div>

      <div>
        <label for="editLanguage">Language:</label>
        <select id="editLanguage" bind:value={language}>
          <option value="en">English</option>
          <option value="es">Spanish</option>
          <option value="fr">French</option>
          <option value="de">German</option>
          <option value="it">Italian</option>
          <option value="pt">Portuguese</option>
          <option value="ru">Russian</option>
          <option value="ja">Japanese</option>
          <option value="zh">Chinese</option>
          <option value="ko">Korean</option>
          <option value="ar">Arabic</option>
          <option value="hi">Hindi</option>
          <option value="bn">Bengali</option>
          <option value="pa">Punjabi</option>
          <option value="jv">Javanese</option>
          <option value="ms">Malay</option>
          <option value="vi">Vietnamese</option>
          <option value="fa">Persian</option>
          <option value="tr">Turkish</option>
          <option value="pl">Polish</option>
          <option value="uk">Ukrainian</option>
          <option value="nl">Dutch</option>
          <option value="sv">Swedish</option>
          <option value="da">Danish</option>
          <option value="no">Norwegian</option>
          <option value="fi">Finnish</option>
          <option value="cs">Czech</option>
          <option value="sk">Slovak</option>
          <option value="hu">Hungarian</option>
          <option value="ro">Romanian</option>
          <option value="bg">Bulgarian</option>
          <option value="hr">Croatian</option>
          <option value="sl">Slovenian</option>
          <option value="et">Estonian</option>
          <option value="lv">Latvian</option>
          <option value="lt">Lithuanian</option>
          <option value="mt">Maltese</option>
          <option value="ga">Irish</option>
          <option value="cy">Welsh</option>
          <option value="is">Icelandic</option>
          <option value="fo">Faroese</option>
          <option value="kl">Greenlandic</option>
          <option value="sq">Albanian</option>
          <option value="mk">Macedonian</option>
          <option value="el">Greek</option>
          <option value="he">Hebrew</option>
          <option value="yi">Yiddish</option>
          <option value="ur">Urdu</option>
          <option value="ta">Tamil</option>
          <option value="te">Telugu</option>
          <option value="kn">Kannada</option>
          <option value="ml">Malayalam</option>
          <option value="si">Sinhala</option>
          <option value="th">Thai</option>
          <option value="lo">Lao</option>
          <option value="my">Burmese</option>
          <option value="km">Khmer</option>
          <option value="am">Amharic</option>
          <option value="ti">Tigrinya</option>
          <option value="om">Oromo</option>
          <option value="so">Somali</option>
          <option value="sw">Swahili</option>
          <option value="rw">Kinyarwanda</option>
          <option value="lg">Luganda</option>
          <option value="ha">Hausa</option>
          <option value="yo">Yoruba</option>
          <option value="ig">Igbo</option>
          <option value="zu">Zulu</option>
          <option value="xh">Xhosa</option>
          <option value="af">Afrikaans</option>
          <option value="st">Sesotho</option>
          <option value="tn">Tswana</option>
          <option value="ts">Tsonga</option>
          <option value="ve">Venda</option>
          <option value="nr">Ndebele</option>
          <option value="ss">Swati</option>
          <option value="nso">Northern Sotho</option>
        </select>
      </div>

      <button type="submit">Update Metadata</button>
      <button type="button" on:click={() => { selectedItem.set(null); mp3File = null; txtFile = null; trainingReferenceVocal = null; }}>Cancel</button>
    </form>
  {/if}
{:else}
  <form on:submit|preventDefault={uploadTrainingData}>
    <div>
      <label for="mp3File">Upload MP3:</label>
      <input type="file" id="mp3File" accept=".mp3" bind:files={mp3File} required />
      {#if duplicateWarning}
        <span style="color: red;"> (Already exists)</span>
      {/if}
    </div>

    <div>
      <label for="txtFile">Upload TXT (Ultrastar file):</label>
      <input type="file" id="txtFile" accept=".txt" bind:files={txtFile} required />
    </div>

    <div>
      <label for="trainingLyrics">Lyrics:</label>
      <textarea id="trainingLyrics" bind:value={trainingLyrics} placeholder="Paste full lyrics..." rows="5" required></textarea>
    </div>

    <div>
      <label for="trainingArtist">Artist:</label>
      <input type="text" id="trainingArtist" bind:value={trainingArtist} placeholder="e.g., The B-52's" required />
    </div>

    <div>
      <label for="trainingTitle">Title:</label>
      <input type="text" id="trainingTitle" bind:value={trainingTitle} placeholder="e.g., Love Shack" required />
    </div>

    <div>
      <label for="trainingReferenceVocal">Reference Vocal (optional, upload humming/singing audio):</label>
      <input type="file" id="trainingReferenceVocal" accept=".wav,.mp3" bind:files={trainingReferenceVocal} />
    </div>

    <div>
      <label for="voiceType">Voice Type:</label>
      <select id="voiceType" bind:value={voiceType}>
        <option value="solo">Solo Voice</option>
        <option value="background">With Background Singers</option>
        <option value="2voices">2 Voices</option>
        <option value="rap">Rap</option>
        <option value="rap_singing">Rap + Singing</option>
      </select>
    </div>

    <div>
      <label for="trainingLanguage">Language:</label>
      <select id="trainingLanguage" bind:value={language}>
        <option value="en">English</option>
        <option value="es">Spanish</option>
        <option value="fr">French</option>
        <option value="de">German</option>
        <option value="it">Italian</option>
        <option value="pt">Portuguese</option>
        <option value="ru">Russian</option>
        <option value="ja">Japanese</option>
        <option value="zh">Chinese</option>
        <option value="ko">Korean</option>
        <option value="ar">Arabic</option>
        <option value="hi">Hindi</option>
        <option value="bn">Bengali</option>
        <option value="pa">Punjabi</option>
        <option value="jv">Javanese</option>
        <option value="ms">Malay</option>
        <option value="vi">Vietnamese</option>
        <option value="fa">Persian</option>
        <option value="tr">Turkish</option>
        <option value="pl">Polish</option>
        <option value="uk">Ukrainian</option>
        <option value="nl">Dutch</option>
        <option value="sv">Swedish</option>
        <option value="da">Danish</option>
        <option value="no">Norwegian</option>
        <option value="fi">Finnish</option>
        <option value="cs">Czech</option>
        <option value="sk">Slovak</option>
        <option value="hu">Hungarian</option>
        <option value="ro">Romanian</option>
        <option value="bg">Bulgarian</option>
        <option value="hr">Croatian</option>
        <option value="sl">Slovenian</option>
        <option value="et">Estonian</option>
        <option value="lv">Latvian</option>
        <option value="lt">Lithuanian</option>
        <option value="mt">Maltese</option>
        <option value="ga">Irish</option>
        <option value="cy">Welsh</option>
        <option value="is">Icelandic</option>
        <option value="fo">Faroese</option>
        <option value="kl">Greenlandic</option>
        <option value="sq">Albanian</option>
        <option value="mk">Macedonian</option>
        <option value="el">Greek</option>
        <option value="he">Hebrew</option>
        <option value="yi">Yiddish</option>
        <option value="ur">Urdu</option>
        <option value="ta">Tamil</option>
        <option value="te">Telugu</option>
        <option value="kn">Kannada</option>
        <option value="ml">Malayalam</option>
        <option value="si">Sinhala</option>
        <option value="th">Thai</option>
        <option value="lo">Lao</option>
        <option value="my">Burmese</option>
        <option value="km">Khmer</option>
        <option value="am">Amharic</option>
        <option value="ti">Tigrinya</option>
        <option value="om">Oromo</option>
        <option value="so">Somali</option>
        <option value="sw">Swahili</option>
        <option value="rw">Kinyarwanda</option>
        <option value="lg">Luganda</option>
        <option value="ha">Hausa</option>
        <option value="yo">Yoruba</option>
        <option value="ig">Igbo</option>
        <option value="zu">Zulu</option>
        <option value="xh">Xhosa</option>
        <option value="af">Afrikaans</option>
        <option value="st">Sesotho</option>
        <option value="tn">Tswana</option>
        <option value="ts">Tsonga</option>
        <option value="ve">Venda</option>
        <option value="nr">Ndebele</option>
        <option value="ss">Swati</option>
        <option value="nso">Northern Sotho</option>
      </select>
    </div>

    <button type="submit" disabled={duplicateWarning}>Upload Training Data</button>
  </form>
{/if}