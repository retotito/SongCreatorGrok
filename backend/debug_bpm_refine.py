#!/usr/bin/env python3
"""Debug: why didn't BPM refine from 281 to 300?"""
import json

with open('reference_songs/ref_0d63a120_1775376503.json') as f:
    data = json.load(f)

comp = data['comparison']
diffs = comp['note_diffs']
ai_bpm = comp['ai_bpm']   # 281
ai_gap = comp['ai_gap']   # 15332
ref_bpm = comp['ref_bpm'] # 300
ref_gap = comp['ref_gap'] # 15500

# The AI beat positions are ALREADY integer-quantized to BPM 281.
# Converting them back to ms gives exact grid positions at 281,
# so refine_bpm would always pick 281 as best -- it's circular!
#
# refine_bpm needs the RAW syllable timestamps from MFA (in seconds),
# NOT the quantized beat positions.

# Let's check: do the AI times even look like real audio timestamps?
times_from_ai = [ai_gap + d['ai']['start'] * 15000 / ai_bpm for d in diffs]

def grid_error(times_ms, gap_ms, ubpm):
    beats = [(t - gap_ms) * ubpm / 15000 for t in times_ms]
    errors = [abs(b - round(b)) for b in beats]
    return sum(errors), sum(errors) / len(errors)

total_281, avg_281 = grid_error(times_from_ai, ai_gap, 281)
total_300, avg_300 = grid_error(times_from_ai, ai_gap, 300)

print(f"AI BPM={ai_bpm}, GAP={ai_gap}")
print(f"Ref BPM={ref_bpm}, GAP={ref_gap}")
print(f"Notes: {len(diffs)}")
print()
print(f"Grid error at BPM 281 (AI):  total={total_281:.3f}, avg={avg_281:.6f}")
print(f"Grid error at BPM 300 (ref): total={total_300:.3f}, avg={avg_300:.6f}")
print()

# Now let's check using REFERENCE timestamps as ground truth
times_from_ref = [ref_gap + d['ref']['start'] * 15000 / ref_bpm for d in diffs]
total_ref_281, avg_ref_281 = grid_error(times_from_ref, ref_gap, 281)
total_ref_300, avg_ref_300 = grid_error(times_from_ref, ref_gap, 300)

print("Using REFERENCE times (ground truth):")
print(f"Grid error at BPM 281: total={total_ref_281:.3f}, avg={avg_ref_281:.6f}")
print(f"Grid error at BPM 300: total={total_ref_300:.3f}, avg={avg_ref_300:.6f}")
print()

# Scan for best BPM using reference times
best_bpm = 281
best_err = float('inf')
for bpm10 in range(2400, 3200):
    bpm = bpm10 / 10
    t, a = grid_error(times_from_ref, ref_gap, bpm)
    if t < best_err:
        best_err = t
        best_bpm = bpm

print(f"Best BPM for reference times: {best_bpm:.1f} (error={best_err:.1f}, avg={best_err/len(diffs):.4f})")
print()

# KEY QUESTION: What does refine_bpm actually get?
# It gets syllable_timings[i]['start'] in SECONDS from MFA.
# Those are the actual audio timestamps.
# But then the pipeline quantizes to integer beats and THAT becomes the output.
# refine_bpm runs BEFORE quantization? Or after?
print("=== PIPELINE ORDER CHECK ===")
print("refine_bpm needs RAW MFA timestamps to work correctly.")
print("If it receives already-quantized beats, it's circular (always picks initial BPM).")
