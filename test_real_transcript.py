"""Test MFA with the ACTUAL full transcript from the failing run."""
import sys, os, subprocess, tempfile
sys.path.insert(0, 'backend')

import librosa
import soundfile as sf
from services.alignment import CONDA_BIN, MFA_ENV, _parse_textgrid_words

audio_path = 'backend/uploads/e304db7c/vocals_u2 - Beatuful Day vocals correctedmp3.mp3'
if not os.path.exists(audio_path):
    # Try previous session
    audio_path = 'backend/uploads/004731a3/vocals_u2 - Beatuful Day vocals correctedmp3.mp3'

# Read the ACTUAL transcript that was sent to MFA
with open('backend/downloads/mfa_transcript.txt') as f:
    transcript = f.read().strip()

word_count = len(transcript.split())
print(f"Transcript: {word_count} words")
print(f"First 100 chars: {transcript[:100]}...")

# Load audio
print("Loading audio...")
y, sr = librosa.load(audio_path, sr=16000, mono=True)
print(f"Audio: {len(y)/sr:.1f}s")

with tempfile.TemporaryDirectory() as temp_dir:
    corpus_dir = os.path.join(temp_dir, "corpus")
    output_dir = os.path.join(temp_dir, "output")
    os.makedirs(corpus_dir)
    os.makedirs(output_dir)

    wav_path = os.path.join(corpus_dir, "song.wav")
    sf.write(wav_path, y, sr)

    txt_path = os.path.join(corpus_dir, "song.txt")
    with open(txt_path, 'w') as f:
        f.write(transcript)

    cmd = [
        CONDA_BIN, "run", "-n", MFA_ENV,
        "mfa", "align",
        corpus_dir,
        "english_mfa", "english_mfa",
        output_dir,
        "--clean", "--single_speaker",
        "--beam", "100", "--retry_beam", "400",
    ]

    print(f"Running MFA with {word_count} words...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    print(f"Exit code: {result.returncode}")

    if result.stderr:
        print(f"\nFULL STDERR:")
        print(result.stderr)

    if result.returncode == 0:
        for root, dirs, files in os.walk(output_dir):
            for fn in files:
                if fn.endswith('.TextGrid'):
                    tg = os.path.join(root, fn)
                    words = _parse_textgrid_words(tg)
                    print(f"\nSUCCESS: {len(words)} words aligned")
                    for w in words[:10]:
                        print(f"  '{w['text']}'  {w['start']:.3f}s - {w['end']:.3f}s")
                    if words:
                        print(f"\n  GAP = {int(words[0]['start']*1000)}ms")
    else:
        print("\nMFA FAILED")
