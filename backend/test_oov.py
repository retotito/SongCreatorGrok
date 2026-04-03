"""Check which words from Beautiful Day lyrics are OOV in MFA dictionary."""
import re, os

ref_path = os.path.expanduser("~/Music/UltraStar Deluxe/U2 - Beautiful Day [VIDEO]/U2 - Beautiful Day.txt")
with open(ref_path) as f:
    ref_lines = f.readlines()

# Extract words from reference Ultrastar file
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

# Apply same cleaning as alignment.py fix
clean_words = []
for line in lyrics_text.strip().split('\n'):
    line = line.strip()
    if not line:
        continue
    clean_line = line.replace('-', '')
    clean_line = re.sub(r"[^\w\s']", '', clean_line)
    clean_line = clean_line.lower()
    clean_words.extend(clean_line.split())

# Load MFA dictionary
dict_path = os.path.expanduser("~/Documents/MFA/pretrained_models/dictionary/english_mfa.dict")
dict_words = set()
with open(dict_path) as f:
    for line in f:
        word = line.split('\t')[0].strip().lower()
        dict_words.add(word)

print(f"Dictionary size: {len(dict_words)} words")
print(f"Transcript words: {len(clean_words)}")

oov = [w for w in clean_words if w not in dict_words]
print(f"OOV words: {len(oov)}")
for w in sorted(set(oov)):
    count = oov.count(w)
    print(f"  '{w}' (appears {count}x)")

# Also show first 10 words of transcript
print(f"\nFirst 30 words: {' '.join(clean_words[:30])}")
print(f"Last 30 words: {' '.join(clean_words[-30:])}")
