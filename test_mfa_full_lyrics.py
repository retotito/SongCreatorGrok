"""Test MFA with full audio + full lyrics to see the specific failure."""
import sys, os, subprocess, tempfile, re
sys.path.insert(0, 'backend')

import librosa
import soundfile as sf
from services.alignment import CONDA_BIN, MFA_ENV, _parse_textgrid_words

audio_path = 'backend/uploads/004731a3/vocals_u2 - Beatuful Day vocals correctedmp3.mp3'

# Full lyrics (cleaned for MFA)
lyrics_lines = [
    "The heart is a bloom shoots up through stony ground",
    "But there's no room no space to rent in this town",
    "You're out of luck and the reason that you had to care",
    "The traffic is stuck and you're not moving anywhere",
    "You thought you'd found a friend to take you out of this place",
    "Someone you could lend a hand in return for grace",
    "It's a beautiful day the sky falls",
    "And you feel like it's a beautiful day",
    "It's a beautiful day",
    "Don't let it get away",
]

all_words = ' '.join(lyrics_lines).lower()
all_words = re.sub(r"[^\w\s']", '', all_words)
word_count = len(all_words.split())
print(f"Total words: {word_count}")
print(f"Transcript: {all_words[:100]}...")

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
        f.write(all_words)

    cmd = [
        CONDA_BIN, "run", "-n", MFA_ENV,
        "mfa", "align",
        corpus_dir,
        "english_mfa", "english_mfa",
        output_dir,
        "--clean", "--single_speaker",
        "--beam", "100", "--retry_beam", "400",
    ]

    print(f"Running MFA with {word_count} words on full audio...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    print(f"Exit code: {result.returncode}")

    if result.stderr:
        err_lines = [l for l in result.stderr.split('\n') if l.strip()]
        print(f"\nSTDERR ({len(err_lines)} lines):")
        for l in err_lines[-15:]:
            print(f"  {l}")

    if result.returncode == 0:
        for root, dirs, files in os.walk(output_dir):
            for f in files:
                if f.endswith('.TextGrid'):
                    tg = os.path.join(root, f)
                    words = _parse_textgrid_words(tg)
                    print(f"\nSUCCESS: {len(words)} words aligned")
                    for w in words[:15]:
                        print(f"  '{w['text']}'  {w['start']:.3f}s - {w['end']:.3f}s")
                    if words:
                        print(f"\n  GAP = {int(words[0]['start']*1000)}ms (ref: 13208ms)")
    else:
        print("\nMFA FAILED")
