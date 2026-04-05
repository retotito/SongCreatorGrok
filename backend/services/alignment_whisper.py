"""Whisper-based alignment: uses Whisper word timestamps + proportional syllable splitting.

This replaces MFA chunked alignment, which suffered from catastrophic drift (+15-30s)
due to chunk stitching errors. Whisper processes the entire audio at once, so there's
no drift — measured accuracy is ~150ms mean absolute error vs reference.

Algorithm:
1. Parse lyrics into syllables (reuse parse_lyrics from alignment.py)
2. Reconstruct words from syllable groups
3. Match lyrics words to Whisper word timestamps (fuzzy sequential matching)
4. For each matched word: distribute syllables proportionally within word's time span
5. Interpolate any unmatched words from neighbors
"""

import re
from typing import List, Optional
from utils.logger import log_step


def parse_lyrics(lyrics_text: str) -> List[List[dict]]:
    """Parse lyrics text into list of lines, each containing syllables.
    
    Duplicated from alignment.py to avoid triggering the slow MFA check
    that runs at import time in that module.
    
    Rules:
    - Each line = one phrase
    - Hyphens split syllables: "beau-ti-ful" -> ["beau", "ti", "ful"]
    - [RAP] / [/RAP] markers preserved
    - Empty lines ignored
    """
    lines = []
    is_rap = False
    
    for line in lyrics_text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        
        if line.upper() == '[RAP]':
            is_rap = True
            continue
        if line.upper() == '[/RAP]':
            is_rap = False
            continue
        
        syllables = []
        words = line.split()
        for i, word in enumerate(words):
            parts = word.split('-')
            for j, part in enumerate(parts):
                if not part:
                    continue
                prefix = " " if (j == 0 and len(syllables) > 0) else ""
                syllables.append({
                    "text": prefix + part,
                    "is_rap": is_rap,
                    "is_word_start": j == 0,
                    "word": word,
                    "line_index": len(lines)
                })
        
        if syllables:
            lines.append(syllables)
    
    return lines


def align_whisper(
    lyrics_text: str,
    whisper_words: List[dict],
    language: str = "english"
) -> List[dict]:
    """Align lyrics to audio using Whisper word timestamps.
    
    Args:
        lyrics_text: Full lyrics text with lines and hyphenated syllables
        whisper_words: List of {word, start, end} from Whisper transcription
        language: Language (unused for now, reserved for future)
        
    Returns:
        List of dicts matching alignment.py format:
        [{"syllable": "beau", "start": 1.23, "end": 1.56, "confidence": 0.85,
          "is_rap": False, "method": "whisper", "line_index": 0}, ...]
    """
    parsed = parse_lyrics(lyrics_text)
    flat_syllables = [s for line in parsed for s in line]
    
    if not flat_syllables:
        log_step("ALIGN", "No syllables parsed from lyrics")
        return []
    
    if not whisper_words:
        log_step("ALIGN", "No Whisper words available, cannot align")
        return []
    
    log_step("ALIGN", f"Whisper alignment: {len(flat_syllables)} syllables, {len(whisper_words)} Whisper words")
    
    # ── Step 1: Build word groups from syllables ──
    word_groups = _build_word_groups(flat_syllables)
    log_step("ALIGN", f"Built {len(word_groups)} word groups from syllables")
    
    # ── Step 2: Match lyrics words to Whisper words ──
    matches = _match_words(word_groups, whisper_words)
    matched_count = sum(1 for m in matches if m is not None)
    log_step("ALIGN", f"Matched {matched_count}/{len(word_groups)} words to Whisper timestamps")
    
    # ── Step 3: Interpolate unmatched words ──
    _interpolate_unmatched(word_groups, matches, whisper_words)
    
    # ── Step 4: Distribute syllables within each word's time span ──
    results = _distribute_syllables(word_groups, matches)
    
    log_step("ALIGN", f"Whisper alignment complete: {len(results)} syllables")
    if results:
        log_step("ALIGN", f"  First: '{results[0]['syllable']}' at {results[0]['start']:.3f}s")
        log_step("ALIGN", f"  Last:  '{results[-1]['syllable']}' at {results[-1]['end']:.3f}s")
    
    return results


