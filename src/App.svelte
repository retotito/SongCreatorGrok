<script>
  import TopMenu from './components/TopMenu.svelte';
  import GenerateSong from './components/GenerateSong.svelte';
  import ManageTraining from './components/ManageTraining.svelte';
  import { activeTab } from './stores/appStore.js';
</script>

<main>
  <h1>Ultrastar Song Generator</h1>
  <p>Upload an audio file or paste a YouTube URL to generate an Ultrastar song file.</p>

  <TopMenu />

  {#if $activeTab === 'generate'}
    <GenerateSong />
  {:else if $activeTab === 'manage'}
    <ManageTraining />
  {/if}
</main>

<style>
  main {
    text-align: center;
    padding: 1em;
    max-width: 600px;
    margin: 0 auto;
  }

  h1 {
    color: #ff3e00;
    text-transform: uppercase;
    font-size: 2em;
    font-weight: 100;
  }
</style>