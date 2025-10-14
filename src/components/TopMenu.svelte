<script>
  import { activeTab } from '../stores/appStore.js';
</script>

<div class="tabs">
  <button on:click={() => activeTab.set('generate')} class:active={$activeTab === 'generate'}>Generate Song</button>
  <button on:click={() => activeTab.set('manage')} class:active={$activeTab === 'manage'}>Manage Training Data</button>
</div>

<style>
  .tabs {
    display: flex;
    justify-content: center;
    margin-bottom: 1em;
  }

  .tabs button {
    padding: 0.5em 1em;
    margin: 0 0.5em;
    border: 1px solid #ccc;
    background: #000;
    color: #fff;
    cursor: pointer;
  }

  .tabs button:hover {
    background: #333;
  }

  .tabs button.active {
    background: #ff3e00;
  }
</style>