<script>
  import { BACKUP_INTERVAL_OPTIONS, getStoredIntervalMin, setStoredIntervalMin, getStoredAutoEnabled, setStoredAutoEnabled } from '../lib/autoBackup.js';

  export let sessionId = '';
  export let open = false;
  /** Called after a successful restore. */
  export let onRestore = null;

  // ── State ────────────────────────────────────────────────
  let backups = [];
  let loading = false;
  let savingNow = false;
  let restoringTs = null;
  let deletingTs = null;
  let errorMsg = '';

  let autoEnabled = getStoredAutoEnabled();
  let intervalMin = getStoredIntervalMin();

  // Inline confirm dialog state
  let confirmAction = null; // { label: string, onConfirm: () => void }

  // ── Reactive: persist settings to localStorage ───────────
  $: { setStoredAutoEnabled(autoEnabled); }
  $: { setStoredIntervalMin(intervalMin); }

  // ── API helpers ──────────────────────────────────────────
  const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
  const BASE = isTauri ? 'http://localhost:8001/api' : '/api';

  async function apiFetch(method, path, body = null) {
    const opts = { method };
    if (body) {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(`${BASE}${path}`, opts);
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(text || res.statusText);
    }
    return res.json();
  }

  // ── Load backups when modal opens ────────────────────────
  $: if (open && sessionId) loadBackups();

  async function loadBackups() {
    loading = true;
    errorMsg = '';
    try {
      const data = await apiFetch('GET', `/sessions/${sessionId}/backups`);
      backups = data.backups || [];
    } catch (e) {
      errorMsg = `Failed to load backups: ${e.message}`;
    } finally {
      loading = false;
    }
  }

  // ── Backup now ───────────────────────────────────────────
  async function backupNow() {
    savingNow = true;
    errorMsg = '';
    try {
      const data = await apiFetch('POST', `/sessions/${sessionId}/backup`);
      backups = [data.backup, ...backups];
    } catch (e) {
      errorMsg = `Backup failed: ${e.message}`;
    } finally {
      savingNow = false;
    }
  }

  // ── Restore ──────────────────────────────────────────────
  function askRestore(ts) {
    confirmAction = {
      label: 'Restore this backup? Current unsaved changes will be lost.',
      confirmText: 'Restore',
      danger: false,
      onConfirm: () => doRestore(ts),
    };
  }

  async function doRestore(ts) {
    confirmAction = null;
    restoringTs = ts;
    errorMsg = '';
    try {
      const data = await apiFetch('POST', `/sessions/${sessionId}/backup/${ts}/restore`);
      open = false;
      if (onRestore) await onRestore(data);
    } catch (e) {
      errorMsg = `Restore failed: ${e.message}`;
    } finally {
      restoringTs = null;
    }
  }

  // ── Delete ───────────────────────────────────────────────
  function askDelete(ts) {
    confirmAction = {
      label: 'Delete this backup? This cannot be undone.',
      confirmText: 'Delete',
      danger: true,
      onConfirm: () => doDelete(ts),
    };
  }

  async function doDelete(ts) {
    confirmAction = null;
    deletingTs = ts;
    errorMsg = '';
    try {
      await apiFetch('DELETE', `/sessions/${sessionId}/backup/${ts}`);
      backups = backups.filter(b => b.ts !== ts);
    } catch (e) {
      errorMsg = `Delete failed: ${e.message}`;
    } finally {
      deletingTs = null;
    }
  }

  // ── Formatting ───────────────────────────────────────────
  function fmtDate(ts) {
    const d = new Date(ts);
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    const isYesterday = d.toDateString() === yesterday.toDateString();
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (isToday) return `Today ${time}`;
    if (isYesterday) return `Yesterday ${time}`;
    return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`;
  }

  function fmtSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  function close() {
    open = false;
  }
</script>

{#if open}
  <!-- svelte-ignore a11y-click-events-have-key-events -->
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div class="backup-overlay" on:click|self={close}>
    <div class="backup-modal" role="dialog" aria-label="Song Backups">
      <div class="backup-header">
        <span class="backup-title">🕐 Song Backups</span>
        <button class="backup-close" on:click={close} aria-label="Close">✕</button>
      </div>

      <!-- Auto-backup controls -->
      <div class="backup-auto-row">
        <label class="backup-auto-label">
          <input type="checkbox" bind:checked={autoEnabled} />
          Auto-backup
        </label>
        <span class="backup-auto-sep">every</span>
        <select class="backup-interval-select" bind:value={intervalMin} disabled={!autoEnabled}>
          {#each BACKUP_INTERVAL_OPTIONS as opt}
            <option value={opt}>{opt} min</option>
          {/each}
        </select>
      </div>

      <button class="backup-now-btn" on:click={backupNow} disabled={savingNow}>
        {savingNow ? '⏳ Saving…' : '↓ Backup Now'}
      </button>

      {#if errorMsg}
        <p class="backup-error">{errorMsg}</p>
      {/if}

      <div class="backup-divider" />

      <!-- List -->
      {#if loading}
        <p class="backup-hint">Loading…</p>
      {:else if backups.length === 0}
        <p class="backup-hint">No backups yet.</p>
      {:else}
        <div class="backup-list">
          {#each backups as b (b.ts)}
            <div class="backup-row">
              <span class="backup-row-date">{fmtDate(b.ts)}</span>
              <span class="backup-row-size">{fmtSize(b.size_bytes)}</span>
              <button
                class="backup-action-btn backup-restore-btn"
                title="Restore this backup"
                on:click={() => askRestore(b.ts)}
                disabled={restoringTs === b.ts || deletingTs === b.ts}
              >
                {restoringTs === b.ts ? '⏳' : '↩'}
              </button>
              <button
                class="backup-action-btn backup-delete-btn"
                title="Delete this backup"
                on:click={() => askDelete(b.ts)}
                disabled={restoringTs === b.ts || deletingTs === b.ts}
              >
                {deletingTs === b.ts ? '⏳' : '🗑'}
              </button>
            </div>
          {/each}
        </div>
      {/if}

      {#if confirmAction}
        <div class="backup-confirm">
          <p class="backup-confirm-msg">{confirmAction.label}</p>
          <div class="backup-confirm-btns">
            <button class="backup-confirm-cancel" on:click={() => confirmAction = null}>Cancel</button>
            <button
              class="backup-confirm-ok"
              class:backup-confirm-danger={confirmAction.danger}
              on:click={confirmAction.onConfirm}
            >{confirmAction.confirmText}</button>
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .backup-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    z-index: 2000;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .backup-modal {
    background: #0f1520;
    border: 1px solid #2a3550;
    border-radius: 10px;
    padding: 1.25rem 1.5rem;
    min-width: 320px;
    max-width: 440px;
    width: 100%;
    color: #c8d0e0;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    max-height: 80vh;
  }

  .backup-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .backup-title {
    font-weight: 600;
    font-size: 1rem;
    color: #e0e8f8;
  }

  .backup-close {
    background: none;
    border: none;
    color: #888;
    font-size: 1rem;
    cursor: pointer;
    padding: 0.1rem 0.3rem;
  }
  .backup-close:hover { color: #fff; }

  .backup-auto-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
  }

  .backup-auto-label {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    cursor: pointer;
  }

  .backup-auto-sep { color: #666; }

  .backup-interval-select {
    background: #1a2235;
    border: 1px solid #2a3550;
    color: #c8d0e0;
    border-radius: 4px;
    padding: 0.15rem 0.4rem;
    font-size: 0.82rem;
    cursor: pointer;
  }
  .backup-interval-select:disabled { opacity: 0.45; }

  .backup-now-btn {
    background: #1e3a5f;
    border: 1px solid #2e5090;
    color: #90c4f8;
    border-radius: 6px;
    padding: 0.45rem 1rem;
    font-size: 0.85rem;
    cursor: pointer;
    transition: background 0.15s;
    align-self: flex-start;
  }
  .backup-now-btn:hover:not(:disabled) { background: #244a7a; }
  .backup-now-btn:disabled { opacity: 0.5; cursor: default; }

  .backup-error {
    color: #f87070;
    font-size: 0.8rem;
    margin: 0;
  }

  .backup-divider {
    height: 1px;
    background: #1e2a40;
  }

  .backup-hint {
    color: #556;
    font-size: 0.82rem;
    text-align: center;
    margin: 0.5rem 0;
  }

  .backup-list {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    overflow-y: auto;
    max-height: 45vh;
  }

  .backup-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.35rem 0.5rem;
    border-radius: 5px;
    background: #121a28;
    font-size: 0.82rem;
  }
  .backup-row:hover { background: #161e2e; }

  .backup-row-date {
    flex: 1;
    color: #a8b8d0;
  }

  .backup-row-size {
    color: #556;
    min-width: 4rem;
    text-align: right;
    font-size: 0.78rem;
  }

  .backup-action-btn {
    background: none;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 0.15rem 0.35rem;
    cursor: pointer;
    font-size: 0.85rem;
    transition: background 0.12s;
  }
  .backup-action-btn:disabled { opacity: 0.4; cursor: default; }

  .backup-restore-btn { color: #4fc3f7; }
  .backup-restore-btn:hover:not(:disabled) { background: #1a3040; border-color: #2a5060; }

  .backup-delete-btn { color: #e57373; }
  .backup-delete-btn:hover:not(:disabled) { background: #2a1515; border-color: #5a2020; }

  .backup-confirm {
    background: #0d1420;
    border: 1px solid #2a3550;
    border-radius: 6px;
    padding: 0.75rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }

  .backup-confirm-msg {
    margin: 0;
    font-size: 0.85rem;
    color: #c8d0e0;
  }

  .backup-confirm-btns {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
  }

  .backup-confirm-cancel,
  .backup-confirm-ok {
    border-radius: 5px;
    padding: 0.3rem 0.8rem;
    font-size: 0.82rem;
    cursor: pointer;
    border: 1px solid;
  }

  .backup-confirm-cancel {
    background: #1a2235;
    border-color: #2a3550;
    color: #8898b0;
  }
  .backup-confirm-cancel:hover { background: #1e293a; }

  .backup-confirm-ok {
    background: #1e3a5f;
    border-color: #2e5090;
    color: #90c4f8;
  }
  .backup-confirm-ok:hover { background: #244a7a; }

  .backup-confirm-ok.backup-confirm-danger {
    background: #4a1515;
    border-color: #8a2020;
    color: #f87070;
  }
  .backup-confirm-ok.backup-confirm-danger:hover { background: #5a1818; }
</style>