def _build_word_groups(flat_syllables: list) -> list:
    """Group syllables into words based on is_word_start markers.
    
    Returns list of word groups:
    [{"word": "beautiful", "clean": "beautiful", "syllables": [...], 
      "line_index": 0, "is_rap": False}, ...]
    """
    groups = []
    current = None
    
    for syl in flat_syllables:
        if syl["is_word_start"] or current is None:
            if current is not None:
                groups.append(current)
            # Reconstruct clean word from the "word" field (has hyphens)
            raw_word = syl.get("word", syl["text"])
            clean = _clean_word(raw_word)
            current = {
                "word": raw_word,
                "clean": clean,
                "syllables": [syl],
                "line_index": syl.get("line_index", 0),
                "is_rap": syl.get("is_rap", False),
            }
        else:
            current["syllables"].append(syl)
    
    if current is not None:
        groups.append(current)
    
    return groups


def _clean_word(word: str) -> str:
    """Normalize word for matching: lowercase, remove punctuation except apostrophes, remove hyphens."""
    w = word.replace('-', '').lower().strip()
    # Normalize curly apostrophes
    w = w.replace('\u2019', "'").replace('\u2018', "'")
    # Remove all punctuation except apostrophes
    w = re.sub(r"[^\w']", '', w)
    return w


def _match_words(word_groups: list, whisper_words: list) -> list:
    """Match lyrics words to Whisper words sequentially with lookahead.
    
    Returns a list parallel to word_groups, where each element is either:
    - A dict {start, end, whisper_idx} for matched words
    - None for unmatched words
    """
    matches = [None] * len(word_groups)
    w_idx = 0  # Whisper index
    
    for g_idx, group in enumerate(word_groups):
        if w_idx >= len(whisper_words):
            break
        
        g_clean = group["clean"]
        
        # Direct match
        ww = whisper_words[w_idx]
        ww_clean = _clean_word(ww.get("word", ""))
        
        if g_clean == ww_clean:
            matches[g_idx] = {
                "start": ww["start"],
                "end": ww["end"],
                "whisper_idx": w_idx,
                "confidence": 0.9,
            }
            w_idx += 1
            continue
        
        # Lookahead in Whisper words (skip Whisper words not in lyrics)
        found = False
        for look_w in range(1, 8):
            if w_idx + look_w >= len(whisper_words):
                break
            if _clean_word(whisper_words[w_idx + look_w].get("word", "")) == g_clean:
                w_idx += look_w
                matches[g_idx] = {
                    "start": whisper_words[w_idx]["start"],
                    "end": whisper_words[w_idx]["end"],
                    "whisper_idx": w_idx,
                    "confidence": 0.8,
                }
                w_idx += 1
                found = True
                break
        
        if found:
            continue
        
        # Lookahead in lyrics words (skip lyrics words not in Whisper)
        for look_g in range(1, 8):
            if g_idx + look_g >= len(word_groups):
                break
            if word_groups[g_idx + look_g]["clean"] == ww_clean:
                # Don't advance w_idx — let the future group match it
                break
        
        # This word is unmatched, try next Whisper word
        # Don't advance w_idx (Whisper word might match a later lyrics word)
    
    return matches


