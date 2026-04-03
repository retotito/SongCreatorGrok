"""Test pyphen hyphenation on common song words."""
import sys
sys.path.insert(0, '.')
from services.hyphenation import hyphenate_lyrics

# Build test lyrics — one word per line for easy checking
test_words = [
    'away', 'today', 'beautiful', 'everywhere', 'anywhere', 'nowhere',
    'nothing', 'something', 'everything', 'never', 'ever', 'over',
    'under', 'about', 'around', 'again', 'against', 'alone',
    'alive', 'above', 'below', 'between', 'behind', 'before',
    'because', 'become', 'believe', 'beyond', 'together', 'forever',
    'heaven', 'seven', 'eleven', 'open', 'broken', 'spoken',
    'fire', 'desire', 'higher', 'power', 'flower', 'tower',
    'every', 'only', 'really', 'maybe', 'baby', 'crazy',
    'colour', 'running', 'coming', 'going', 'knowing',
    'stony', 'story', 'glory', 'morning', 'evening',
    "you're", "there's", "don't", "can't", "won't", "isn't",
    "it's", "we're", "they're",
    'through', 'thought', 'though', 'enough', 'tough', 'rough',
    'world', 'heart', 'earth', 'dream', 'scream', 'stream',
    'anywhere', 'everyone', 'overcome', 'understand', 'underneath',
    'impossible', 'unbelievable', 'extraordinary', 'hallelujah',
    'paradise', 'hurricane', 'waterfall', 'yesterday', 'tomorrow',
    'surrender', 'remember', 'december', 'november', 'september',
]

lyrics_text = '\n'.join(test_words)
result = hyphenate_lyrics(lyrics_text, 'en')

# Known correct syllable counts for validation
known = {
    'away': 2, 'today': 2, 'beautiful': 3, 'everywhere': 4,
    'anywhere': 3, 'nowhere': 2, 'nothing': 2, 'something': 2,
    'everything': 4, 'never': 2, 'ever': 2, 'over': 2,
    'again': 2, 'alone': 2, 'alive': 2, 'above': 2,
    'believe': 2, 'beyond': 2, 'together': 3, 'forever': 3,
    'heaven': 2, 'seven': 2, 'eleven': 3, 'desire': 2,
    'higher': 2, 'power': 2, 'flower': 2, 'tower': 2,
    'fire': 1, 'every': 3, 'really': 2, 'maybe': 2,
    'morning': 2, 'evening': 2, 'stony': 2,
    'through': 1, 'thought': 1, 'though': 1, 'enough': 2,
    'world': 1, 'heart': 1, 'earth': 1, 'dream': 1,
    'paradise': 3, 'hurricane': 3, 'yesterday': 3, 'tomorrow': 3,
    'remember': 3, 'hallelujah': 4, 'understand': 3,
}

print(f"{'Word':<20} {'Hyphenated':<25} {'#':<4} {'Expected':<8} {'Status'}")
print("-" * 70)

problems = []
for i, line_info in enumerate(result['lines']):
    w = test_words[i]
    hyphenated = line_info['hyphenated']
    n = line_info['syllable_count']
    expected = known.get(w.lower().replace("'", "'"), None)
    
    if expected and n != expected:
        status = f"WRONG (expected {expected})"
        problems.append((w, hyphenated, n, expected))
    elif hyphenated == w and len(w) > 5:
        status = "NOT SPLIT?"
        problems.append((w, hyphenated, n, '?'))
    else:
        status = "ok"
    
    print(f"{w:<20} {hyphenated:<25} {n:<4} {str(expected or '?'):<8} {status}")

print(f"\n{'='*70}")
print(f"PROBLEMS FOUND: {len(problems)}")
for w, result, got, expected in problems:
    print(f"  {w} -> {result} ({got} syllables, expected {expected})")
