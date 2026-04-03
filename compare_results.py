"""Compare latest MFA alignment with reference data."""
import json, sys

# Load reference
with open("backend/reference_songs/ref_f775ad20_1775211922.json") as f:
    ref = json.load(f)

ref_bpm = ref["comparison"]["ref_bpm"]
ref_gap = ref["comparison"]["ref_gap"]
ref_notes = [d["ref"] for d in ref["comparison"]["note_diffs"]]
ref_syllables = [d["syllable_ref"] for d in ref["comparison"]["note_diffs"]]
ai_bpm = 272.0
ai_gap = 13200  # From our latest MFA test

print(f"REFERENCE: BPM={ref_bpm}, GAP={ref_gap}ms, notes={len(ref_notes)}")
print(f"GENERATED: BPM={ai_bpm}, GAP={ai_gap}ms")
print()

# Read alignment debug for MFA timings
mfa_timings = []
with open("backend/downloads/alignment_debug.txt") as f:
    for line in f:
        parts = line.split()
        if len(parts) >= 7 and parts[0].isdigit():
            idx = int(parts[0])
            syllable = parts[1]
            start = float(parts[2])
            end = float(parts[3])
            mfa_timings.append({"idx": idx, "syllable": syllable, "start": start, "end": end})

print(f"MFA syllables: {len(mfa_timings)}, Reference notes: {len(ref_notes)}")
print()

# Convert both to absolute time for comparison
# Ref time = ref_gap/1000 + (start_beat * 60 / ref_bpm)
# MFA time is already absolute

print("COMPARISON - First 25 notes:")
print(f"{'#':>3}  {'Ref Syl':>15} {'Ref Time':>10}  {'MFA Syl':>15} {'MFA Time':>10}  {'Diff':>8}")
print("-" * 80)

n = min(25, len(ref_notes), len(mfa_timings))
for i in range(n):
    rn = ref_notes[i]
    mt = mfa_timings[i]
    rs = ref_syllables[i]
    ref_time = ref_gap / 1000.0 + (rn["start"] * 60.0 / ref_bpm)
    ref_dur_sec = rn["duration"] * 60.0 / ref_bpm
    mfa_time = mt["start"]
    mfa_dur = mt["end"] - mt["start"]
    diff = mfa_time - ref_time
    print(f"{i:>3}  {rs:>15} {ref_time:>9.3f}s  {mt['syllable']:>15} {mfa_time:>9.3f}s  {diff:>+7.3f}s")

# Overall stats
print("\n--- TIMING STATISTICS ---")
diffs = []
for i in range(min(len(ref_notes), len(mfa_timings))):
    rn = ref_notes[i]
    mt = mfa_timings[i]
    ref_time = ref_gap / 1000.0 + (rn["start"] * 60.0 / ref_bpm)
    diff = mt["start"] - ref_time
    diffs.append(diff)

abs_diffs = [abs(d) for d in diffs]
print(f"Compared: {len(diffs)} notes")
print(f"Avg timing diff: {sum(abs_diffs)/len(abs_diffs):.3f}s")
print(f"Max timing diff: {max(abs_diffs):.3f}s")
print(f"Median timing diff: {sorted(abs_diffs)[len(abs_diffs)//2]:.3f}s")
print(f"Avg signed diff: {sum(diffs)/len(diffs):+.3f}s (positive = MFA is late)")
print()

# Check how many are within various thresholds
for thresh in [0.1, 0.2, 0.5, 1.0, 2.0]:
    within = sum(1 for d in abs_diffs if d <= thresh)
    print(f"  Within {thresh:.1f}s: {within}/{len(diffs)} ({100*within/len(diffs):.0f}%)")
