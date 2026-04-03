"""Debug MFA alignment failure."""
import tempfile, os, subprocess, librosa, soundfile as sf, glob

# Find vocals file
vocal_files = glob.glob('uploads/*/vocals_*')
if not vocal_files:
    vocal_files = glob.glob('uploads/*/*.mp3')
print(f"Found vocal files: {vocal_files}")

audio_path = vocal_files[0] if vocal_files else None
if not audio_path:
    print("No vocal file found!")
    exit(1)

temp_dir = tempfile.mkdtemp()
corpus_dir = os.path.join(temp_dir, "corpus")
output_dir = os.path.join(temp_dir, "output")
os.makedirs(corpus_dir)
os.makedirs(output_dir)

# Prepare audio - clip to first 30 seconds for faster test
audio_dest = os.path.join(corpus_dir, "song.wav")
y, sr = librosa.load(audio_path, sr=16000, mono=True, duration=30.0)
sf.write(audio_dest, y, sr)
print(f"Audio: {len(y)/sr:.1f}s at {sr}Hz -> {audio_dest}")

# Prepare transcript (just first line for speed)
transcript = "The heart is a bloom shoots up through the stony ground"
transcript_path = os.path.join(corpus_dir, "song.txt")
with open(transcript_path, "w") as f:
    f.write(transcript)
print(f"Transcript: {transcript}")
print(f"Corpus dir contents: {os.listdir(corpus_dir)}")

# Run MFA
conda = os.path.expanduser("~/miniconda3/bin/conda")
cmd = [conda, "run", "-n", "mfa", "mfa", "align",
       corpus_dir, "english_mfa", "english_mfa", output_dir,
       "--clean", "--single_speaker"]
print(f"\nRunning: {' '.join(cmd)}\n")

result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
print(f"Exit code: {result.returncode}")
print(f"\n--- STDOUT ---\n{result.stdout[:3000]}")
print(f"\n--- STDERR ---\n{result.stderr[:3000]}")

# Check output
if result.returncode == 0:
    print("\n--- OUTPUT FILES ---")
    for root, dirs, files in os.walk(output_dir):
        for f in sorted(files):
            print(f"  {os.path.join(root, f)}")
