<script>
  import { tick } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  const dispatch = createEventDispatcher();

  export let notes = [];
  export let open = false;
  // Bindable outputs — parent reads these in the canvas draw loop
  export let matches = [];
  export let matchIndex = 0;

  let findQuery = '';
  let inputEl;

  $: matches = (() => {
    if (!open || !findQuery.trim()) return [];
    const q = findQuery.trim().toLowerCase();
    return notes
      .map((n, i) => ({ note: n, idx: i }))
      .filter(({ note }) => note.type !== 'break' && note.syllable.trim().toLowerCase().includes(q));
  })();

  $: if (open) tick().then(() => inputEl?.focus());

  function jump(index) {
    if (!matches.length) return;
    matchIndex = ((index % matches.length) + matches.length) % matches.length;
    dispatch('jump', { note: matches[matchIndex].note });
  }

  export function close() {
    open = false;
    findQuery = '';
    matchIndex = 0;
    dispatch('close');
  }
</script>

{#if open}
  <!-- svelte-ignore a11y-autofocus -->
  <div class="find-widget">
    <span class="find-icon">⌕</span>
    <input
      bind:this={inputEl}
      type="text"
      placeholder="Find syllable…"
      bind:value={findQuery}
      on:input={() => { matchIndex = 0; if (matches.length) jump(0); else dispatch('redraw'); }}
      on:keydown|stopPropagation={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); jump(matchIndex + (e.shiftKey ? -1 : 1)); }
        if (e.key === 'Escape') { e.preventDefault(); close(); }
      }}
    />
    <span class="find-count">{findQuery.trim() ? (matches.length ? `${matchIndex + 1} / ${matches.length}` : 'No results') : ''}</span>
    <button class="find-nav-btn" on:click={() => jump(matchIndex - 1)} title="Previous (Shift+Enter)" disabled={matches.length < 2}>↑</button>
    <button class="find-nav-btn" on:click={() => jump(matchIndex + 1)} title="Next (Enter)" disabled={matches.length < 2}>↓</button>
    <button class="find-close-btn" on:click={close} title="Close (Escape)">✕</button>
  </div>
{/if}

<style>
  .find-widget {
    position: absolute;
    top: 8px;
    right: 12px;
    z-index: 200;
    display: flex;
    align-items: center;
    gap: 4px;
    background: #1e1e1e;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 4px 6px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.5);
    font-size: 13px;
  }
  .find-icon {
    color: #888;
    font-size: 15px;
    line-height: 1;
    user-select: none;
  }
  .find-widget input {
    background: #2d2d2d;
    border: 1px solid #555;
    border-radius: 3px;
    color: #eee;
    font-size: 13px;
    padding: 2px 6px;
    width: 180px;
    outline: none;
  }
  .find-widget input:focus {
    border-color: #4fc3f7;
  }
  .find-count {
    color: #999;
    font-size: 11px;
    min-width: 52px;
    text-align: center;
    white-space: nowrap;
  }
  .find-nav-btn {
    background: none;
    border: none;
    color: #ccc;
    cursor: pointer;
    font-size: 14px;
    padding: 2px 4px;
    border-radius: 3px;
    line-height: 1;
  }
  .find-nav-btn:hover:not(:disabled) {
    background: #333;
    color: #fff;
  }
  .find-nav-btn:disabled {
    color: #555;
    cursor: default;
  }
  .find-close-btn {
    background: none;
    border: none;
    color: #888;
    cursor: pointer;
    font-size: 13px;
    padding: 2px 5px;
    border-radius: 3px;
    line-height: 1;
    margin-left: 2px;
  }
  .find-close-btn:hover {
    background: #333;
    color: #fff;
  }
</style>
