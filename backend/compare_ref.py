#!/usr/bin/env python3
"""Compare generated song vs reference — timing and pitch analysis."""
import json, statistics

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

# Compute pitch offset (octave normalization)
ai_pitches = sorted([d['ai']['pitch'] for d in diffs])
ref_pitches = sorted([d['ref']['pitch'] for d in diffs])
ai_med = ai_pitches[len(ai_pitches)//2]
ref_med = ref_pitches[len(ref_pitches)//2]
octave_offset = round((ai_med - ref_med) / 12) * 12

print(f"Generated: BPM={ai_bpm}, GAP={ai_gap}")
print(f"Reference: BPM={ref_bpm}, GAP={ref_gap}")
print(f"Pitch offset: AI median={ai_med}, Ref median={ref_med}, octave_offset={octave_offset}")
print(f"Total notes: {len(diffs)}")
print()

# Side-by-side comparison
print(f"{'#':>3} {'Syl':>10} {'AI t(s)':>8} {'Ref t(s)':>8} {'dt(s)':>7} {'AI P':>5} {'RefP+O':>6} {'dP':>4}  Flags")
print("-" * 90)

timing_errs = []
pitch_errs = []

for d in diffs:
    i = d['index']
    ai = d['ai']
    ref = d['ref']
    
    ai_t = beat_to_sec(ai['start'], ai_bpm, ai_gap)
    ref_t = beat_to_sec(ref['start'], ref_bpm, ref_gap)
    dt = ai_t - ref_t
    
    ai_dur = ai['duration'] * 15.0 / ai_bpm
    ref_dur = ref['duration'] * 15.0 / ref_bpm
    
    dp = ai['pitch'] - (ref['pitch'] + octave_offset)
    
    timing_errs.append(dt)
    pitch_errs.append(dp)
    
    flags = ""
    if abs(dt) > 1.0: flags += " TIME!"
    if abs(dt) > 3.0: flags += "!"
    if abs(dp) > 3: flags += " PITCH!"
    
    syl = d.get('syllable_ai', '?')[:10]
    
    if i < 60 or flags:  # Show first 60 + all flagged
        print(f"{i:>3} {syl:>10} {ai_t:>8.2f} {ref_t:>8.2f} {dt:>+7.2f} {ai['pitch']:>5} {ref['pitch']+octave_offset:>6} {dp:>+4d}  {flags}")

# Summary
print("\n" + "=" * 60)
print("TIMING SUMMARY")
print(f"  Mean offset: {statistics.mean(timing_errs):+.3f}s")
print(f"  Median offset: {statistics.median(timing_errs):+.3f}s")
print(f"  Std dev: {statistics.stdev(timing_errs):.3f}s")
print(f"  Range: {min(timing_errs):+.3f}s to {max(timing_errs):+.3f}s")

for thresh in [0.5, 1.0, 2.0, 5.0]:
    n = sum(1 for dt in timing_errs if abs(dt) > thresh)
    print(f"  Notes > {thresh}s off: {n}/{len(timing_errs)} ({100*n/len(timing_errs):.0f}%)")

print()
print("PITCH SUMMARY (after octave normalization)")
print(f"  Mean offset: {statistics.mean(pitch_errs):+.2f} semitones")
print(f"  Std dev: {statistics.stdev(pitch_errs):.2f} semitones")
exact = sum(1 for dp in pitch_errs if dp == 0)
w1 = sum(1 for dp in pitch_errs if abs(dp) <= 1)
w2 = sum(1 for dp in pitch_errs if abs(dp) <= 2)
w3 = sum(1 for dp in pitch_errs if abs(dp) <= 3)
print(f"  Exact: {exact}/{len(pitch_errs)} ({100*exact/len(pitch_errs):.0f}%)")
print(f"  Within ±1: {w1}/{len(pitch_errs)} ({100*w1/len(pitch_errs):.0f}%)")
print(f"  Within ±2: {w2}/{len(pitch_errs)} ({100*w2/len(pitch_errs):.0f}%)")
print(f"  Within ±3: {w3}/{len(pitch_errs)} ({100*w3/len(pitch_errs):.0f}%)")

# Show worst timing offenders
print("\n\nWORST TIMING (top 20)")
sorted_by_time = sorted(enumerate(timing_errs), key=lambda x: abs(x[1]), reverse=True)
for rank, (i, dt) in enumerate(sorted_by_time[:20]):
    d = diffs[i]
    ai_t = beat_to_sec(d['ai']['start'], ai_bpm, ai_gap)
    ref_t = beat_to_sec(d['ref']['start'], ref_bpm, ref_gap)
    print(f"  {rank+1}. #{i} '{d['syllable_ai']}' AI={ai_t:.2f}s Ref={ref_t:.2f}s dt={dt:+.2f}s")
