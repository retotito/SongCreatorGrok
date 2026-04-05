#!/usr/bin/env python3
"""Quick test of Whisper alignment without triggering MFA import."""
import sys, json, re, statistics
sys.path.insert(0, '.')

# ── Avoid MFA import by providing parse_lyrics ourselves ──
def parse_lyrics(lyrics_text):
    lines = []
    is_rap = False
    for line in lyrics_text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.upper() in ['[RAP]', '[/RAP]']:
            is_rap = line.upper() == '[RAP]'
            continue
        syllables = []
        words = line.split()
        for i, word in enumerate(words):
            parts = word.split('-')
            for j, part in enumerate(parts):
                if not part:
                    continue
                prefix = " " if (j == 0 and len(syllables) > 0) else ""
                syllables.append({
                    "text": prefix + part,
                    "is_rap": is_rap,
                    "is_word_start": j == 0,
                    "word": word,
                    "line_index": len(lines)
                })
        if syllables:
            lines.append(syllables)
    return lines

# Patch: make alignment_whisper use our parse_lyrics instead of importing alignment.py
import services.alignment_whisper as _aw
_aw_orig_align = _aw.align_whisper

def patched_align(lyrics_text, whisper_words, language="english"):
    """Patched version that avoids MFA import."""
    parsed = parse_lyrics(lyrics_text)
    flat_syllables = [s for line in parsed for s in line]
    if not flat_syllables or not whisper_words:
        return []
    word_groups = _aw._build_word_groups(flat_syllables)
    matches = _aw._match_words(word_groups, whisper_words)
    _aw._interpolate_unmatched(word_groups, matches, whisper_words)
    return _aw._distribute_syllables(word_groups, matches)

# ── Load test data ──
with open('reference_songs/ref_0d63a120_1775376503.json') as f:
    data = json.load(f)

comp = data['comparison']
ref_bpm = comp['ref_bpm']
ref_gap = comp['ref_gap']

# Reference notes (skip ~)
ref_notes = []
for d in comp['note_diffs']:
    syl = d.get('syllable_ref', '?')
    if syl == '~': continue
    beat = d['ref']['start'] 
    t_sec = ref_gap / 1000.0 + beat * 15.0 / ref_bpm
    ref_notes.append({'syllable': syl, 'time': t_sec})

# User lyrics from comparison
lyric_diffs = comp['lyrics_comparison']['differences']
lyrics_text = '\n'.join(d['user'] for d in lyric_diffs)

# Whisper words
whisper_words = []
with open('downloads/whisper_words.txt') as f:
    for line in f:
        m = re.match(r'([\d.]+)\s*-\s*([\d.]+)\s+\([\d.]+s\)\s+(.+)', line.strip())
        if m:
            whisper_words.append({'word': m.group(3).strip(), 'start': float(m.group(1)), 'end': float(m.group(2))})

print(f"Reference: {len(ref_notes)} notes | Lyrics: {len(lyric_diffs)} lines | Whisper: {len(whisper_words)} words")

# ── Test word group building ──
parsed = parse_lyrics(lyrics_text)
flat = [s for line in parsed for s in line]
groups = _aw._build_word_groups(flat)
print(f"\nWord groups: {len(groups)} words from {len(flat)} syllables")
for g in groups[:10]:
    print(f"  '{g['word']}' -> clean='{g['clean']}' ({len(g['syllables'])} syl, line={g['line_index']})")

# ── Test matching ──
matches = _aw._match_words(groups, whisper_words)
matched_count = sum(1 for m in matches if m is not None)
print(f"\nMatched: {matched_count}/{len(groups)} words")

# ── Run full alignment ──
results = patched_align(lyrics_text, whisper_words)
print(f"\nAlignment: {len(results)} syllables")

if results:
    print(f"  First: '{results[0]['syllable']}' at {results[0]['start']:.3f}s")
    print(f"  Last:  '{results[-1]['syllable']}' at {results[-1]['end']:.3f}s")

# ── Compare to reference ──
def clean(w):
    return re.sub(r"[^\w']", '', w.lower().replace('\u2019', "'"))

r_idx = a_idx = 0
matched_pairs = []
while r_idx < len(ref_notes) and a_idx < len(results):
    rc = clean(ref_notes[r_idx]['syllable'])
    ac = clean(results[a_idx]['syllable'])
    if rc == ac:
        dt = results[a_idx]['start'] - ref_notes[r_idx]['time']
        matched_pairs.append({'ref': ref_notes[r_idx]['syllable'], 'ai': results[a_idx]['syllable'],
                              'ref_t': ref_notes[r_idx]['time'], 'ai_t': results[a_idx]['start'], 'dt': dt})
        r_idx += 1; a_idx += 1
    else:
        found = False
        for look in range(1, 6):
            if r_idx + look < len(ref_notes) and clean(ref_notes[r_idx + look]['syllable']) == ac:
                r_idx += look; found = True; break
        if not found:
            for look in range(1, 6):
                if a_idx + look < len(results) and clean(results[a_idx + look]['syllable']) == rc:
                    a_idx += look; found = True; break
        if not found:
            r_idx += 1; a_idx += 1

print(f"\n{'='*60}")
print(f"WHISPER ALIGNMENT vs REFERENCE")
print(f"  Matched: {len(matched_pairs)} / {len(ref_notes)} ref notes")

if matched_pairs:
    dts = [m['dt'] for m in matched_pairs]
    abs_dts = [abs(dt) for dt in dts]
    print(f"  Mean offset:   {statistics.mean(dts):+.3f}s")
    print(f"  Mean |error|:  {statistics.mean(abs_dts):.3f}s")
    print(f"  Std dev:       {statistics.stdev(dts):.3f}s")
    for thresh in [0.2, 0.5, 1.0, 2.0]:
        n = sum(1 for dt in abs_dts if dt > thresh)
        print(f"  > {thresh}s off: {n}/{len(dts)} ({100*n/len(dts):.0f}%)")
    
    print(f"\n  vs MFA pipeline: mean |error| ~15.6s, 98% > 1.0s off")

    # First 15 matches
    print(f"\n  {'Ref':>10} {'AI':>12} {'Ref_t':>7} {'AI_t':>7} {'dt':>7}")
    for m in matched_pairs[:15]:
        print(f"  {m['ref']:>10} {m['ai']:>12} {m['ref_t']:>7.2f} {m['ai_t']:>7.2f} {m['dt']:>+7.2f}")