def _interpolate_unmatched(word_groups: list, matches: list, whisper_words: list):
    """Fill in timing for unmatched words by interpolating from neighbors.
    
    Modifies matches in-place.
    """
    n = len(matches)
    
    # Build list of (index, start_time) for matched words
    anchors = [(i, m["start"], m["end"]) for i, m in enumerate(matches) if m is not None]
    
    if not anchors:
        # No matches at all — distribute evenly across audio
        if whisper_words:
            audio_start = whisper_words[0]["start"]
            audio_end = whisper_words[-1]["end"]
        else:
            audio_start = 0.0
            audio_end = 60.0
        
        dur_per_word = (audio_end - audio_start) / max(1, n)
        for i in range(n):
            matches[i] = {
                "start": audio_start + i * dur_per_word,
                "end": audio_start + (i + 1) * dur_per_word,
                "whisper_idx": -1,
                "confidence": 0.3,
            }
        return
    
    for i in range(n):
        if matches[i] is not None:
            continue
        
        # Find nearest anchors before and after
        prev_anchor = None
        next_anchor = None
        
        for idx, start, end in anchors:
            if idx < i:
                prev_anchor = (idx, start, end)
            elif idx > i and next_anchor is None:
                next_anchor = (idx, start, end)
                break
        
        if prev_anchor and next_anchor:
            # Interpolate between neighbors
            p_idx, p_start, p_end = prev_anchor
            n_idx, n_start, n_end = next_anchor
            # Position proportionally between prev and next
            frac = (i - p_idx) / (n_idx - p_idx)
            t_start = p_end + frac * (n_start - p_end)
            t_end = t_start + (n_end - n_start) / max(1, n_idx - p_idx)
            # Ensure end doesn't exceed next anchor start
            t_end = min(t_end, n_start)
            if t_end <= t_start:
                t_end = t_start + 0.05  # minimum 50ms
        elif prev_anchor:
            # After last anchor — extrapolate forward
            p_idx, p_start, p_end = prev_anchor
            gap = i - p_idx
            avg_word_dur = (p_end - p_start)
            t_start = p_end + (gap - 1) * avg_word_dur * 0.5
            t_end = t_start + avg_word_dur
        elif next_anchor:
            # Before first anchor — extrapolate backward
            n_idx, n_start, n_end = next_anchor
            gap = n_idx - i
            avg_word_dur = (n_end - n_start)
            t_start = n_start - gap * avg_word_dur
            t_end = t_start + avg_word_dur
            if t_start < 0:
                t_start = 0.0
        else:
            continue
        
        matches[i] = {
            "start": t_start,
            "end": t_end,
            "whisper_idx": -1,
            "confidence": 0.4,  # lower confidence for interpolated
        }


def _distribute_syllables(word_groups: list, matches: list) -> list:
    """Distribute syllables within each word's time span proportionally.
    
    For single-syllable words: syllable gets the full word time.
    For multi-syllable words: time is distributed proportionally by character count
    (rough proxy for duration — consonant clusters take longer).
    """
    results = []
    
    for g_idx, group in enumerate(word_groups):
        match = matches[g_idx]
        if match is None:
            # This shouldn't happen after interpolation, but handle gracefully
            continue
        
        syllables = group["syllables"]
        word_start = match["start"]
        word_end = match["end"]
        word_dur = word_end - word_start
        confidence = match.get("confidence", 0.5)
        
        if len(syllables) == 1:
            # Single syllable — use full word span
            results.append({
                "syllable": syllables[0]["text"],
                "start": word_start,
                "end": word_end,
                "confidence": confidence,
                "is_rap": syllables[0].get("is_rap", False),
                "method": "whisper",
                "line_index": group["line_index"],
            })
        else:
            # Multiple syllables — distribute proportionally by character weight
            weights = []
            for syl in syllables:
                text = syl["text"].strip()
                # Weight: character count with minimum of 1
                w = max(1, len(text))
                weights.append(w)
            
            total_weight = sum(weights)
            current_time = word_start
            
            for syl_idx, syl in enumerate(syllables):
                syl_dur = word_dur * weights[syl_idx] / total_weight
                syl_start = current_time
                syl_end = current_time + syl_dur
                
                # Ensure last syllable ends at word_end
                if syl_idx == len(syllables) - 1:
                    syl_end = word_end
                
                results.append({
                    "syllable": syl["text"],
                    "start": syl_start,
                    "end": syl_end,
                    "confidence": confidence * 0.9,  # slightly lower for split syllables
                    "is_rap": syl.get("is_rap", False),
                    "method": "whisper",
                    "line_index": group["line_index"],
                })
                
                current_time = syl_end
    
    return results
