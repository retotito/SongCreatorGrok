"""Compare latest UI result with reference."""
import json

# Reference data
with open('backend/reference_songs/ref_f775ad20_1775211922.json') as f:
    ref = json.load(f)

s = ref['comparison']['summary']
print("=== REFERENCE COMPARISON (from last UI run at 19:29) ===")
print(f"BPM diff:          {s['bpm_diff']}")
print(f"GAP diff:          {s['gap_diff']}ms")
print(f"Avg start diff:    {s['avg_start_diff']} beats")
print(f"Median start diff: {s['median_start_diff']} beats")
print(f"Avg pitch diff:    {s['avg_pitch_diff']} semitones")
print(f"Exact pitch:       {s['exact_pitch_matches']}/{s['total_notes_ai']}")
print(f"Close pitch:       {s['close_pitch_matches']}/{s['total_notes_ai']}")
print(f"Timing bias:       {s['timing_bias']}")
print(f"Pitch bias:        {s['pitch_bias']}")
print()

# First 15 note diffs
print("First 15 note diffs (AI vs Reference):")
print(f"  {'Syllable':>12} {'AI_start':>8} {'Ref_start':>9} {'Start_diff':>10} {'AI_pitch':>8} {'Ref_pitch':>9} {'Pitch_diff':>10}")
print(f"  {'-'*68}")
for d in ref['comparison']['note_diffs'][:15]:
    print(f"  {d['syllable_ai']:>12} {d['ai']['start']:>8} {d['ref']['start']:>9} {d['start_diff']:>10} {d['ai']['pitch']:>8} {d['ref']['pitch']:>9} {d['pitch_diff']:>10}")

# Summary of the latest song.txt header
print()
print("=== Latest song_1775237355.txt header ===")
with open('backend/downloads/song_1775237355.txt') as f:
    for i, line in enumerate(f):
        if i < 7:
            print(f"  {line.rstrip()}")
        else:
            break

# Summary stats
print()
print("=== Summary from summary_1775237355.txt ===")
with open('backend/downloads/summary_1775237355.txt') as f:
    for i, line in enumerate(f):
        if i < 10:
            print(f"  {line.rstrip()}")
        else:
            break

# MFA debug trace
print()
print("=== Latest alignment_debug.txt (top) ===")
with open('backend/downloads/alignment_debug.txt') as f:
    for i, line in enumerate(f):
        if i < 20:
            print(f"  {line.rstrip()}")
        else:
            break
