"""Test MFA with the ACTUAL lyrics from frontendTest/lyrics.txt"""
import sys, os
sys.path.insert(0, 'backend')

from services.alignment import align_lyrics_to_audio, MFA_AVAILABLE, _clean_lyrics_to_words

print(f"MFA available: {MFA_AVAILABLE}")

audio = "backend/uploads/5cacb570/vocals_u2 - Beatuful Day vocals correctedmp3.mp3"
if not os.path.exists(audio):
    audio = "backend/uploads/004731a3/vocals_u2 - Beatuful Day vocals correctedmp3.mp3"

with open("frontendTest/lyrics.txt") as f:
    lyrics = f.read()

# Show what words MFA will see
words = _clean_lyrics_to_words(lyrics)
print(f"Total clean words: {len(words)}")
print(f"First 20: {' '.join(words[:20])}")
print(f"All words: {' '.join(words)}")

# Run the full pipeline
print("\nRunning full alignment pipeline...")
results = align_lyrics_to_audio(audio, lyrics, "english")

print(f"\nResults: {len(results)} syllables")
if results:
    methods = {}
    for r in results:
        m = r.get("method", "?")
        methods[m] = methods.get(m, 0) + 1
    print(f"Methods: {methods}")
    print(f"GAP: {int(results[0]['start']*1000)}ms (ref: 13208ms)")
    print(f"\nFirst 15:")
    for r in results[:15]:
        print(f"  {r['syllable']:>15} {r['start']:.3f}s - {r['end']:.3f}s ({r['method']})")
