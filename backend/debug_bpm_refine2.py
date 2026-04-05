#!/usr/bin/env python3
"""Debug: test refine_bpm with simulated MFA quality data."""
import json, sys, random
sys.path.insert(0, '.')

with open('reference_songs/ref_0d63a120_1775376503.json') as f:
    data = json.load(f)

diffs = data['comparison']['note_diffs']
ref_bpm = data['comparison']['ref_bpm']  # 300
ref_gap = data['comparison']['ref_gap']  # 15500

# Convert ref beats to seconds (simulated perfect MFA output)
ref_timings = []
for d in diffs:
    t_ms = ref_gap + d['ref']['start'] * 15000 / ref_bpm
    ref_timings.append({'start': t_ms / 1000.0})

from services.bpm_detection import refine_bpm

print("Testing refine_bpm with PERFECT (reference) timestamps:")
result = refine_bpm(ref_timings, int(ref_gap), 281.0)
print(f"  Input: 281.0, Output: {result}")
print()

random.seed(42)
noisy50 = [{'start': t['start'] + random.gauss(0, 0.05)} for t in ref_timings]
print("Testing with ±50ms gaussian noise:")
result2 = refine_bpm(noisy50, int(ref_gap), 281.0)
print(f"  Input: 281.0, Output: {result2}")
print()

random.seed(42)
noisy100 = [{'start': t['start'] + random.gauss(0, 0.10)} for t in ref_timings]
print("Testing with ±100ms gaussian noise:")
result3 = refine_bpm(noisy100, int(ref_gap), 281.0)
print(f"  Input: 281.0, Output: {result3}")
print()

random.seed(42)
noisy200 = [{'start': t['start'] + random.gauss(0, 0.20)} for t in ref_timings]
print("Testing with ±200ms gaussian noise:")
result4 = refine_bpm(noisy200, int(ref_gap), 281.0)
print(f"  Input: 281.0, Output: {result4}")
