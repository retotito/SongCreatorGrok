<script>
  import { createEventDispatcher } from 'svelte';

  export let open = false;

  const dispatch = createEventDispatcher();

  function close() { open = false; }
  function fix() { dispatch('fix'); open = false; }
</script>

{#if open}
  <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
  <div class="modal-overlay" on:click={close} role="dialog" aria-label="Fix Spaces" tabindex="-1">
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
    <div class="modal-content" on:click|stopPropagation role="document">
      <div class="modal-header">
        <h3>🔤 Fix Spaces</h3>
        <button class="modal-close" on:click={close}>✕</button>
      </div>

      <div class="modal-body">
        <p>
          Older Ultrastar songs (and some imports) use <strong>leading spaces</strong> to separate
          words — the space sits at the <em>start</em> of a syllable rather than at the
          <em>end</em> of the previous one. This editor uses the modern convention where the space
          lives at the <em>end</em> of the last syllable in a word (shown as <span class="dot">·</span>).
        </p>
        <p>
          This fix only <strong>moves</strong> existing spaces — it never adds or removes word
          boundaries.
        </p>

        <div class="example">
          <div class="example-col">
            <div class="example-label">Before <span class="tag old">old style</span></div>
            <div class="syllables">
              <span class="syl">You</span><span class="syl leading">·make</span><span class="syl leading">·me</span><span class="syl leading">·feel</span>
            </div>
            <div class="hint">Space before the syllable (leading)</div>
          </div>
          <div class="arrow">→</div>
          <div class="example-col">
            <div class="example-label">After <span class="tag new">new style</span></div>
            <div class="syllables">
              <span class="syl trailing">You·</span><span class="syl trailing">make·</span><span class="syl trailing">me·</span><span class="syl">feel</span>
            </div>
            <div class="hint">Space after the syllable (trailing)</div>
          </div>
        </div>
      </div>

      <div class="modal-actions">
        <button class="btn btn-secondary" on:click={close}>Cancel</button>
        <button class="btn btn-primary" on:click={fix}>Fix Spaces Now</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .modal-content {
    background: #1e1e1e;
    border: 1px solid #444;
    border-radius: 10px;
    padding: 1.5rem;
    width: min(520px, 92vw);
    color: #eee;
    font-size: 0.9rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .modal-header h3 { margin: 0; font-size: 1.05rem; }
  .modal-close {
    background: none; border: none; color: #aaa; font-size: 1.1rem;
    cursor: pointer; padding: 0 4px;
  }
  .modal-close:hover { color: #fff; }
  .modal-body { display: flex; flex-direction: column; gap: 0.75rem; }
  .modal-body p { margin: 0; line-height: 1.55; color: #ccc; }
  .dot { color: #4fc3f7; font-weight: bold; }

  .example {
    display: flex;
    align-items: center;
    gap: 1rem;
    background: #111;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 1rem;
    margin-top: 0.25rem;
  }
  .example-col { flex: 1; display: flex; flex-direction: column; gap: 0.4rem; }
  .example-label { font-size: 0.78rem; color: #888; display: flex; align-items: center; gap: 0.4rem; }
  .tag {
    font-size: 0.68rem; padding: 1px 6px; border-radius: 4px; font-weight: 600;
  }
  .tag.old { background: #5a2a2a; color: #f87171; }
  .tag.new { background: #1a3a2a; color: #4ade80; }
  .syllables { display: flex; flex-wrap: wrap; gap: 2px; }
  .syl {
    background: #2a4a6a; color: #fff; padding: 3px 7px;
    border-radius: 4px; font-size: 0.85rem; font-family: monospace;
  }
  .syl.leading { background: #4a2a2a; }
  .syl.leading::first-letter { color: #f87171; }
  .syl.trailing { background: #1a3a2a; }
  .hint { font-size: 0.72rem; color: #666; }
  .arrow { font-size: 1.4rem; color: #666; align-self: center; }

  .modal-actions {
    display: flex; justify-content: flex-end; gap: 0.6rem; padding-top: 0.25rem;
  }
  .btn {
    padding: 0.45rem 1rem; border-radius: 6px; border: none;
    cursor: pointer; font-size: 0.88rem; font-weight: 500;
  }
  .btn-secondary { background: #333; color: #ccc; }
  .btn-secondary:hover { background: #444; }
  .btn-primary { background: #238636; color: #fff; }
  .btn-primary:hover { background: #2ea043; }
</style>
