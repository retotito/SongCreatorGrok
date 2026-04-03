"""Check which words in the transcript are NOT in MFA's english dictionary."""
import sys, os, subprocess
sys.path.insert(0, 'backend')
from services.alignment import CONDA_BIN, MFA_ENV

# Read actual transcript
with open('backend/downloads/mfa_transcript.txt') as f:
    transcript = f.read().strip()

words = transcript.split()
print(f"Total words: {len(words)}")
print(f"Unique words: {len(set(words))}")

# Check OOV using mfa validate
import tempfile, soundfile as sf, librosa
import numpy as np

audio_path = 'backend/uploads/e304db7c/vocals_u2 - Beatuful Day vocals correctedmp3.mp3'
if not os.path.exists(audio_path):
    audio_path = 'backend/uploads/004731a3/vocals_u2 - Beatuful Day vocals correctedmp3.mp3'

with tempfile.TemporaryDirectory() as temp_dir:
    corpus_dir = os.path.join(temp_dir, "corpus")
    os.makedirs(corpus_dir)
    
    # Create a short dummy wav (1 second of silence)
    sr = 16000
    silence = np.zeros(sr, dtype=np.float32)
    sf.write(os.path.join(corpus_dir, "song.wav"), silence, sr)
    
    with open(os.path.join(corpus_dir, "song.txt"), 'w') as f:
        f.write(transcript)
    
    cmd = [
        CONDA_BIN, "run", "-n", MFA_ENV,
        "mfa", "validate",
        corpus_dir,
        "english_mfa",
        "--clean",
    ]
    
    print("Running MFA validate...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    print(f"Exit code: {result.returncode}")
    if result.stdout:
        print(f"\nSTDOUT:\n{result.stdout[-2000:]}")
    if result.stderr:
        print(f"\nSTDERR:\n{result.stderr[-2000:]}")
