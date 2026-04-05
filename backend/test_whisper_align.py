#!/usr/bin/env python3
"""Test the new Whisper alignment module against reference data.

Simulates the pipeline: parse lyrics → match to Whisper words → get syllable timings.
Compares output against reference Ultrastar note timings.
"""
import json, re, statistics, sys
sys.path.insert(0, '.')

from services.alignment_whisper import align_whisper

# ── Load reference ──
with open('reference_songs/ref_0d63a120_1775376503.json') as f:
    data = json.load(f)

comp = data['comparison']
diffs = comp['note_diffs']
ref_bpm = comp['ref_bpm']  # 300
ref_gap = comp['ref_gap']  # 15500

# ── Build reference timing list (all notes, skip ~ line breaks) ──  
ref_notes = []
for d in diffs:
    syl = d.get('syllable_ref', '?')
    if syl == '~':
        continue
    beat = d['ref']['start']
    t_sec = ref_gap / 1000.0 + beat * 15.0 / ref_bpm
    ref_notes.append({'syllable': syl, 'time': t_sec, 'beat': beat})

print(f"Reference: {len(ref_notes)} notes, BPM={ref_bpm}, GAP={ref_gap}")

# ── Get user lyrics from comparison ──
lyric_diffs = comp['lyrics_comparison']['differences']
lyrics_lines = [d['user'] for d in lyric_diffs]
lyrics_text = '\n'.join(lyrics_lines)
print(f"Lyrics: {len(lyrics_lines)} lines")

# ── Load Whisper words ──
whisper_words = []
with open('downloads/whisper_words.txt') as f:
    for line in f:
        line = line.strip()
        m = re.match(r'([\d.]+)\s*-\s*([\d.]+)\s+\([\d.]+s\)\s+(.+)', line)
        if m:
            whisper_words.append({
                'word': m.group(3).strip(),
                'start': float(m.group(1)),
                'end': float(m.group(2)),
            })
print(f"Whisper: {len(whisper_words)} words")

# ── Run new aligner ──
print("\n" + "="*65)
print("Running Whisper alignment...")
results = align_whisper(lyrics_text, whisper_words)
print(f"Result: {len(results)} syllables")

# ── Display first 20 syllables ──
print(f"\n{'#':>3} {'Syllable':>15} {'Start':>8} {'End':>8} {'Dur':>6} {'Conf':>5} {'Line':>4}")
print("-" * 60)
for i, r in enumerate(results[:20]):
    dur = r['end'] - r['start']
    print(f"{i:>3} {r['syllable']:>15} {r['start']:>8.3f} {r['end']:>8.3f} {dur:>6.3f} {r['confidence']:>5.2f} {r.get('line_index', '?'):>4}")
if len(results) > 20:
    print(f"  ... ({len(results) - 20} more)")

# ── Compare to reference ──
# Match by word (sequential fuzzy matching)
def clean(w):
    return re.sub(r"[^\w']", '', w.lower().replace('\u2019', "'"))

r_idx = 0
a_idx = 0
matched = []

while r_idx < len(ref_notes) and a_idx < len(results):
    r_clean = clean(ref_notes[r_idx]['syllable'])
    a_clean = clean(results[a_idx]['syllable'])
    
    if r_clean == a_clean:
        dt = results[a_idx]['start'] - ref_notes[r_idx]['time']
        matched.append({
            'ref': ref_notes[r_idx]['syllable'],
            'ai': results[a_idx]['syllable'],
            'ref_t': ref_notes[r_idx]['time'],
            'ai_t': results[a_idx]['start'],
            'dt': dt,
        })
        r_idx += 1
        a_idx += 1
    else:
        found = False
        for look in range(1, 6):
            if r_idx + look < len(ref_notes) and clean(ref_notes[r_idx + look]['syllable']) == a_clean:
                r_idx += look
                found = True
                break
        if not found:
            for look in range(1, 6):
                if a_idx + look < len(results) and clean(results[a_idx + look]['syllable']) == r_clean:
                    a_idx += look
                    found = True
                    break
        if not found:
            r_idx += 1
            a_idx += 1

# ── Stats ──
print(f"\n{'='*65}")
print(f"NEW WHISPER ALIGNMENT vs REFERENCE")
print(f"  Matched: {len(matched)} / {len(ref_notes)} ref notes ({100*len(matched)/len(ref_notes):.0f}%)")

if matched:
    dts = [m['dt'] for m in matched]
    abs_dts = [abs(dt) for dt in dts]
    print(f"  Mean offset:   {statistics.mean(dts):+.3f}s")
    print(f"  Median offset: {statistics.median(dts):+.3f}s")
    print(f"  Std dev:       {statistics.stdev(dts):.3f}s")
    print(f"  Mean |error|:  {statistics.mean(abs_dts):.3f}s")
    print(f"  Range:         {min(dts):+.3f}s to {max(dts):+.3f}s")
    for thresh in [0.1, 0.2, 0.5, 1.0, 2.0]:
        n = sum(1 for dt in abs_dts if dt > thresh)
        print(f"  Words > {thresh}s off: {n}/{len(dts)} ({100*n/len(dts):.0f}%)")
    
    print(f"\n  PREVIOUS MFA PIPELINE:")
    print(f"    Mean |error|: ~15.6s")
    print(f"    > 1.0s off:  294/299 (98%)")
    
    # BPM refinement test: can we recover BPM 300 from these timestamps?
    print(f"\n  BPM REFINEMENT TEST:")
    # Use the aligned timestamps and try to find the best BPM
    from services.bpm_detection import refine_bpm
    test_gap_ms = int(results[0]['start'] * 1000)
    test_bpm_initial = 281  # what our ensemble detector gave
    refined = refine_bpm(results, test_gap_ms, test_bpm_initial)
    print(f"    Initial BPM: {test_bpm_initial}")
    print(f"    Refined BPM: {refined}")
    print(f"    Target BPM:  {ref_bpm}")
    print(f"    {'✓ CORRECT' if abs(refined - ref_bpm) < 1.0 else '✗ STILL WRONG'}")

    # Show first 20 matched pairs
    print(f"\n  First 20 matched pairs:")
    print(f"  {'Ref':>12} {'AI':>12} {'Ref_t':>8} {'AI_t':>8} {'dt':>7}")
    for m in matched[:20]:
        flag = " !" if abs(m['dt']) > 0.5 else ""
        print(f"  {m['ref']:>12} {m['ai']:>12} {m['ref_t']:>8.3f} {m['ai_t']:>8.3f} {m['dt']:>+7.3f}{flag}")
