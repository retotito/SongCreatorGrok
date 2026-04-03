"""Test MFA alignment with full lyrics - reproduce and fix the pipeline failure."""
import sys, os, tempfile, subprocess, re
sys.path.insert(0, '.')

# Get the lyrics from the reference file to simulate what the server sends
ref_path = os.path.expanduser("~/Music/UltraStar Deluxe/U2 - Beautiful Day [VIDEO]/U2 - Beautiful Day.txt")
with open(ref_path) as f:
    ref_lines = f.readlines()

# Extract words from reference to build test lyrics
# (The actual lyrics the user pastes would be similar)
lyrics_lines = []
current_line = []
for line in ref_lines:
    line = line.strip()
    if line.startswith('#') or line == 'E':
        continue
    if line.startswith('-'):
        if current_line:
            lyrics_lines.append(' '.join(current_line))
            current_line = []
        continue
    if line.startswith(':') or line.startswith('*') or line.startswith('F:'):
        parts = line.split(' ', 4)
        if len(parts) >= 5:
            word = parts[4]
            current_line.append(word.strip())

if current_line:
    lyrics_lines.append(' '.join(current_line))

lyrics_text = '\n'.join(lyrics_lines)
print(f"Extracted {len(lyrics_lines)} lines of lyrics")
print(f"First 3 lines:")
for l in lyrics_lines[:3]:
    print(f"  {l}")

# Now simulate the FIXED transcript preparation
clean_words = []
for line in lyrics_text.strip().split('\n'):
    line = line.strip()
    if not line:
        continue
    clean_line = line.replace('-', '')
    clean_line = re.sub(r"[^\w\s']", '', clean_line)
    clean_line = clean_line.lower()
    words = clean_line.split()
    clean_words.extend(words)

transcript = ' '.join(clean_words)
print(f"\nTotal words: {len(clean_words)}")
print(f"Transcript (first 200 chars): {transcript[:200]}...")

# Set up corpus
temp_dir = tempfile.mkdtemp()
corpus_dir = os.path.join(temp_dir, "corpus")
output_dir = os.path.join(temp_dir, "output")
os.makedirs(corpus_dir)
os.makedirs(output_dir)

# Use existing WAV if available, otherwise convert
import librosa, soundfile as sf
audio_path = "uploads/95f32f0c/vocals_u2 - Beatuful Day vocals correctedmp3.mp3"
if not os.path.exists(audio_path):
    import glob
    files = glob.glob("uploads/*/vocals_*")
    audio_path = files[0] if files else None
    
audio_dest = os.path.join(corpus_dir, "song.wav")
y, sr = librosa.load(audio_path, sr=16000, mono=True)
sf.write(audio_dest, y, sr)
print(f"Audio: {len(y)/sr:.1f}s")

with open(os.path.join(corpus_dir, "song.txt"), 'w') as f:
    f.write(transcript)

# Run MFA
conda = os.path.expanduser("~/miniconda3/bin/conda")
cmd = [conda, "run", "-n", "mfa", "mfa", "align",
       corpus_dir, "english_mfa", "english_mfa", output_dir,
       "--clean", "--single_speaker"]

print(f"\nRunning MFA...")
result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
print(f"Exit code: {result.returncode}")
if result.stdout:
    print(f"STDOUT (last 500): {result.stdout[-500:]}")
if result.stderr:
    print(f"STDERR (last 500): {result.stderr[-500:]}")

if result.returncode == 0:
    # Check output
    for root, dirs, files in os.walk(output_dir):
        for f in sorted(files):
            path = os.path.join(root, f)
            print(f"  Output: {path} ({os.path.getsize(path)} bytes)")
            if f.endswith('.TextGrid'):
                with open(path) as tf:
                    content = tf.read()
                # Count words
                word_count = content.count('text = "') - content.count('text = ""')
                print(f"  Words aligned: ~{word_count}")
    print("\nSUCCESS! MFA aligned full lyrics.")
else:
    print("\nFAILED. See errors above.")
