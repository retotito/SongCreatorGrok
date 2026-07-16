/**
 * Parse an Ultrastar .txt content string into an array of note objects.
 *
 * Each note object has:
 *   { id, startBeat, duration, pitch, syllable, isRap, isGolden, isFreestyle, confidence, original }
 * Break lines produce:
 *   { id, type: 'break', startBeat, endBeat }
 */
export function parseUltrastar(content) {
  const lines = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  const parsed = [];
  let id = 0;

  for (const line of lines) {
    const trimmed = line.trimStart(); // trimStart only — trailing space is part of the syllable
    if (trimmed.startsWith('*') || trimmed.startsWith(':') || trimmed.startsWith('R ') || trimmed.startsWith('R:') || trimmed.startsWith('F:') || trimmed.startsWith('F ') || trimmed === 'F') {
      const isGolden = trimmed.startsWith('*');
      // R is the standard rap prefix; F: is accepted as legacy
      const isRap = trimmed.startsWith('R ') || trimmed.startsWith('R:') || trimmed.startsWith('F:');
      const isFreestyle = !isRap && trimmed.startsWith('F');
      let prefix;
      if (isRap && (trimmed.startsWith('R ') || trimmed.startsWith('R:'))) prefix = trimmed.startsWith('R:') ? 'R:' : 'R';
      else if (isRap) prefix = 'F:'; // legacy
      else if (isFreestyle) prefix = 'F';
      else if (isGolden) prefix = '*';
      else prefix = ':';
      // Parse 3 numeric fields, then preserve the rest as syllable text
      // (including any leading space which signals a word boundary in Ultrastar)
      const rest = trimmed.substring(prefix.length);
      const match = rest.match(/^\s+(-?\d+)\s+(\d+)\s+(-?\d+) (.*)$/);

      if (match) {
        const startBeat = parseInt(match[1]);
        const duration = parseInt(match[2]);
        const pitch = parseInt(match[3]);
        const syllable = match[4];

        parsed.push({
          id: id++,
          startBeat,
          duration,
          pitch,
          syllable,
          isRap,
          isGolden: isGolden || false,
          isFreestyle: isFreestyle || false,
          confidence: 1.0,
          original: { startBeat, duration, pitch },
        });
      }
    } else if (trimmed.startsWith('-')) {
      // Break line — store for rendering
      const parts = trimmed.substring(1).trim().split(/\s+/);
      parsed.push({
        id: id++,
        type: 'break',
        startBeat: parseInt(parts[0]) || 0,
        endBeat: parseInt(parts[1]) || null,
      });
    }
  }

  return parsed;
}

/**
 * Serialize an array of note objects (as produced by parseUltrastar) into
 * Ultrastar note lines (no headers, no trailing "E").
 *
 * Supports both the `isGolden`/`isRap`/`isFreestyle` boolean style (from parseUltrastar) and
 * the `type` string style ('golden', 'rap', 'freestyle', 'break') used internally by the editor.
 */
export function notesToUltrastar(notes) {
  const lines = [];
  for (const note of notes) {
    const isBreak = note.type === 'break';
    if (isBreak) {
      lines.push(`- ${note.startBeat}`);
    } else {
      const isGolden = note.isGolden || note.type === 'golden';
      const isRap = note.isRap || note.type === 'rap';
      const isFreestyle = note.isFreestyle || note.type === 'freestyle';
      const prefix = isRap ? 'R' : isFreestyle ? 'F' : isGolden ? '*' : ':';
      lines.push(`${prefix} ${note.startBeat} ${note.duration} ${note.pitch} ${note.syllable}`);
    }
  }
  return lines.join('\n');
}
