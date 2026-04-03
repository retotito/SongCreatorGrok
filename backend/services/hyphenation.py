"""Auto-hyphenation service using pyphen.

Splits plain lyrics into syllables automatically.
Users can then review and correct the hyphenation.
"""

import re
from utils.logger import log_step

try:
    import pyphen
    PYPHEN_AVAILABLE = True
except ImportError:
    PYPHEN_AVAILABLE = False
    log_step("INIT", "pyphen not installed — auto-hyphenation disabled")

# Supported languages (pyphen locale codes)
LANGUAGE_MAP = {
    "en": "en_US",
    "de": "de_DE",
    "fr": "fr_FR",
    "es": "es_ES",
    "it": "it_IT",
    "pt": "pt_PT",
    "nl": "nl_NL",
    "pl": "pl_PL",
    "sv": "sv_SE",
    "fi": "fi_FI",
    "no": "nb_NO",
    "da": "da_DK",
    "hu": "hu_HU",
    "cs": "cs_CZ",
    "ro": "ro_RO",
    "hr": "hr_HR",
    "sk": "sk_SK",
}


def get_supported_languages():
    """Return list of supported language codes."""
    return list(LANGUAGE_MAP.keys())


def hyphenate_lyrics(lyrics: str, language: str = "en") -> dict:
    """Auto-hyphenate plain lyrics text.

    Args:
        lyrics: Plain text lyrics (one line per phrase).
        language: Language code (e.g., 'en', 'de').

    Returns:
        dict with:
            - hyphenated: Full hyphenated text
            - lines: List of { original, hyphenated, syllable_count }
            - total_syllables: Total syllable count
            - language: Language used
            - method: 'pyphen' or 'fallback'
    """
    if not PYPHEN_AVAILABLE:
        return _hyphenate_fallback(lyrics, language)

    locale = LANGUAGE_MAP.get(language, "en_US")

    try:
        dic = pyphen.Pyphen(lang=locale)
    except Exception:
        # Locale not available, fall back to English
        log_step("HYPHEN", f"Locale {locale} not available, using en_US")
        dic = pyphen.Pyphen(lang="en_US")

    result_lines = []
    total_syllables = 0

    for line in lyrics.strip().split("\n"):
        line = line.strip()
        if not line:
            result_lines.append({
                "original": "",
                "hyphenated": "",
                "syllable_count": 0,
            })
            continue

        # Skip marker lines like [RAP], [/RAP]
        if re.match(r"^\[.*\]$", line):
            result_lines.append({
                "original": line,
                "hyphenated": line,
                "syllable_count": 0,
            })
            continue

        # If line already contains hyphens, keep as-is
        if "-" in line and not line.startswith("-"):
            syl_count = _count_syllables_in_hyphenated(line)
            total_syllables += syl_count
            result_lines.append({
                "original": line,
                "hyphenated": line,
                "syllable_count": syl_count,
            })
            continue

        # Hyphenate each word
        hyphenated_words = []
        for word in line.split():
            # Preserve punctuation
            prefix, core, suffix = _split_punctuation(word)
            if core:
                hyphenated = dic.inserted(core, hyphen="-")
                # pyphen sometimes returns the word unchanged
                hyphenated_words.append(f"{prefix}{hyphenated}{suffix}")
            else:
                hyphenated_words.append(word)

        hyphenated_line = " ".join(hyphenated_words)
        syl_count = _count_syllables_in_hyphenated(hyphenated_line)
        total_syllables += syl_count

        result_lines.append({
            "original": line,
            "hyphenated": hyphenated_line,
            "syllable_count": syl_count,
        })

    log_step("HYPHEN", f"Hyphenated {len(result_lines)} lines, {total_syllables} syllables ({language})")

    return {
        "hyphenated": "\n".join(l["hyphenated"] for l in result_lines),
        "lines": result_lines,
        "total_syllables": total_syllables,
        "language": language,
        "method": "pyphen",
    }


def _hyphenate_fallback(lyrics: str, language: str) -> dict:
    """Simple fallback: treat each word as one syllable."""
    result_lines = []
    total_syllables = 0

    for line in lyrics.strip().split("\n"):
        line = line.strip()
        if not line or re.match(r"^\[.*\]$", line):
            result_lines.append({
                "original": line,
                "hyphenated": line,
                "syllable_count": 0,
            })
            continue

        syl_count = len(line.split())
        total_syllables += syl_count
        result_lines.append({
            "original": line,
            "hyphenated": line,
            "syllable_count": syl_count,
        })

    return {
        "hyphenated": "\n".join(l["hyphenated"] for l in result_lines),
        "lines": result_lines,
        "total_syllables": total_syllables,
        "language": language,
        "method": "fallback (pyphen not installed)",
    }


def _split_punctuation(word: str):
    """Split leading/trailing punctuation from a word core."""
    match = re.match(r"^([^\w]*)(\w.*\w|\w)([^\w]*)$", word)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return "", word, ""


def _count_syllables_in_hyphenated(line: str) -> int:
    """Count syllables in a hyphenated line. Each hyphen-separated part is one syllable."""
    count = 0
    for word in line.split():
        if re.match(r"^\[.*\]$", word):
            continue
        # Count parts separated by hyphens
        parts = word.split("-")
        count += len(parts)
    return count
