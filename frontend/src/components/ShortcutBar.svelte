<div class="shortcut-bar">
  <div class="shortcut-group">
    <span class="shortcut-label">Playback</span>
    <span class="shortcut"><kbd>Space</kbd> play/pause</span>
    <span class="shortcut"><kbd>←→</kbd> seek ±5s</span>
    <span class="shortcut"><kbd>Shift+←→</kbd> ±1s</span>
    <span class="shortcut"><kbd>L</kbd> loop</span>
    <span class="shortcut"><kbd>M</kbd> mic · <kbd>Shift+M</kbd> trail</span>
    <span class="shortcut"><kbd>V</kbd> vocal trace · <kbd>Shift+V</kbd> show</span>
  </div>
  <div class="shortcut-group">
    <span class="shortcut-label">Navigate</span>
    <span class="shortcut"><kbd>Scroll</kbd> pan</span>
    <span class="shortcut"><kbd>Ctrl+Scroll</kbd> zoom</span>
    <span class="shortcut"><kbd>,</kbd> prev position</span>
    <span class="shortcut"><kbd>.</kbd> next position</span>
    <span class="shortcut"><kbd>Shift+,</kbd> prev marker</span>
    <span class="shortcut"><kbd>Shift+.</kbd> next marker</span>
    <span class="shortcut"><kbd>⌘F</kbd> find syllable</span>
  </div>
  <div class="shortcut-group">
    <span class="shortcut-label">Select</span>
    <span class="shortcut"><kbd>Click</kbd> select</span>
    <span class="shortcut"><kbd>Ctrl+Click</kbd> multi</span>
    <span class="shortcut"><kbd>Ctrl+Drag</kbd> box select</span>
  </div>
  <div class="shortcut-group">
    <span class="shortcut-label">Edit</span>
    <span class="shortcut"><kbd>Drag</kbd> move</span>
    <span class="shortcut"><kbd>Shift+Drag</kbd> move X only</span>
    <span class="shortcut"><kbd>Tab</kbd> next note</span>
    <span class="shortcut"><kbd>Shift+Tab</kbd> previous note</span>
    <span class="shortcut"><kbd>Option+←→</kbd> extend to adjacent</span>
    <span class="shortcut"><kbd>Shift+Option+←→</kbd> move boundary</span>
    <span class="shortcut"><kbd>↑↓</kbd> pitch ±1 semitone</span>
    <span class="shortcut"><kbd>Ctrl+↑↓</kbd> pitch ±1 octave</span>
    <span class="shortcut"><kbd>S</kbd> split</span>
    <span class="shortcut"><kbd>Del</kbd> delete</span>
    <span class="shortcut"><kbd>P</kbd> play pitch</span>
    <span class="shortcut"><kbd>E</kbd> edit syllable</span>
    <span class="shortcut"><kbd>Ctrl+←→</kbd> resize right edge</span>
    <span class="shortcut"><kbd>Shift+Ctrl+←→</kbd> resize left edge</span>
    <span class="shortcut"><kbd>Ctrl+X/C/V</kbd> cut/copy/paste</span>
  </div>
  <div class="shortcut-group">
    <span class="shortcut-label">Tools</span>
    <span class="shortcut"><kbd>Ctrl+Z</kbd> undo</span>
    <span class="shortcut"><kbd>Shift+Ctrl+Z</kbd> redo</span>
    <span class="shortcut"><kbd>Ctrl+S</kbd> save</span>
    <span class="shortcut"><kbd>Ctrl+G</kbd> set GAP</span>
    <span class="shortcut"><kbd>9</kbd> MIDI · <kbd>0</kbd> metronome</span>
  </div>
</div>

<style>
  .shortcut-bar {
    display: flex;
    justify-content: center;
    gap: 1.5rem;
    margin-top: 0.5rem;
    padding: 0.5rem 1rem;
    background: #0a0e14;
    border: 1px solid #1e2433;
    border-radius: 8px;
    flex-wrap: wrap;
  }

  .shortcut-group {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .shortcut-label {
    color: #4fc3f7;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-right: 0.15rem;
  }

  .shortcut {
    color: #667;
    font-size: 0.75rem;
    white-space: nowrap;
  }

  .shortcut kbd {
    display: inline-block;
    background: #1a1f2b;
    border: 1px solid #2a3040;
    border-radius: 3px;
    padding: 0px 4px;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 0.7rem;
    color: #aab;
    margin-right: 2px;
    line-height: 1.5;
  }
</style>
