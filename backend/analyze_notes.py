"""Quick analysis: compare note counts and durations between generated and reference."""
import re, glob, os

# Reference file
ref_path = '/Users/retokupfer/Music/UltraStar Deluxe/U2 - Beautiful Day [VIDEO]/U2 - Beautiful Day.txt'
with open(ref_path, encoding='utf-8', errors='replace') as f:
    ref_lines = f.readlines()

ref_notes = [l.strip() for l in ref_lines if l.strip().startswith(':') or l.strip().startswith('F:')]
ref_durations = [int(n.split(None, 4)[2]) for n in ref_notes]

print('=== REFERENCE ===')
print(f'Total notes: {len(ref_notes)}')
print(f'Average duration (beats): {sum(ref_durations)/len(ref_durations):.1f}')
print(f'Min duration: {min(ref_durations)}, Max: {max(ref_durations)}')
print('Duration distribution:')
for d in sorted(set(ref_durations)):
    count = ref_durations.count(d)
    print(f'  {d} beat(s): {count} notes ({count*100//len(ref_durations)}%)')
for l in ref_lines:
    if l.startswith('#BPM') or l.startswith('#GAP'):
        print(f'  {l.strip()}')

# Generated file
gen_files = sorted(glob.glob('downloads/song_*.txt'), key=lambda f: int(re.search(r'song_(\d+)', f).group(1)))
if gen_files:
    gen_path = gen_files[-1]
    print(f'\n=== GENERATED ({gen_path}) ===')
    with open(gen_path, encoding='utf-8') as f:
        gen_lines = f.readlines()
    gen_notes = [l.strip() for l in gen_lines if l.strip().startswith(':') or l.strip().startswith('F:')]
    gen_durations = [int(n.split(None, 4)[2]) for n in gen_notes]
    print(f'Total notes: {len(gen_notes)}')
    print(f'Average duration (beats): {sum(gen_durations)/len(gen_durations):.1f}')
    print(f'Min duration: {min(gen_durations)}, Max: {max(gen_durations)}')
    print('Duration distribution:')
    for d in sorted(set(gen_durations)):
        count = gen_durations.count(d)
        if count >= 2:
            print(f'  {d} beat(s): {count} notes ({count*100//len(gen_durations)}%)')
    for l in gen_lines:
        if l.startswith('#BPM') or l.startswith('#GAP'):
            print(f'  {l.strip()}')

    # Side by side first 15 notes
    print(f'\n=== FIRST 15 NOTES ===')
    print(f'{"REF":<50s} | GENERATED')
    for i in range(min(15, len(ref_notes), len(gen_notes))):
        print(f'{ref_notes[i]:<50s} | {gen_notes[i]}')

    # Pitch variety
    ref_pitches = [int(n.split(None, 4)[3]) for n in ref_notes]
    gen_pitches = [int(n.split(None, 4)[3]) for n in gen_notes]
    print(f'\n=== PITCH VARIETY ===')
    print(f'Reference: {len(set(ref_pitches))} unique pitches, range {min(ref_pitches)}-{max(ref_pitches)}')
    print(f'Generated: {len(set(gen_pitches))} unique pitches, range {min(gen_pitches)}-{max(gen_pitches)}')

    # How many adjacent notes share the same pitch?
    ref_same = sum(1 for i in range(1, len(ref_pitches)) if ref_pitches[i] == ref_pitches[i-1])
    gen_same = sum(1 for i in range(1, len(gen_pitches)) if gen_pitches[i] == gen_pitches[i-1])
    print(f'\nAdjacent same-pitch pairs:')
    print(f'  Reference: {ref_same}/{len(ref_pitches)-1} ({ref_same*100//(len(ref_pitches)-1)}%)')
    print(f'  Generated: {gen_same}/{len(gen_pitches)-1} ({gen_same*100//(len(gen_pitches)-1)}%)')
