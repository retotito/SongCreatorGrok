"""Test MFA alignment with Beautiful Day vocals."""
import subprocess
import tempfile
import os
import shutil

# Paths
vocals_path = "/Users/retokupfer/projects/SongCreatorGrok/backend/uploads/be3676fb/U2 - Beautiful Day_vocals.mp3"
mfa_bin = os.path.expanduser("~/miniconda3/envs/mfa/bin/mfa")

# First line of lyrics for quick test
test_lyrics = """The heart is a bloom
Shoots up through the stony ground
But there's no room
No space to rent in this town"""

# MFA needs: a folder with .wav + .txt files (same base name)
work_dir = tempfile.mkdtemp(prefix="mfa_test_")
corpus_dir = os.path.join(work_dir, "corpus")
output_dir = os.path.join(work_dir, "output")
os.makedirs(corpus_dir)
os.makedirs(output_dir)

# Convert MP3 to WAV (MFA prefers WAV)
wav_path = os.path.join(corpus_dir, "song.wav")
print(f"Converting vocals to WAV...")
# Use ffmpeg or sox - let's try with python
import librosa
import soundfile as sf

# Load just the first 30 seconds for a quick test
y, sr = librosa.load(vocals_path, sr=16000, mono=True, duration=30.0)
sf.write(wav_path, y, sr)
print(f"Wrote {len(y)/sr:.1f}s audio to {wav_path}")

# Write transcript file (same name as wav, .txt extension)
txt_path = os.path.join(corpus_dir, "song.txt")
with open(txt_path, "w") as f:
    f.write(test_lyrics)
print(f"Wrote lyrics to {txt_path}")

# Run MFA align using conda run to get proper environment
print(f"\nRunning MFA alignment...")
conda_bin = os.path.expanduser("~/miniconda3/bin/conda")
cmd = [
    conda_bin, "run", "-n", "mfa",
    "mfa", "align",
    corpus_dir,
    "english_mfa",       # dictionary
    "english_mfa",       # acoustic model
    output_dir,
    "--clean",
    "--single_speaker",
]
print(f"Command: {' '.join(cmd)}")

result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
print(f"Exit code: {result.returncode}")
if result.stdout:
    print(f"STDOUT:\n{result.stdout[-1000:]}")
if result.stderr:
    print(f"STDERR:\n{result.stderr[-1000:]}")

# Check output
print(f"\nOutput files:")
for root, dirs, files in os.walk(output_dir):
    for f in sorted(files):
        full = os.path.join(root, f)
        rel = os.path.relpath(full, output_dir)
        size = os.path.getsize(full)
        print(f"  {rel}  ({size:,} bytes)")

# Parse TextGrid output if exists
tg_files = []
for root, dirs, files in os.walk(output_dir):
    for f in files:
        if f.endswith('.TextGrid'):
            tg_files.append(os.path.join(root, f))

if tg_files:
    print(f"\n=== ALIGNMENT RESULTS ===")
    with open(tg_files[0]) as f:
        content = f.read()
    print(content[:3000])
else:
    print("\nNo TextGrid output found!")

# Cleanup
# shutil.rmtree(work_dir)  # Keep for debugging
print(f"\nWork dir: {work_dir}")
