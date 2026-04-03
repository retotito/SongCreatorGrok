"""Debug: test MFA chunked alignment directly and show what fails."""
import sys, os, tempfile, subprocess, re, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

import librosa
import soundfile as sf
from services.alignment import (
    _detect_vocal_sections, _group_lyrics_into_sections,
    _clean_lyrics_to_words, _parse_textgrid_words,
    MFA_AVAILABLE, CONDA_BIN, MFA_ENV, parse_lyrics
)

# Use the actual uploaded audio
audio_path = 'backend/uploads/004731a3/vocals_u2 - Beatuful Day vocals correctedmp3.mp3'
if not os.path.exists(audio_path):
    audio_path = 'backend/uploads/f775ad20/vocals_u2 - Beatuful Day vocals correctedmp3.mp3'

y, sr = librosa.load(audio_path, sr=16000, mono=True)
duration = len(y) / sr
print(f"Audio: {duration:.1f}s")

# Detect vocal sections
sections = _detect_vocal_sections(y, sr, min_silence_sec=1.5, min_section_sec=1.0)
print(f"\nVocal sections: {len(sections)}")
for i, (s, e) in enumerate(sections[:10]):
    print(f"  [{i}] {s:.1f}s - {e:.1f}s ({e-s:.1f}s)")

# Test: run MFA on the FULL audio with just the first verse
# (same as what worked in the earlier test)
test_words = "the heart is a bloom shoots up through the stony ground"
print(f"\n--- Test 1: Full audio + short transcript ---")
print(f"Transcript: {test_words}")

with tempfile.TemporaryDirectory() as td:
    corpus = os.path.join(td, "corpus")
    output = os.path.join(td, "output")
    os.makedirs(corpus)
    os.makedirs(output)
    
    sf.write(os.path.join(corpus, "song.wav"), y, sr)
    with open(os.path.join(corpus, "song.txt"), "w") as f:
        f.write(test_words)
    
    cmd = [CONDA_BIN, "run", "-n", MFA_ENV, "mfa", "align",
           corpus, "english_mfa", "english_mfa", output,
           "--clean", "--single_speaker", "--beam", "100", "--retry_beam", "400"]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    print(f"Exit: {result.returncode}")
    if result.returncode == 0:
        for root, dirs, files in os.walk(output):
            for fn in files:
                if fn.endswith('.TextGrid'):
                    words = _parse_textgrid_words(os.path.join(root, fn))
                    print(f"Words: {len(words)}")
                    for w in words[:5]:
                        print(f"  {w['text']:<12} {w['start']:.3f} - {w['end']:.3f}")
    else:
        print(f"FAILED: {result.stderr[-300:]}")

# Test 2: Chunked - first vocal section only
print(f"\n--- Test 2: First chunk only ---")
seg_start, seg_end = sections[0]
pad = 1.0
cs = max(0, seg_start - pad)
ce = min(duration, seg_end + pad)
chunk = y[int(cs*sr):int(ce*sr)]
print(f"Chunk: {cs:.1f}s - {ce:.1f}s ({ce-cs:.1f}s)")
print(f"Transcript: {test_words}")

with tempfile.TemporaryDirectory() as td:
    corpus = os.path.join(td, "corpus")
    output = os.path.join(td, "output")
    os.makedirs(corpus)
    os.makedirs(output)
    
    sf.write(os.path.join(corpus, "chunk.wav"), chunk, sr)
    with open(os.path.join(corpus, "chunk.txt"), "w") as f:
        f.write(test_words)
    
    cmd = [CONDA_BIN, "run", "-n", MFA_ENV, "mfa", "align",
           corpus, "english_mfa", "english_mfa", output,
           "--clean", "--single_speaker", "--beam", "100", "--retry_beam", "400"]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    print(f"Exit: {result.returncode}")
    if result.returncode == 0:
        for root, dirs, files in os.walk(output):
            for fn in files:
                if fn.endswith('.TextGrid'):
                    words = _parse_textgrid_words(os.path.join(root, fn))
                    print(f"Words: {len(words)}")
                    for w in words:
                        abs_s = w['start'] + cs
                        abs_e = w['end'] + cs
                        print(f"  {w['text']:<12} chunk:{w['start']:.3f}-{w['end']:.3f}  abs:{abs_s:.3f}-{abs_e:.3f}")
    else:
        print(f"FAILED: {result.stderr[-300:]}")

# Test 3: Multiple chunks in one corpus
print(f"\n--- Test 3: Multi-chunk corpus (first 3 sections) ---")
with tempfile.TemporaryDirectory() as td:
    corpus = os.path.join(td, "corpus")
    output = os.path.join(td, "output")
    os.makedirs(corpus)
    os.makedirs(output)
    
    chunk_texts = [
        "the heart is a bloom",
        "shoots up through the stony ground",
        "but theres no room no space to rent in this town"
    ]
    
    for ci in range(min(3, len(sections))):
        seg_s, seg_e = sections[ci]
        cs = max(0, seg_s - 1.0)
        ce = min(duration, seg_e + 1.0)
        chunk = y[int(cs*sr):int(ce*sr)]
        
        name = f"chunk_{ci:03d}"
        sf.write(os.path.join(corpus, f"{name}.wav"), chunk, sr)
        with open(os.path.join(corpus, f"{name}.txt"), "w") as f:
            f.write(chunk_texts[ci])
        print(f"  {name}: {cs:.1f}-{ce:.1f}s, text='{chunk_texts[ci]}'")
    
    cmd = [CONDA_BIN, "run", "-n", MFA_ENV, "mfa", "align",
           corpus, "english_mfa", "english_mfa", output,
           "--clean", "--single_speaker", "--beam", "100", "--retry_beam", "400"]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    print(f"\nExit: {result.returncode}")
    
    # Show relevant stderr
    if result.stderr:
        for line in result.stderr.split('\n'):
            ll = line.lower()
            if any(kw in ll for kw in ['error', 'oov', 'failed', 'warning', 'not found', 'no words']):
                print(f"  STDERR: {line.strip()}")
    
    if result.returncode == 0:
        for root, dirs, files in os.walk(output):
            for fn in sorted(files):
                if fn.endswith('.TextGrid'):
                    words = _parse_textgrid_words(os.path.join(root, fn))
                    print(f"\n  {fn}: {len(words)} words")
                    for w in words:
                        print(f"    {w['text']:<12} {w['start']:.3f} - {w['end']:.3f}")
    else:
        print(f"\nFAILED stderr (last 500):")
        print(result.stderr[-500:])

print("\nDONE")
