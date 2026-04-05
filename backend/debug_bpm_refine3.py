#!/usr/bin/env python3
"""Debug BPM refinement with the new Whisper-aligned timestamps."""
import json, sys
sys.path.insert(0, '.')

# Load the new generation's summary to get syllable timings
# Or reconstruct from the summary file
import re

# Parse summary for syllable start times
timings = []
with open('downloads/summary_1775379902.txt') as f:
    in_table = False
    for line in f:
        if 'Syllable' in line and 'Start' in line:
            in_table = True
            continue
        if in_table and line.strip().startswith('-'):
            continue
        if in_table and line.strip():
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit():
                try:
                    start = float(parts[2])
                    timings.append({"start": start, "syllable": parts[1]})
                except ValueError:
                    pass

print(f"Loaded {len(timings)} syllable timings")
print(f"First: {timings[0]['syllable']} at {timings[0]['start']:.3f}s")
print(f"Last:  {timings[-1]['syllable']} at {timings[-1]['start']:.3f}s")

gap_ms = int(timings[0]['start'] * 1000)
print(f"GAP: {gap_ms}ms")

# Test refinement
from services.bpm_detection import _compute_grid_error, refine_bpm

initial_bpm = 292.0  # What we got from the detector
target_bpm = 300.0   # What reference says

times_ms = [s["start"] * 1000 for s in timings]
n = len(times_ms)

# Test a range of BPMs
print(f"\nBPM grid fit analysis (GAP={gap_ms}ms, {n} notes):")
print(f"{'BPM':>8} {'Avg Err':>8} {'Total':>8} {'Pass?':>6}")
print("-" * 40)

for bpm in [280, 285, 290, 292, 294, 296, 298, 300, 302, 304, 306, 310]:
    _, total = _compute_grid_error(times_ms, gap_ms, float(bpm))
    avg = total / n
    passed = "YES" if avg < 0.15 else "no"
    marker = " <-- initial" if bpm == 292 else (" <-- target" if bpm == 300 else "")
    print(f"{bpm:>8} {avg:>8.3f} {total:>8.1f} {passed:>6}{marker}")

# Also test with different GAPs
print(f"\nBPM=300 with different GAPs:")
for gap_test in range(gap_ms - 1000, gap_ms + 1001, 100):
    _, total = _compute_grid_error(times_ms, gap_test, 300.0)
    avg = total / n
    if avg < 0.20:
        print(f"  GAP={gap_test}ms: avg_err={avg:.3f} {'PASS' if avg < 0.15 else ''}")

# Run the actual refinement
print(f"\n--- Running refine_bpm(initial={initial_bpm}) ---")
result = refine_bpm(timings, gap_ms, initial_bpm)
print(f"Result: {result}")

# Also test with wider BPM range manually
print(f"\n--- Manual wider search (±20%) ---")
best_bpm = initial_bpm
best_avg = float('inf')
for bpm_x10 in range(2400, 3200):
    bpm = bpm_x10 / 10.0
    _, total = _compute_grid_error(times_ms, gap_ms, bpm)
    avg = total / n
    if avg < best_avg:
        best_avg = avg
        best_bpm = bpm

print(f"Best BPM in 240-320 range: {best_bpm:.1f} (avg_err={best_avg:.3f})")

# What if we also optimize GAP?
print(f"\n--- Joint BPM+GAP optimization ---")
best_combo = (initial_bpm, gap_ms, float('inf'))
for bpm_x10 in range(2800, 3100):
    bpm = bpm_x10 / 10.0
    for gap_test in range(gap_ms - 2000, gap_ms + 2001, 50):
        _, total = _compute_grid_error(times_ms, gap_test, bpm)
        avg = total / n
        if avg < best_combo[2]:
            best_combo = (bpm, gap_test, avg)

bpm_opt, gap_opt, avg_opt = best_combo
print(f"Optimal: BPM={bpm_opt:.1f}, GAP={gap_opt}ms, avg_err={avg_opt:.3f}")
print(f"vs reference: BPM=300.0, GAP=15500ms")
