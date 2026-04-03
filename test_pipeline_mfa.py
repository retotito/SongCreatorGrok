"""Test MFA alignment through the actual pipeline code."""
import sys
sys.path.insert(0, 'backend')

from services.alignment import align_with_mfa, parse_lyrics, MFA_AVAILABLE

print(f"MFA available: {MFA_AVAILABLE}")

audio = "backend/uploads/004731a3/vocals_u2 - Beatuful Day vocals correctedmp3.mp3"

lyrics = """The heart is a bloom shoots up through sto-ny ground
But there is no room no space to rent in this town
You are out of luck and the rea-son that you had to care
The traf-fic is stuck and you are not mov-ing a-ny-where
You thought you had found a friend to take you out of this place
Some-one you could lend a hand in re-turn for grace

It is a beau-ti-ful day the sky falls
And you feel like it is a beau-ti-ful day
It is a beau-ti-ful day
Do not let it get a-way"""

parsed = parse_lyrics(lyrics)
flat = [s for line in parsed for s in line]
print(f"Parsed: {len(flat)} syllables, {len(parsed)} lines")

results = align_with_mfa(audio, lyrics, flat, "english")
print(f"\nResults: {len(results)} syllables")
if results:
    mfa_count = sum(1 for r in results if r["method"] == "mfa")
    print(f"MFA aligned: {mfa_count}/{len(results)}")
    print(f"GAP: {int(results[0]['start']*1000)}ms (ref: 13208ms)")
    print(f"\nFirst 15 syllables:")
    for r in results[:15]:
        print(f"  {r['syllable']:>12} {r['start']:.3f}s - {r['end']:.3f}s ({r['method']})")
