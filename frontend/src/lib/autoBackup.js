/**
 * Auto-backup timer — no Svelte deps, pure JS.
 *
 * Usage:
 *   import { createAutoBackup } from '../lib/autoBackup.js';
 *
 *   const autoBackup = createAutoBackup({
 *     getIntervalMs: () => Number(localStorage.getItem('backup_interval_min') ?? 10) * 60_000,
 *     isEnabled: () => localStorage.getItem('backup_auto_enabled') !== 'false',
 *     onBackup: async () => { ... call the API ... },
 *   });
 *
 *   // Call whenever the user makes an edit:
 *   autoBackup.markChanged();
 *
 *   // On editor unmount:
 *   autoBackup.destroy();
 */

const INTERVAL_KEY = 'backup_interval_min';
const ENABLED_KEY = 'backup_auto_enabled';

export const BACKUP_INTERVAL_OPTIONS = [5, 10, 30, 60]; // minutes
export const BACKUP_INTERVAL_DEFAULT = 10;

export function getStoredIntervalMin() {
  const v = parseInt(localStorage.getItem(INTERVAL_KEY), 10);
  return BACKUP_INTERVAL_OPTIONS.includes(v) ? v : BACKUP_INTERVAL_DEFAULT;
}

export function setStoredIntervalMin(min) {
  localStorage.setItem(INTERVAL_KEY, String(min));
}

export function getStoredAutoEnabled() {
  return localStorage.getItem(ENABLED_KEY) !== 'false';
}

export function setStoredAutoEnabled(enabled) {
  localStorage.setItem(ENABLED_KEY, enabled ? 'true' : 'false');
}

/**
 * @param {{ getIntervalMs: () => number, isEnabled: () => boolean, onBackup: () => Promise<void> }} opts
 * @returns {{ markChanged: () => void, destroy: () => void }}
 */
export function createAutoBackup({ getIntervalMs, isEnabled, onBackup }) {
  let changedSinceLastBackup = false;
  let timerId = null;
  let destroyed = false;

  function schedule() {
    if (destroyed) return;
    const ms = getIntervalMs();
    timerId = setTimeout(tick, ms);
  }

  async function tick() {
    if (destroyed) return;
    try {
      if (isEnabled() && changedSinceLastBackup) {
        changedSinceLastBackup = false;
        await onBackup();
      }
    } catch (err) {
      console.warn('[AutoBackup] backup failed:', err);
      // Restore dirty flag so next tick will retry
      changedSinceLastBackup = true;
    } finally {
      schedule();
    }
  }

  // Start the first timer
  schedule();

  return {
    markChanged() {
      changedSinceLastBackup = true;
    },
    destroy() {
      destroyed = true;
      if (timerId !== null) {
        clearTimeout(timerId);
        timerId = null;
      }
    },
  };
}
