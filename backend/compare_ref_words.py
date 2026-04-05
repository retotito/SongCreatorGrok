#!/usr/bin/env python3
"""Word-content-aware comparison of AI vs reference timing.

The standard compare_ref.py uses index-based note matching which breaks
when AI and reference have different note counts (e.g., ~ line breaks).
This script matches notes by WORD CONTENT sequentially.
"""
import json, re, statistics

with open('reference_songs/ref_50b02eb1_1775379902.json') as f:
    data = json.load(f)

comp = data['comparison']
diffs = comp['note_diffs']
ai_bpm = comp['ai_bpm']
ref_bpm = comp['ref_bpm']
ai_gap = comp['ai_gap']
ref_gap = comp['ref_gap']

def beat_to_sec(beat, bpm, gap_ms):
    return gap_ms / 1000.0 + beat * 15.0 / bpm

def clean(w):
    return re.sub(r"[^\w']", '', w.lower().replace('\u2019', "'"))

# Build separate AI and Ref note lists with absolute times
ai_notes = []
ref_notes = []
for d in diffs:
    syl_ai = d.get('syllable_ai', '?')
    syl_ref = d.get('syllable_ref', '?')
    ai_t = beat_to_sec(d['ai']['start'], ai_bpm, ai_gap)
    ref_t = beat_to_sec(d['ref']['start'], ref_bpm, ref_gap)
    
    if clean(syl_ai):
        ai_notes.append({'syl': syl_ai, 'clean': clean(syl_ai), 'time': ai_t, 'pitch': d['ai']['pitch']})
    if syl_ref != '~' and clean(syl_ref):
        ref_notes.append({'syl': syl_ref, 'clean': clean(syl_ref), 'time': ref_t, 'pitch': d['ref']['pitch']})

print(f"AI:  {len(ai_notes)} notes, BPM={ai_bpm}, GAP={ai_gap}")
print(f"Ref: {len(ref_notes)} notes, BPM={ref_bpm}, GAP={ref_gap}")

# Sequential word-content matching
a_idx = r_idx = 0
matched = []

while a_idx < len(ai_notes) and r_idx < len(ref_notes):
    ac = ai_notes[a_idx]['clean']
    rc = ref_notes[r_idx]['clean']
    
    if ac == rc:
        dt = ai_notes[a_idx]['time'] - ref_notes[r_idx]['time']
        matched.append({
            'ai': ai_notes[a_idx], 'ref': ref_notes[r_idx],
            'dt': dt, 'a_idx': a_idx, 'r_idx': r_idx
        })
        a_idx += 1; r_idx += 1
    else:
        found = False
        for look in range(1, 8):
            if r_idx + look < len(ref_notes) and ref_notes[r_idx + look]['clean'] == ac:
                r_idx += look; found = True; break
        if not found:
            for look in range(1, 8):
                if a_idx + look < len(ai_notes) and ai_notes[a_idx + look]['clean'] == rc:
                    a_idx += look; found = True; break
        if not found:
            a_idx += 1; r_idx += 1

# Stats
print(f"\nMatched: {len(matched)} / {min(len(ai_notes), len(ref_notes))} notes")

dts = [m['dt'] for m in matched]
abs_dts = [abs(dt) for dt in dts]

# Pitch
ai_med = statistics.median([m['ai']['pitch'] for m in matched])
ref_med = statistics.median([m['ref']['pitch'] for m in matched])
oct_off = round((ai_med - ref_med) / 12) * 12
pitch_errs = [m['ai']['pitch'] - (m['ref']['pitch'] + oct_off) for m in matched]

# Show first 30 + flagged
print(f"\n{'#':>3} {'AI Syl':>10} {'Ref Syl':>10} {'AI t':>7} {'Ref t':>7} {'dt':>7}  {'dP':>4} Flags")
print("-" * 70)
for i, m in enumerate(matched):
    dp = m['ai']['pitch'] - (m['ref']['pitch'] + oct_off)
    flag = ""
    if abs(m['dt']) > 1.0: flag += " TIME!"
    if abs(dp) > 3: flag += " PITCH!"
    if i < 30 or flag:
        print(f"{i:>3} {m['ai']['syl']:>10} {m['ref']['syl']:>10} {m['ai']['time']:>7.2f} {m['ref']['time']:>7.2f} {m['dt']:>+7.2f}  {dp:>+4d} {flag}")
if len(matched) > 30:
    n_clean = sum(1 for m in matched[30:] if abs(m['dt']) <= 1.0)
    print(f"  ... ({len(matched)-30} more, {n_clean} within ±1.0s)")

print(f"\n{'='*60}")
print("TIMING (word-content matched)")
print(f"  Mean offset:  {statistics.mean(dts):+.3f}s")
print(f"  Median offset: {statistics.median(dts):+.3f}s")
print(f"  Std dev:      {statistics.stdev(dts):.3f}s")
print(f"  Mean |error|: {statistics.mean(abs_dts):.3f}s")
print(f"  Range:        {min(dts):+.3f}s to {max(dts):+.3f}s")
for t in [0.5, 1.0, 2.0, 5.0]:
    n = sum(1 for dt in abs_dts if dt > t)
    print(f"  > {t}s off: {n}/{len(dts)} ({100*n/len(dts):.0f}%)")

print(f"\nPITCH (octave offset={oct_off})")
exact = sum(1 for p in pitch_errs if p == 0)
w2 = sum(1 for p in pitch_errs if abs(p) <= 2)
w3 = sum(1 for p in pitch_errs if abs(p) <= 3)
print(f"  Exact:     {exact}/{len(matched)} ({100*exact/len(matched):.0f}%)")
print(f"  Within ±2: {w2}/{len(matched)} ({100*w2/len(matched):.0f}%)")
print(f"  Within ±3: {w3}/{len(matched)} ({100*w3/len(matched):.0f}%)")

# Time drift by section
print(f"\nTIME DRIFT BY SECTION:")
n_per = max(1, len(matched) // 6)
for i in range(0, len(matched), n_per):
    section = matched[i:i+n_per]
    if len(section) > 1:
        s_dts = [m['dt'] for m in section]
        t_range = f"{section[0]['ref']['time']:.0f}-{section[-1]['ref']['time']:.0f}s"
        print(f"  {t_range:>12}: mean {statistics.mean(s_dts):+.3f}s  std {statistics.stdev(s_dts):.3f}s")

# Previous comparison
print(f"\nPREVIOUS (MFA, index-matched): mean|err|=15.6s, >1s=98%")
