#!/usr/bin/env python3
"""Compare Whisper word timestamps to reference Ultrastar note timestamps.

The reference has one note per syllable. We reconstruct word timing from
the reference by reading the raw Ultrastar file to get word boundaries,
then compare against Whisper word timestamps.
"""
import json, re, statistics

# ── Load reference data ──
with open('reference_songs/ref_0d63a120_1775376503.json') as f:
    data = json.load(f)

comp = data['comparison']
diffs = comp['note_diffs']
ref_bpm = comp['ref_bpm']  # 300
ref_gap = comp['ref_gap']  # 15500

# Build reference word list from note_diffs
# Each note_diff has syllable_ref. In this song, most notes are full words.
# The ~ notes are line breaks. Multi-syllable words would have no space
# between consecutive syllables (but this ref doesn't have space prefixes).
# Strategy: each non-~ syllable = 1 word (for this English pop song, most
# syllables ARE words). For multi-syllable words like "in-side", they'll
# appear as separate notes: "in", "side".
ref_words = []
for d in diffs:
    syl = d.get('syllable_ref', '?')
    if syl in ('~', '?', ''):
        continue
    beat = d['ref']['start']
    t_sec = ref_gap / 1000.0 + beat * 15.0 / ref_bpm
    clean = re.sub(r'[^\w\']', '', syl.lower())
    if clean:
        ref_words.append({'word': syl, 'clean': clean, 'time': t_sec, 'beat': beat})

print(f"Reference: {len(ref_words)} notes (excluding ~)")
for w in ref_words[:10]:
    print(f"  {w['time']:8.3f}s  beat {w['beat']:>4}  {w['word']}")
print(f"  ...")

# ── Load Whisper words ──
whisper_words = []
with open('downloads/whisper_words.txt') as f:
    for line in f:
        line = line.strip()
        m = re.match(r'([\d.]+)\s*-\s*([\d.]+)\s+\([\d.]+s\)\s+(.+)', line)
        if m:
            start = float(m.group(1))
            end = float(m.group(2))
            word = m.group(3).strip()
            clean = re.sub(r'[^\w\']', '', word.lower())
            if clean:
                whisper_words.append({'word': word, 'clean': clean, 'start': start, 'end': end})

print(f"\nWhisper: {len(whisper_words)} words")
for w in whisper_words[:5]:
    print(f"  {w['start']:8.3f}s  {w['word']}")
print(f"  ...")

# ── Sequential matching with lookahead ──
w_idx = 0
r_idx = 0
matched = []

while r_idx < len(ref_words) and w_idx < len(whisper_words):
    rw = ref_words[r_idx]
    ww = whisper_words[w_idx]
    
    if rw['clean'] == ww['clean']:
        dt = ww['start'] - rw['time']
        matched.append({
            'ref_word': rw['word'], 'whisper_word': ww['word'],
            'ref_t': rw['time'], 'whisper_t': ww['start'],
            'dt': dt, 'r_idx': r_idx, 'w_idx': w_idx
        })
        r_idx += 1
        w_idx += 1
    else:
        # Look ahead up to 5 in each direction
        found = False
        for look in range(1, 6):
            if r_idx + look < len(ref_words) and ref_words[r_idx + look]['clean'] == ww['clean']:
                r_idx += look  # skip unmatched ref notes
                found = True
                break
        if not found:
            for look in range(1, 6):
                if w_idx + look < len(whisper_words) and whisper_words[w_idx + look]['clean'] == rw['clean']:
                    w_idx += look  # skip unmatched whisper words
                    found = True
                    break
        if not found:
            r_idx += 1
            w_idx += 1

# ── Print matches ──
print(f"\n{'='*65}")
print(f"Matched: {len(matched)} / {len(ref_words)} ref notes")
print(f"\n{'#':>3} {'Word':>12} {'Whisper':>8} {'Ref':>8} {'dt(s)':>7}  Flags")
print("-" * 60)
for i, m in enumerate(matched):
    flag = ""
    if abs(m['dt']) > 0.5: flag = " LATE" if m['dt'] > 0 else " EARLY"
    if abs(m['dt']) > 2.0: flag += "!"
    if i < 40 or abs(m['dt']) > 2.0:
        print(f"{i:>3} {m['ref_word']:>12} {m['whisper_t']:>8.3f} {m['ref_t']:>8.3f} {m['dt']:>+7.3f}  {flag}")
if len(matched) > 40:
    n_flagged = sum(1 for m in matched[40:] if abs(m['dt']) > 2.0)
    if n_flagged == 0:
        print(f"  ... ({len(matched) - 40} more, all within ±2.0s)")

# ── Summary statistics ──
dts = [m['dt'] for m in matched]
abs_dts = [abs(dt) for dt in dts]

print(f"\n{'='*65}")
print(f"WHISPER vs REFERENCE TIMING ACCURACY")
print(f"  Matched:       {len(matched)} / {len(ref_words)} ref notes ({100*len(matched)/len(ref_words):.0f}%)")
print(f"  Mean offset:   {statistics.mean(dts):+.3f}s")
print(f"  Median offset: {statistics.median(dts):+.3f}s")
print(f"  Std dev:       {statistics.stdev(dts):.3f}s")
print(f"  Mean |error|:  {statistics.mean(abs_dts):.3f}s")
print(f"  Range:         {min(dts):+.3f}s to {max(dts):+.3f}s")
for thresh in [0.1, 0.2, 0.5, 1.0, 2.0]:
    n = sum(1 for dt in abs_dts if dt > thresh)
    pct = 100*n/len(dts) if dts else 0
    print(f"  Words > {thresh}s off: {n}/{len(dts)} ({pct:.0f}%)")

print(f"\n  COMPARISON TO CURRENT MFA PIPELINE:")
print(f"    MFA mean offset:  +15.620s")
print(f"    MFA mean |error|: ~15.6s")
print(f"    MFA > 1.0s off:  294/299 (98%)")

# ── Time drift over song ──
print(f"\n  TIME DRIFT BY SECTION:")
n_per_section = max(1, len(matched) // 5)
for i in range(0, len(matched), n_per_section):
    section = matched[i:i+n_per_section]
    if section:
        s_dts = [m['dt'] for m in section]
        t_range = f"{section[0]['ref_t']:.0f}-{section[-1]['ref_t']:.0f}s"
        print(f"    {t_range:>12}: mean {statistics.mean(s_dts):+.3f}s  std {statistics.stdev(s_dts) if len(s_dts) > 1 else 0:.3f}s")
