"""Forced alignment using Montreal Forced Aligner (MFA).

Aligns lyrics text to audio to get per-syllable timing.
Falls back to energy-based detection if MFA is not available.
"""

import os
import json
import re
import tempfile
import subprocess
from typing import List, Tuple, Optional
from utils.logger import log_step, log_progress

# ── Detect MFA via conda environment (lazy) ──
CONDA_BIN = os.path.expanduser("~/miniconda3/bin/conda")
MFA_ENV = "mfa"
_mfa_checked = False
MFA_AVAILABLE = False

def _check_mfa():
    """Check if MFA is available in the conda environment."""
    global _mfa_checked, MFA_AVAILABLE
    if _mfa_checked:
        return MFA_AVAILABLE
    _mfa_checked = True
    if not os.path.exists(CONDA_BIN):
        MFA_AVAILABLE = False
        log_step("INIT", "MFA not found (conda not installed)")
        return False
    try:
        result = subprocess.run(
            [CONDA_BIN, "run", "-n", MFA_ENV, "mfa", "version"],
            capture_output=True, text=True, timeout=30
        )
        MFA_AVAILABLE = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        MFA_AVAILABLE = False
    if MFA_AVAILABLE:
        log_step("INIT", "MFA available via conda (real forced alignment)")
    else:
        log_step("INIT", "MFA not found, will use energy-based fallback alignment")
    return MFA_AVAILABLE


def align_lyrics_to_audio(
    audio_path: str,
    lyrics_text: str,
    language: str = "english",
    whisper_words: list = None
) -> List[dict]:
    """Align lyrics to audio, returning timing for each syllable.
    
    Args:
        audio_path: Path to vocal audio file
        lyrics_text: Full lyrics text with lines and hyphenated syllables
        language: Language for MFA model
        whisper_words: Optional Whisper word timestamps for drift correction
        
    Returns:
        List of dicts: [{"syllable": "beau", "start": 1.23, "end": 1.56, "confidence": 0.95}, ...]
    """
    # Parse lyrics into syllables with line structure
    parsed = parse_lyrics(lyrics_text)
    flat_syllables = [s for line in parsed for s in line]
    
    log_step("ALIGN", f"Parsed {len(flat_syllables)} syllables across {len(parsed)} lines")
    
    if _check_mfa():
        try:
            results = align_with_mfa(audio_path, lyrics_text, flat_syllables, language,
                                     whisper_words=whisper_words)
            log_step("ALIGN", f"MFA alignment succeeded: {len(results)} syllables")
            if results:
                log_step("ALIGN", f"  First syllable: '{results[0]['syllable']}' at {results[0]['start']:.3f}s (method={results[0]['method']})")
                log_step("ALIGN", f"  Last syllable: '{results[-1]['syllable']}' at {results[-1]['end']:.3f}s")
            # Write debug trace
            _write_alignment_debug(results, "mfa")
            return results
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log_step("ALIGN", f"MFA failed: {e}")
            log_step("ALIGN", f"MFA traceback:\n{tb}")
            log_step("ALIGN", "Falling back to energy-based alignment")
            # Write error to debug file (separate from mfa_error.txt which has stderr)
            err_path = os.path.join(os.path.dirname(__file__), '..', 'downloads', 'mfa_traceback.txt')
            try:
                with open(err_path, 'w') as ef:
                    ef.write(f"MFA ERROR: {e}\n\n{tb}\n")
            except:
                pass
    
    # Fallback: distribute syllables using energy-based segment detection
    log_step("ALIGN", "Using energy-based fallback alignment")
    results = align_fallback(audio_path, parsed)
    if results:
        log_step("ALIGN", f"  First syllable: '{results[0]['syllable']}' at {results[0]['start']:.3f}s")
        log_step("ALIGN", f"  Last syllable: '{results[-1]['syllable']}' at {results[-1]['end']:.3f}s")
    _write_alignment_debug(results, "fallback")
    return results


def _write_alignment_debug(results: list, method: str):
    """Write alignment results to a debug file for tracing."""
    debug_path = os.path.join(os.path.dirname(__file__), '..', 'downloads', 'alignment_debug.txt')
    try:
        with open(debug_path, 'w') as f:
            f.write(f"ALIGNMENT DEBUG TRACE (method={method})\n")
            f.write(f"{'='*70}\n")
            f.write(f"Total syllables: {len(results)}\n\n")
            f.write(f"{'#':<5} {'Syllable':<18} {'Start':>10} {'End':>10} {'Dur':>8} {'Conf':>6} {'Method':<20}\n")
            f.write(f"{'-'*77}\n")
            for i, r in enumerate(results):
                dur = r['end'] - r['start']
                f.write(f"{i:<5} {r['syllable']:<18} {r['start']:>10.4f} {r['end']:>10.4f} {dur:>8.4f} {r['confidence']:>6.2f} {r.get('method','?'):<20}\n")
        log_step("ALIGN", f"Debug trace written to {debug_path}")
    except Exception as e:
        log_step("ALIGN", f"Could not write debug trace: {e}")


def parse_lyrics(lyrics_text: str) -> List[List[str]]:
    """Parse lyrics text into list of lines, each containing syllables.
    
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
        
        # Check for RAP markers
        if line.upper() == '[RAP]':
            is_rap = True
            continue
        if line.upper() == '[/RAP]':
            is_rap = False
            continue
        
        syllables = []
        words = line.split()
        for i, word in enumerate(words):
            # Split on hyphens for syllables
            parts = word.split('-')
            for j, part in enumerate(parts):
                if not part:
                    continue
                # Add space before word (except first syllable of first word)
                prefix = " " if (j == 0 and len(syllables) > 0) else ""
                syllables.append({
                    "text": prefix + part,
                    "is_rap": is_rap,
                    "is_word_start": j == 0,
                    "word": word,
                    "line_index": len(lines)  # track which line this syllable belongs to
                })
        
        if syllables:
            lines.append(syllables)
    
    return lines


def _clean_lyrics_to_words(lyrics_text: str) -> List[str]:
    """Convert lyrics text into a flat list of clean words for MFA."""
    clean_words = []
    for line in lyrics_text.strip().split('\n'):
        line = line.strip()
        if not line or line.upper() in ['[RAP]', '[/RAP]']:
            continue
        # Remove hyphens (MFA works on whole words)
        clean_line = line.replace('-', '')
        # Normalize curly apostrophes to straight
        clean_line = clean_line.replace('\u2019', "'").replace('\u2018', "'")
        # Strip punctuation except apostrophes (needed for contractions)
        clean_line = re.sub(r"[^\w\s']", '', clean_line)
        # Lowercase for dictionary matching
        clean_line = clean_line.lower()
        words = clean_line.split()
        clean_words.extend(words)
    return clean_words


def _detect_vocal_sections(y, sr, min_silence_sec=1.0, min_section_sec=1.0):
    """Detect vocal sections in audio by finding silence gaps.
    
    Returns list of (start_sec, end_sec) tuples for voiced sections.
    
    Pipeline:
    1. Find ALL raw voiced frames (even tiny ones)
    2. Merge segments with gaps < 0.5s (brief breath/dip within a word)
    3. Filter by min_section_sec (drop noise blips)
    4. Merge segments with gaps < min_silence_sec (group phrases into sections)
    """
    import librosa
    import numpy as np
    
    hop_length = 512
    frame_length = 2048
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)
    
    # Adaptive threshold: 8% of max RMS (lower to catch quieter vocal starts)
    threshold = np.max(rms) * 0.08
    is_voiced = rms > threshold
    
    # Step 1: Find ALL raw voiced segments (no duration filter yet)
    raw_segments = []
    in_seg = False
    seg_start = 0.0
    
    for i in range(len(is_voiced)):
        if is_voiced[i] and not in_seg:
            seg_start = times[i]
            in_seg = True
        elif not is_voiced[i] and in_seg:
            seg_end = times[i]
            raw_segments.append((seg_start, seg_end))
            in_seg = False
    if in_seg:
        seg_end = times[-1]
        raw_segments.append((seg_start, seg_end))
    
    # Step 2: Merge segments with very short gaps (< 0.5s) — these are
    # brief energy dips within a word/phrase, not real silence
    breath_merged = []
    for seg in raw_segments:
        if breath_merged and seg[0] - breath_merged[-1][1] < 0.5:
            breath_merged[-1] = (breath_merged[-1][0], seg[1])
        else:
            breath_merged.append(seg)
    
    # Step 3: NOW filter by minimum section duration
    filtered = [(s, e) for s, e in breath_merged if e - s >= min_section_sec]
    
    # Step 4: Merge sections with gaps < min_silence_sec
    merged = []
    for seg in filtered:
        if merged and seg[0] - merged[-1][1] < min_silence_sec:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)
    
    return merged


def _group_lyrics_into_sections(lyrics_text: str) -> List[List[str]]:
    """Split lyrics into sections at blank lines.
    
    Returns list of sections, each being a list of non-empty lines.
    If no blank lines, splits into groups of ~4 lines.
    """
    raw_lines = lyrics_text.strip().split('\n')
    
    sections = []
    current = []
    for line in raw_lines:
        stripped = line.strip()
        if stripped.upper() in ['[RAP]', '[/RAP]']:
            continue
        if not stripped:
            if current:
                sections.append(current)
                current = []
        else:
            current.append(stripped)
    if current:
        sections.append(current)
    
    # If only 1 section (no blank lines), split into chunks of ~4 lines
    if len(sections) <= 1 and sections:
        all_lines = sections[0]
        chunk_size = 4
        sections = [all_lines[i:i+chunk_size] for i in range(0, len(all_lines), chunk_size)]
    
    return sections


def _count_words_in_section(lines: List[str]) -> int:
    """Count words in a section of lyrics lines."""
    count = 0
    for line in lines:
        clean = line.replace('-', '')
        clean = re.sub(r"[^\w\s']", '', clean)
        count += len(clean.split())
    return count


def align_with_mfa(
    audio_path: str,
    lyrics_text: str,
    flat_syllables: list,
    language: str,
    whisper_words: list = None
) -> List[dict]:
    """Use MFA to align lyrics to audio via conda environment.
    
    Strategy: Segment-based alignment.
    1. Detect silence breaks in audio to find vocal segments
    2. Distribute lyrics sections across audio segments
    3. Run MFA on each segment independently (prevents drift accumulation)
    4. Merge results with correct global time offsets
    """
    import librosa
    import soundfile as sf

    MFA_LANG_MAP = {
        "en": "english", "de": "german", "fr": "french",
        "es": "spanish", "it": "italian", "pt": "portuguese",
        "nl": "dutch", "ja": "japanese", "ko": "korean",
        "zh": "mandarin_china",
    }
    mfa_lang = MFA_LANG_MAP.get(language, language)
    
    # ── Load audio at 16kHz ──
    y, sr = librosa.load(audio_path, sr=16000, mono=True)
    audio_duration = len(y) / sr
    log_step("ALIGN", f"Audio loaded: {audio_duration:.1f}s at 16kHz")
    
    # ── Detect vocal sections (silence breaks) ──
    vocal_sections = _detect_vocal_sections(y, sr, min_silence_sec=1.5, min_section_sec=1.0)
    
    # ── Refine: split long segments (>15s) using shorter silence gaps ──
    MAX_SEGMENT_SEC = 15.0
    refined_sections = []
    for (vs, ve) in vocal_sections:
        if ve - vs > MAX_SEGMENT_SEC:
            # Try progressively shorter silence thresholds to find sub-breaks
            start_sample = int(vs * sr)
            end_sample = int(ve * sr)
            y_sub = y[start_sample:end_sample]
            
            split_found = False
            for silence_thresh in [0.5, 0.3, 0.2]:
                sub_sections = _detect_vocal_sections(y_sub, sr, min_silence_sec=silence_thresh, min_section_sec=2.0)
                if len(sub_sections) > 1:
                    for ss, se in sub_sections:
                        refined_sections.append((round(ss + vs, 4), round(se + vs, 4)))
                    log_step("ALIGN", f"  Split long section {vs:.1f}-{ve:.1f}s into {len(sub_sections)} sub-sections (silence={silence_thresh}s)")
                    split_found = True
                    break
            
            if not split_found:
                # Force split at midpoint if still too long
                mid = (vs + ve) / 2
                refined_sections.append((vs, round(mid, 4)))
                refined_sections.append((round(mid, 4), ve))
                log_step("ALIGN", f"  Force-split long section {vs:.1f}-{ve:.1f}s at midpoint {mid:.1f}s")
        else:
            refined_sections.append((vs, ve))
    vocal_sections = refined_sections
    
    log_step("ALIGN", f"Final: {len(vocal_sections)} vocal sections:")
    for i, (vs, ve) in enumerate(vocal_sections):
        log_step("ALIGN", f"  Section {i}: {vs:.1f}s - {ve:.1f}s ({ve-vs:.1f}s)")
    
    # ── Build flat word list from lyrics ──
    all_clean_words = _clean_lyrics_to_words(lyrics_text)
    log_step("ALIGN", f"Lyrics: {len(all_clean_words)} words total")
    
    # Save full transcript for debugging
    debug_dir = os.path.join(os.path.dirname(__file__), '..', 'downloads')
    transcript_full = ' '.join(all_clean_words)
    try:
        with open(os.path.join(debug_dir, 'mfa_transcript.txt'), 'w') as f:
            f.write(transcript_full)
    except:
        pass
    
    # ── Assign words to audio segments ──
    word_assignments = _assign_words_to_segments(all_clean_words, vocal_sections, whisper_words)
    log_step("ALIGN", f"Assigned words to {len(word_assignments)} segments")
    for i, (seg_words, ss, se) in enumerate(word_assignments):
        log_step("ALIGN", f"  Seg {i}: {ss:.1f}-{se:.1f}s, {len(seg_words)} words: "
                 f"'{' '.join(seg_words[:5])}...'")
    
    # ── Run MFA on each segment ──
    all_word_intervals = []
    all_phones = []
    seg_data = []  # [(n_expected_lyrics_words, n_mfa_words)] for segments that produced output
    
    with tempfile.TemporaryDirectory() as temp_dir:
        for seg_idx, (seg_words, seg_start, seg_end) in enumerate(word_assignments):
            if not seg_words:
                log_step("ALIGN", f"  Segment {seg_idx}: no words, skipping")
                seg_data.append((0, 0))
                continue
            
            seg_transcript = ' '.join(seg_words)
            
            # Cut audio for this segment (with small padding)
            PAD = 0.3  # seconds padding before/after
            cut_start = max(0, seg_start - PAD)
            cut_end = min(audio_duration, seg_end + PAD)
            start_sample = int(cut_start * sr)
            end_sample = int(cut_end * sr)
            y_segment = y[start_sample:end_sample]
            
            log_step("ALIGN", f"  Segment {seg_idx}: {cut_start:.1f}-{cut_end:.1f}s, "
                     f"{len(seg_words)} words: '{seg_transcript[:50]}...'")
            
            # Create temp corpus for this segment
            seg_corpus = os.path.join(temp_dir, f"corpus_{seg_idx}")
            seg_output = os.path.join(temp_dir, f"output_{seg_idx}")
            os.makedirs(seg_corpus, exist_ok=True)
            os.makedirs(seg_output, exist_ok=True)
            
            wav_path = os.path.join(seg_corpus, f"seg{seg_idx}.wav")
            sf.write(wav_path, y_segment, sr)
            
            txt_path = os.path.join(seg_corpus, f"seg{seg_idx}.txt")
            with open(txt_path, 'w') as f:
                f.write(seg_transcript)
            
            # Run MFA on this segment
            cmd = [
                CONDA_BIN, "run", "-n", MFA_ENV,
                "mfa", "align",
                seg_corpus,
                f"{mfa_lang}_mfa",
                f"{mfa_lang}_mfa",
                seg_output,
                "--clean",
                "--single_speaker",
                "--beam", "100",
                "--retry_beam", "400",
            ]
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                
                if result.returncode != 0:
                    log_step("ALIGN", f"  Segment {seg_idx} MFA failed (exit {result.returncode})")
                    if result.stderr:
                        log_step("ALIGN", f"    stderr: {result.stderr[-200:]}")
                    # Try to dump error for debugging
                    try:
                        with open(os.path.join(debug_dir, f'mfa_error_seg{seg_idx}.txt'), 'w') as f:
                            f.write(f"Segment {seg_idx}: {cut_start:.1f}-{cut_end:.1f}s\n")
                            f.write(f"Words: {seg_transcript}\n\n")
                            f.write(f"Exit: {result.returncode}\nStderr:\n{result.stderr}\n")
                    except:
                        pass
                    seg_data.append((len(seg_words), 0))
                    continue
                
                # Parse TextGrid output
                tg_path = None
                for root, dirs, files in os.walk(seg_output):
                    for f_name in files:
                        if f_name.endswith('.TextGrid'):
                            tg_path = os.path.join(root, f_name)
                            break
                
                if not tg_path:
                    log_step("ALIGN", f"  Segment {seg_idx}: no TextGrid output")
                    seg_data.append((len(seg_words), 0))
                    continue
                
                # Save TextGrid for debugging
                try:
                    import shutil
                    shutil.copy2(tg_path, os.path.join(debug_dir, f'mfa_seg{seg_idx}.TextGrid'))
                except:
                    pass
                
                # Parse and offset to global time
                seg_words_iv = _parse_textgrid_words(tg_path)
                seg_phones = _parse_textgrid_phones(tg_path)
                
                # Offset all timestamps by cut_start (to get global time)
                for w in seg_words_iv:
                    w["start"] = round(w["start"] + cut_start, 4)
                    w["end"] = round(w["end"] + cut_start, 4)
                    w["seg_idx"] = seg_idx
                for p in seg_phones:
                    p["start"] = round(p["start"] + cut_start, 4)
                    p["end"] = round(p["end"] + cut_start, 4)        
                
                # Merge MFA contraction splits within this segment
                seg_words_iv = _merge_mfa_contractions(seg_words_iv)
                
                log_step("ALIGN", f"  Segment {seg_idx}: {len(seg_words_iv)} words "
                         f"(expected {len(seg_words)}), {len(seg_phones)} phones aligned")
                
                seg_data.append((len(seg_words), len(seg_words_iv)))
                all_word_intervals.extend(seg_words_iv)
                all_phones.extend(seg_phones)
                
            except subprocess.TimeoutExpired:
                log_step("ALIGN", f"  Segment {seg_idx}: MFA timed out")
                seg_data.append((len(seg_words), 0))
                continue
    
    log_step("ALIGN", f"Total MFA output: {len(all_word_intervals)} words, {len(all_phones)} phones")
    
    if not all_word_intervals:
        raise RuntimeError("MFA produced no word intervals from any segment")
    
    # Save debug info
    _dump_phone_debug(all_phones, word_intervals=all_word_intervals, time_range=(0, audio_duration))
    
    if all_word_intervals:
        log_step("ALIGN", f"  First word: '{all_word_intervals[0]['text']}' at {all_word_intervals[0]['start']:.3f}s")
        log_step("ALIGN", f"  Last word: '{all_word_intervals[-1]['text']}' at {all_word_intervals[-1]['end']:.3f}s")
    
    # ── Map word intervals to syllables ──
    results = _map_mfa_words_to_syllables(all_word_intervals, flat_syllables, lyrics_text, all_phones,
                                          seg_word_counts=seg_data)

    # ── Post-process: anchor to Whisper for drift correction ──
    if whisper_words:
        results = _anchor_to_whisper(results, whisper_words)

    return results


def _assign_words_to_segments(all_words: list, vocal_sections: list,
                              whisper_words: list = None) -> list:
    """Assign lyrics words to audio vocal segments.
    
    Works at the WORD level rather than section level, avoiding the
    mismatch between arbitrary lyrics chunking and audio silence breaks.
    
    Strategy:
    - With Whisper timestamps: match each lyrics word to its Whisper
      timestamp, then assign to the vocal segment containing that time.
      This ensures words land in the correct audio segment.
    - Without Whisper: distribute words proportionally by segment duration
    
    Returns list of (word_list, seg_start, seg_end) tuples.
    """
    total_words = len(all_words)
    if total_words == 0 or not vocal_sections:
        return []
    
    # If only one segment, give it all words
    if len(vocal_sections) == 1:
        return [(all_words, vocal_sections[0][0], vocal_sections[0][1])]
    
    if whisper_words and len(whisper_words) >= total_words * 0.3:
        return _assign_words_whisper_direct(all_words, vocal_sections, whisper_words)
    else:
        return _assign_words_proportional(all_words, vocal_sections)


def _assign_words_whisper_direct(all_words: list, vocal_sections: list,
                                  whisper_words: list) -> list:
    """Assign words to segments using Whisper timestamps directly.
    
    1. Sequentially match each lyrics word to a Whisper word
    2. Use that Whisper word's timestamp to find the right vocal segment
    3. Group consecutive words assigned to the same segment
    """
    # ── Step 1: Match lyrics words to Whisper words ──
    # Both lists should be in order; do sequential fuzzy matching
    word_timestamps = [None] * len(all_words)  # timestamps for each lyrics word
    
    wi = 0  # whisper index
    for li, lw in enumerate(all_words):
        lw_clean = lw.lower().strip(".,!?;:'-\"()")
        if not lw_clean:
            continue
        
        # Search for a match in a wide window around current whisper position
        # Wide window handles Whisper inserting/skipping words vs lyrics
        best_wi = None
        best_score = -1
        search_end = min(len(whisper_words), wi + 15)
        
        for try_wi in range(wi, search_end):
            ww_clean = whisper_words[try_wi]["word"].lower().strip(".,!?;:'-\"()")
            
            # Exact match (best)
            if ww_clean == lw_clean:
                best_wi = try_wi
                best_score = 3
                break
            
            # Prefix match (3+ chars)
            if len(lw_clean) >= 3 and len(ww_clean) >= 3:
                if lw_clean[:3] == ww_clean[:3]:
                    if best_score < 2:
                        best_wi = try_wi
                        best_score = 2
            
            # Short word match (1-2 chars)
            if len(lw_clean) <= 2 and lw_clean == ww_clean:
                if best_score < 2:
                    best_wi = try_wi
                    best_score = 2
        
        if best_wi is not None:
            word_timestamps[li] = whisper_words[best_wi]["start"]
            wi = best_wi + 1
    
    matched = sum(1 for t in word_timestamps if t is not None)
    log_step("ALIGN", f"Whisper direct match: {matched}/{len(all_words)} words matched")
    
    # ── Step 2: Fill gaps by interpolation ──
    # Words without a Whisper match get interpolated from neighbors
    _interpolate_timestamps(word_timestamps, vocal_sections)
    
    # ── Step 3: Assign each word to a vocal segment ──
    word_segment_idx = [0] * len(all_words)
    for li in range(len(all_words)):
        wt = word_timestamps[li]
        if wt is None:
            continue
        
        best_si = 0
        best_dist = float('inf')
        for si, (ss, se) in enumerate(vocal_sections):
            if ss - 1.0 <= wt <= se + 0.5:
                best_si = si
                best_dist = 0
                break
            dist = min(abs(wt - ss), abs(wt - se))
            if dist < best_dist:
                best_dist = dist
                best_si = si
        word_segment_idx[li] = best_si
    
    # ── Step 4: Group into segment assignments ──
    # Build: segment_index -> [word_indices]
    segment_words = {}
    for li in range(len(all_words)):
        si = word_segment_idx[li]
        if si not in segment_words:
            segment_words[si] = []
        segment_words[si].append(li)
    
    assignments = []
    for si in sorted(segment_words.keys()):
        indices = segment_words[si]
        words = [all_words[i] for i in indices]
        ss, se = vocal_sections[si]
        assignments.append((words, ss, se))
    
    # Debug: write assignment details
    debug_dir = os.path.join(os.path.dirname(__file__), '..', 'downloads')
    try:
        with open(os.path.join(debug_dir, 'segment_assignment_debug.txt'), 'w') as f:
            f.write(f"SEGMENT ASSIGNMENT (Whisper-direct)\n{'='*70}\n")
            f.write(f"Words: {len(all_words)}, Matched: {matched}\n\n")
            for si, (words, ss, se) in enumerate(assignments):
                f.write(f"Seg {si}: {ss:.1f}-{se:.1f}s ({se-ss:.1f}s), {len(words)} words\n")
                for w in words:
                    f.write(f"  {w}\n")
                f.write("\n")
    except:
        pass
    
    return assignments


def _interpolate_timestamps(word_timestamps: list, vocal_sections: list):
    """Fill None gaps in word_timestamps by interpolating between known neighbors."""
    n = len(word_timestamps)
    if n == 0:
        return
    
    # Forward fill: find runs of None and interpolate
    i = 0
    while i < n:
        if word_timestamps[i] is not None:
            i += 1
            continue
        
        # Find the run of Nones
        run_start = i
        while i < n and word_timestamps[i] is None:
            i += 1
        run_end = i  # exclusive
        
        # Get bounds
        prev_time = word_timestamps[run_start - 1] if run_start > 0 else vocal_sections[0][0]
        next_time = word_timestamps[run_end] if run_end < n else (
            word_timestamps[run_start - 1] + 0.3 * (run_end - run_start) if run_start > 0 
            else vocal_sections[-1][1]
        )
        
        # Interpolate
        run_len = run_end - run_start
        for j in range(run_len):
            frac = (j + 1) / (run_len + 1)
            word_timestamps[run_start + j] = prev_time + frac * (next_time - prev_time)


def _assign_words_proportional(all_words: list, vocal_sections: list) -> list:
    """Fallback: distribute words proportionally by segment duration."""
    total_words = len(all_words)
    total_duration = sum(e - s for s, e in vocal_sections)
    
    segment_word_counts = []
    for ss, se in vocal_sections:
        proportion = (se - ss) / total_duration
        segment_word_counts.append(round(proportion * total_words))
    
    # Ensure total matches
    assigned = sum(segment_word_counts)
    if assigned != total_words:
        for si in range(len(segment_word_counts) - 1, -1, -1):
            if segment_word_counts[si] > 0:
                segment_word_counts[si] += (total_words - assigned)
                break
    
    for si in range(len(segment_word_counts)):
        segment_word_counts[si] = max(0, segment_word_counts[si])
    
    log_step("ALIGN", "Word assignment (proportional by duration)")
    
    assignments = []
    word_idx = 0
    for si, (ss, se) in enumerate(vocal_sections):
        n_words = segment_word_counts[si]
        if n_words <= 0:
            continue
        n_words = min(n_words, total_words - word_idx)
        if n_words <= 0:
            continue
        seg_words = all_words[word_idx:word_idx + n_words]
        word_idx += n_words
        assignments.append((seg_words, ss, se))
    
    if word_idx < total_words and assignments:
        last_words, last_ss, last_se = assignments[-1]
        assignments[-1] = (last_words + all_words[word_idx:], last_ss, last_se)
    
    return assignments


def _parse_textgrid_words(textgrid_path: str) -> List[dict]:
    """Parse word intervals from TextGrid file."""
    with open(textgrid_path, 'r') as f:
        content = f.read()
    
    # Find the "words" tier
    # TextGrid has two tiers: "words" and "phones"
    # We want the words tier
    tiers = content.split('item [')
    word_tier = None
    for tier in tiers:
        if '"words"' in tier:
            word_tier = tier
            break
    
    if not word_tier:
        # Fall back to first tier
        word_tier = content
    
    pattern = r'xmin = ([\d.]+)\s+xmax = ([\d.]+)\s+text = "([^"]*)"'
    matches = re.findall(pattern, word_tier)
    
    intervals = []
    for xmin, xmax, text in matches:
        text = text.strip()
        if text:  # Skip empty intervals (silence)
            intervals.append({
                "start": float(xmin),
                "end": float(xmax),
                "text": text.lower()
            })
    
    return intervals


def _parse_textgrid_phones(textgrid_path: str) -> List[dict]:
    """Parse phone intervals from TextGrid file."""
    with open(textgrid_path, 'r') as f:
        content = f.read()
    
    tiers = content.split('item [')
    phone_tier = None
    for tier in tiers:
        if '"phones"' in tier:
            phone_tier = tier
            break
    
    if not phone_tier:
        return []
    
    pattern = r'xmin = ([\d.]+)\s+xmax = ([\d.]+)\s+text = "([^"]*)"'
    matches = re.findall(pattern, phone_tier)
    
    intervals = []
    for xmin, xmax, text in matches:
        text = text.strip()
        if text:
            intervals.append({
                "start": float(xmin),
                "end": float(xmax),
                "text": text
            })
    
    return intervals


def _dump_phone_debug(phones: List[dict], word_intervals: List[dict] = None,
                      time_range: tuple = None):
    """Dump phone-level data to debug file for inspecting MFA's raw output.
    
    Shows ALL phones (including silence gaps) so we can see what MFA
    actually recognises vs what it outputs at word level.
    """
    debug_path = os.path.join(os.path.dirname(__file__), '..', 'downloads', 'phone_debug.txt')
    try:
        t_lo, t_hi = time_range or (0, 9999)
        with open(debug_path, 'w') as f:
            f.write(f"PHONE-LEVEL DEBUG ({t_lo}s - {t_hi}s)\n{'='*80}\n\n")
            
            # Word intervals in range
            if word_intervals:
                f.write("WORD INTERVALS (raw from MFA):\n")
                for i, w in enumerate(word_intervals):
                    if w["end"] >= t_lo and w["start"] <= t_hi:
                        dur = w["end"] - w["start"]
                        # Show gap to next word
                        gap_str = ""
                        if i + 1 < len(word_intervals):
                            gap = word_intervals[i+1]["start"] - w["end"]
                            if gap > 0.01:
                                gap_str = f"  >> GAP {gap:.3f}s to next word"
                        f.write(f"  {w['start']:8.3f} - {w['end']:8.3f}  ({dur:5.3f}s)  {w['text']}{gap_str}\n")
                f.write("\n")
            
            # All phones in range — show gaps as [SILENCE]
            f.write("PHONES (with silence gaps shown):\n")
            prev_end = None
            for p in phones:
                if p["end"] < t_lo:
                    prev_end = p["end"]
                    continue
                if p["start"] > t_hi:
                    break
                # Show gap before this phone
                if prev_end is not None:
                    gap = p["start"] - prev_end
                    if gap > 0.005:
                        f.write(f"  {prev_end:8.3f} - {p['start']:8.3f}  ({gap:5.3f}s)  [SILENCE]\n")
                f.write(f"  {p['start']:8.3f} - {p['end']:8.3f}  ({p['end']-p['start']:5.3f}s)  {p['text']}\n")
                prev_end = p["end"]
            
        log_step("ALIGN", f"Phone debug written to {debug_path}")
    except Exception as e:
        log_step("ALIGN", f"Could not write phone debug: {e}")


def _merge_mfa_contractions(word_intervals: List[dict]) -> List[dict]:
    """Merge MFA-split contractions back into single words.

    MFA sometimes splits contractions like "there's" into "there" + "'s",
    creating a word count mismatch with our lyrics. Merge them back.
    """
    if not word_intervals:
        return word_intervals

    merged = []
    i = 0
    while i < len(word_intervals):
        w = dict(word_intervals[i])
        # Check if next word is a contraction suffix
        if i + 1 < len(word_intervals):
            next_w = word_intervals[i + 1]
            next_text = next_w["text"]
            # Detect contraction suffixes: 's, 're, 'll, 've, 't, 'm, 'd
            if next_text.startswith("\u2019") or next_text.startswith("'"):
                # Merge: extend current word to cover both
                w["end"] = next_w["end"]
                w["text"] = w["text"] + next_w["text"]
                log_step("ALIGN", f"  Merged MFA contraction: "
                         f"'{word_intervals[i]['text']}' + '{next_text}' -> '{w['text']}'")
                i += 2
                merged.append(w)
                continue
        merged.append(w)
        i += 1

    if len(merged) != len(word_intervals):
        log_step("ALIGN", f"Merged {len(word_intervals) - len(merged)} MFA "
                 f"contractions ({len(word_intervals)} -> {len(merged)} words)")

    return merged


def _trim_word_intervals_with_phones(
    word_intervals: List[dict],
    phones: List[dict]
) -> List[dict]:
    """Trim word end times using phone data.
    
    MFA word intervals span from start of word to start of next word,
    including silence gaps. This function uses phone-level data to find
    the actual pronunciation end (last phone) within each word, so that
    notes don't include trailing silence.
    
    We allow a small grace period (0.15s) after the last phone to avoid
    cutting off word endings too aggressively.
    """
    if not phones:
        return word_intervals
    
    GRACE = 0.15  # seconds to add after last phone
    
    trimmed = []
    for word in word_intervals:
        ws = word["start"]
        we = word["end"]
        
        # Find all phones within this word's time range
        word_phones = [
            p for p in phones
            if p["start"] >= ws - 0.01 and p["end"] <= we + 0.01
        ]
        
        if word_phones:
            last_phone_end = max(p["end"] for p in word_phones)
            # Trim the word's end to last phone end + grace, but don't extend
            trimmed_end = min(we, last_phone_end + GRACE)
            # Don't trim below minimum duration (at least the phones)
            trimmed_end = max(trimmed_end, last_phone_end)
            trimmed.append({
                **word,
                "end": trimmed_end,
                "original_end": we
            })
        else:
            # No phones found for this word — keep as-is
            trimmed.append(word)
    
    # Log trimming stats
    total_trimmed_sec = sum(
        (w.get("original_end", w["end"]) - w["end"]) for w in trimmed
    )
    big_trims = [
        w for w in trimmed
        if (w.get("original_end", w["end"]) - w["end"]) > 0.5
    ]
    if big_trims:
        log_step("ALIGN", f"Phone-trimmed {len(big_trims)} bloated words (total {total_trimmed_sec:.1f}s removed)")
        for w in big_trims[:5]:
            trim_amt = w["original_end"] - w["end"]
            log_step("ALIGN", f"  '{w['text']}': {w['original_end'] - w['end'] + w['end'] - w['start']:.2f}s -> {w['end'] - w['start']:.2f}s (trimmed {trim_amt:.2f}s)")
    
    return trimmed


# Vowel phones in the English MFA phone set (ARPAbet-based)
_VOWEL_PHONES = {
    'aa', 'ae', 'ah', 'ao', 'aw', 'ay', 'eh', 'er', 'ey',
    'ih', 'iy', 'ow', 'oy', 'uh', 'uw',
    # Also match numbered variants (aa0, aa1, aa2, etc.)
}


def _is_vowel_phone(phone_text: str) -> bool:
    """Check if a phone is a vowel (syllable nucleus)."""
    base = phone_text.lower().rstrip('012')
    return base in _VOWEL_PHONES


def _get_syllable_boundaries_from_phones(
    word_start: float,
    word_end: float,
    num_syllables: int,
    phones: List[dict]
) -> list:
    """Use phone data to find syllable boundaries within a word.
    
    Strategy: Find vowel nuclei in the phone sequence. Each vowel marks
    a syllable onset. Split the phone sequence at consonant clusters
    between vowels, assigning onset consonants to the following syllable.
    
    Returns list of (start, end) tuples, one per syllable, or None if
    phone data is insufficient.
    """
    if not phones or num_syllables <= 1:
        return None
    
    # Get phones within this word's time range
    word_phones = [
        p for p in phones
        if p["start"] >= word_start - 0.01 and p["end"] <= word_end + 0.01
        and p["text"].strip()  # skip empty/silence
    ]
    
    if not word_phones:
        return None
    
    # Find vowel positions (syllable nuclei)
    vowel_indices = [
        i for i, p in enumerate(word_phones)
        if _is_vowel_phone(p["text"])
    ]
    
    if len(vowel_indices) < num_syllables:
        # Not enough vowels found — fall back to equal distribution
        return None
    
    # If more vowels than syllables (diphthongs counted as separate?),
    # take the first num_syllables vowels
    if len(vowel_indices) > num_syllables:
        vowel_indices = vowel_indices[:num_syllables]
    
    # Build syllable boundaries:
    # Each syllable starts at the consonant(s) before its vowel
    # (or at the word start for the first syllable)
    boundaries = []
    
    for s in range(num_syllables):
        vowel_idx = vowel_indices[s]
        
        if s == 0:
            # First syllable starts at word start
            syl_start = word_phones[0]["start"]
        else:
            # Find the split point between previous vowel and this vowel
            prev_vowel_idx = vowel_indices[s - 1]
            # Split at the midpoint of consonants between the two vowels
            consonant_start_idx = prev_vowel_idx + 1
            if consonant_start_idx <= vowel_idx:
                # Split: give first consonant to previous syllable, rest to this
                # (onset maximization principle)
                split_idx = consonant_start_idx
                if vowel_idx - consonant_start_idx >= 2:
                    # Multiple consonants: split in the middle, giving more to onset
                    split_idx = vowel_idx - 1
                syl_start = word_phones[split_idx]["start"]
            else:
                # Adjacent vowels (hiatus)
                syl_start = word_phones[vowel_idx]["start"]
        
        if s < num_syllables - 1:
            # End at the start of next syllable (will be computed in next iteration)
            # For now, set to next vowel's consonant onset
            next_vowel_idx = vowel_indices[s + 1]
            consonant_start_idx = vowel_idx + 1
            if consonant_start_idx <= next_vowel_idx:
                split_idx = consonant_start_idx
                if next_vowel_idx - consonant_start_idx >= 2:
                    split_idx = next_vowel_idx - 1
                syl_end = word_phones[split_idx]["start"]
            else:
                syl_end = word_phones[next_vowel_idx]["start"]
        else:
            # Last syllable ends at word end
            syl_end = word_phones[-1]["end"]
        
        boundaries.append((syl_start, max(syl_start + 0.01, syl_end)))
    
    return boundaries


def _map_mfa_words_to_syllables(
    word_intervals: List[dict],
    flat_syllables: list,
    lyrics_text: str,
    phones: List[dict] = None,
    seg_word_counts: List[tuple] = None
) -> List[dict]:
    """Map MFA word-level timestamps back to syllable-level timestamps.
    
    Strategy:
    1. Match each MFA word to the corresponding word in our lyrics
    2. Use phone data to trim inflated word durations (MFA word intervals
       span until the next word, including silence gaps)
    3. For multi-syllable words, distribute the word's time proportionally
    4. Track line_index for break line generation
    5. If seg_word_counts is provided, resync at segment boundaries so that
       a word-count mismatch in one segment doesn't cascade to all later ones
    """
    # ── Pre-process: trim word intervals using phone data ──
    # MFA word intervals span from word start to next word start, including
    # silence gaps. Use phone data to find actual pronunciation end.
    if phones:
        word_intervals = _trim_word_intervals_with_phones(word_intervals, phones)
    # Build a list of (word, syllable_count, syllable_indices) from flat_syllables
    word_groups = []  # [{word, syllables: [idx, ...], line_index}]
    current_word_syls = []
    current_line_idx = 0
    
    for i, syl in enumerate(flat_syllables):
        line_idx = syl.get("line_index", current_line_idx)
        if syl.get("is_word_start", True) and current_word_syls:
            # Close previous word group
            word_groups.append({
                "syllable_indices": current_word_syls[:],
                "line_index": current_line_idx
            })
            current_word_syls = []
        current_word_syls.append(i)
        current_line_idx = line_idx
    
    if current_word_syls:
        word_groups.append({
            "syllable_indices": current_word_syls[:],
            "line_index": current_line_idx
        })
    
    log_step("ALIGN", f"Word groups from lyrics: {len(word_groups)}, MFA words: {len(word_intervals)}")
    
    # ── Build segment boundary map for resyncing ──
    # seg_word_counts = [(n_expected_lyrics_words, n_actual_mfa_words), ...]
    # This lets us resync at segment boundaries so a mismatch in one segment
    # (e.g., MFA splitting a contraction) doesn't cascade to all later segments.
    if seg_word_counts:
        total_expected = sum(n_exp for n_exp, _ in seg_word_counts)
        total_mfa = sum(n_mfa for _, n_mfa in seg_word_counts)
        if total_expected != len(word_groups):
            log_step("ALIGN", f"Warning: seg_word_counts total ({total_expected}) != "
                     f"word_groups ({len(word_groups)}), falling back to sequential matching")
            seg_word_counts = None
        elif total_mfa != len(word_intervals):
            log_step("ALIGN", f"Warning: seg_word_counts MFA total ({total_mfa}) != "
                     f"word_intervals ({len(word_intervals)}), falling back to sequential matching")
            seg_word_counts = None
        else:
            mismatched = [(i, ne, nm) for i, (ne, nm) in enumerate(seg_word_counts) if ne != nm]
            if mismatched:
                for seg_i, ne, nm in mismatched:
                    log_step("ALIGN", f"  Seg {seg_i}: expected {ne} words, MFA produced {nm} "
                             f"(diff {nm - ne:+d}) — will resync at boundary")
    
    # ── Match word groups to MFA intervals ──
    results = [None] * len(flat_syllables)
    
    def _assign_word(wg, mfa_word, confidence, method):
        """Assign MFA timing to a word group's syllables."""
        syl_indices = wg["syllable_indices"]
        line_idx = wg["line_index"]
        num_syls = len(syl_indices)
        word_start = mfa_word["start"]
        word_end = mfa_word["end"]
        word_duration = word_end - word_start
        
        # Distribute time across syllables using phone data when possible
        syl_boundaries = _get_syllable_boundaries_from_phones(
            word_start, word_end, num_syls, phones
        ) if phones and num_syls > 1 else None
        
        if syl_boundaries and len(syl_boundaries) == num_syls:
            for j, syl_idx in enumerate(syl_indices):
                syl = flat_syllables[syl_idx]
                start, end = syl_boundaries[j]
                results[syl_idx] = {
                    "syllable": syl["text"],
                    "start": round(start, 4),
                    "end": round(end, 4),
                    "confidence": confidence,
                    "is_rap": syl.get("is_rap", False),
                    "method": method,
                    "line_index": line_idx
                }
        else:
            syl_duration = word_duration / num_syls if num_syls > 0 else 0
            for j, syl_idx in enumerate(syl_indices):
                syl = flat_syllables[syl_idx]
                start = word_start + j * syl_duration
                end = word_start + (j + 1) * syl_duration
                results[syl_idx] = {
                    "syllable": syl["text"],
                    "start": round(start, 4),
                    "end": round(end, 4),
                    "confidence": confidence,
                    "is_rap": syl.get("is_rap", False),
                    "method": method,
                    "line_index": line_idx
                }
    
    def _extrapolate_word(wg):
        """Assign extrapolated timing when no MFA word is available."""
        syl_indices = wg["syllable_indices"]
        line_idx = wg["line_index"]
        num_syls = len(syl_indices)
        
        last_known = [r for r in results if r is not None]
        word_start = last_known[-1]["end"] + 0.05 if last_known else 0.0
        word_duration = 0.3 * num_syls
        syl_duration = 0.3
        
        for j, syl_idx in enumerate(syl_indices):
            syl = flat_syllables[syl_idx]
            results[syl_idx] = {
                "syllable": syl["text"],
                "start": round(word_start + j * syl_duration, 4),
                "end": round(word_start + (j + 1) * syl_duration, 4),
                "confidence": 0.3,
                "is_rap": syl.get("is_rap", False),
                "method": "mfa_extrapolated",
                "line_index": line_idx
            }
    
    if seg_word_counts:
        # ── Segment-aware matching: resync at each segment boundary ──
        mfa_idx = 0
        wg_idx = 0
        
        for seg_i, (n_expected, n_mfa) in enumerate(seg_word_counts):
            seg_mfa_start = mfa_idx
            
            for word_i in range(n_expected):
                if wg_idx >= len(word_groups):
                    break
                wg = word_groups[wg_idx]
                
                if mfa_idx < seg_mfa_start + n_mfa:
                    _assign_word(wg, word_intervals[mfa_idx], 0.9, "mfa")
                    mfa_idx += 1
                else:
                    # Ran out of MFA words in this segment — extrapolate
                    _extrapolate_word(wg)
                
                wg_idx += 1
            
            # Skip any remaining MFA words in this segment (handles extra words)
            mfa_idx = seg_mfa_start + n_mfa
    else:
        # ── Fallback: sequential matching (original behavior) ──
        mfa_idx = 0
        for wg in word_groups:
            if mfa_idx < len(word_intervals):
                _assign_word(wg, word_intervals[mfa_idx], 0.9, "mfa")
                mfa_idx += 1
            else:
                _extrapolate_word(wg)
    
    # Fill any gaps (shouldn't happen, but safety net)
    filled = [r for r in results if r is not None]
    if len(filled) < len(results):
        log_step("ALIGN", f"Warning: {len(results) - len(filled)} syllables unmapped, filling gaps")
        for i in range(len(results)):
            if results[i] is None:
                # Interpolate from neighbors
                prev = results[i-1] if i > 0 and results[i-1] else {"end": 0.0}
                results[i] = {
                    "syllable": flat_syllables[i]["text"],
                    "start": prev["end"],
                    "end": prev["end"] + 0.2,
                    "confidence": 0.2,
                    "is_rap": flat_syllables[i].get("is_rap", False),
                    "method": "interpolated",
                    "line_index": flat_syllables[i].get("line_index", 0)
                }
    
    log_step("ALIGN", f"Mapped {len(results)} syllables from MFA alignment")
    if results:
        log_step("ALIGN", f"  Time range: {results[0]['start']:.2f}s - {results[-1]['end']:.2f}s")
    
    # ── Post-process: remove phantom words that MFA couldn't place ──
    results = _remove_phantom_words(results)
    
    # ── Post-process: fix bloated syllables ──
    results = _fix_bloated_syllables(results)
    
    # ── Post-process: fix boundary-crammed words ──
    results = _fix_boundary_crammed_words(results)
    
    return results


def _remove_phantom_words(results: List[dict]) -> List[dict]:
    """Remove syllables that MFA couldn't place in the audio (phantom words).
    
    When the lyrics contain a word that isn't actually sung, MFA is forced to
    fit it somewhere. It typically squeezes it into a tiny duration (< 0.08s).
    These phantom syllables cause all subsequent notes to shift off by one
    position in the comparison.
    
    Detection criteria (ALL must be true):
    1. Duration < 0.08s (MFA couldn't give it real time)
    2. Standalone single-syllable word (starts with space, next starts with space)
    3. Common function word (article/preposition: the, a, an, of, in, on, to, etc.)
    4. BOTH neighbors have substantial duration (> 0.3s each)
       This is the key filter: a phantom word is isolated between normal-length
       syllables. In fast passages, neighbors are also short, so real words
       won't be caught.
    """
    if not results or len(results) < 5:
        return results
    
    PHANTOM_THRESHOLD = 0.08
    NEIGHBOR_MIN = 0.5  # Both neighbors must be > this to confirm phantom
    # Common function words that singers sometimes skip
    FUNCTION_WORDS = {"the", "a", "an", "of", "in", "on", "to", "at", "by", "is",
                      "it", "or", "as", "if", "so", "up", "no", "do", "my", "we",
                      "he", "me", "be"}
    
    phantoms = []
    for i, r in enumerate(results):
        dur = r["end"] - r["start"]
        if dur >= PHANTOM_THRESHOLD:
            continue
        
        text = r["syllable"].strip().lower().rstrip(",.'!?;:")
        
        # Must be a known function word
        if text not in FUNCTION_WORDS:
            continue
        
        # Must be a standalone word (starts with space, and next syllable starts a new word)
        if not r["syllable"].startswith(" ") and i > 0:
            continue
        
        is_standalone = False
        if i + 1 < len(results):
            next_text = results[i + 1]["syllable"]
            if next_text.startswith(" ") or (len(next_text) > 0 and next_text[0].isupper()):
                is_standalone = True
        elif i == len(results) - 1:
            is_standalone = True
        
        if not is_standalone:
            continue
        
        # KEY CHECK: Both neighbors must have substantial duration.
        # A phantom word is squeezed between two normal syllables.
        # In fast passages, neighbors are also short — so real words pass through.
        prev_dur = (results[i-1]["end"] - results[i-1]["start"]) if i > 0 else 1.0
        next_dur = (results[i+1]["end"] - results[i+1]["start"]) if i + 1 < len(results) else 1.0
        
        if prev_dur < NEIGHBOR_MIN or next_dur < NEIGHBOR_MIN:
            continue
        
        phantoms.append(i)
        log_step("ALIGN", f"  Phantom word detected: '{text}' at {r['start']:.2f}s "
                 f"(dur={dur:.3f}s, prev={prev_dur:.3f}s, next={next_dur:.3f}s)")
    
    if phantoms:
        log_step("ALIGN", f"Removing {len(phantoms)} phantom word(s) not found in audio")
        for idx in reversed(phantoms):
            results.pop(idx)
    
    return results


def _fix_bloated_syllables(results: List[dict]) -> List[dict]:
    """Fix syllables with inflated durations from MFA.
    
    MFA sometimes assigns silence/held-note gaps to the preceding word,
    making it much longer than it should be. After trimming bloated syllables,
    we also check for large gaps between the trimmed syllable and the next one,
    and shift subsequent syllables earlier to close those gaps.
    
    Strategy:
    1. Compute median syllable duration
    2. Find syllables > threshold duration, cap them
    3. After capping, check gap to next syllable — if too large, shift
       subsequent syllables earlier to close the gap
    """
    import numpy as np
    
    if not results or len(results) < 10:
        return results
    
    durations = [r["end"] - r["start"] for r in results]
    median_dur = float(np.median(durations))
    
    # Threshold: syllable is "bloated" if > max(4x median, 1.5s)
    bloat_threshold = max(median_dur * 4, 1.5)
    
    fixes_applied = 0
    
    for i in range(len(results)):
        dur = results[i]["end"] - results[i]["start"]
        
        if dur <= bloat_threshold:
            continue
        
        # Also check if there's a gap after this syllable to the next line
        # (phrase boundary detection)
        has_line_break = False
        if i + 1 < len(results):
            has_line_break = results[i].get("line_index", 0) != results[i + 1].get("line_index", 0)
        
        # Determine reasonable max duration for this syllable
        # Short words (1-4 chars): max ~0.8s
        # Longer words: max ~1.2s  
        # At phrase end (line break following): slightly more generous
        syl_text = results[i]["syllable"].strip().rstrip(",.'!?")
        if len(syl_text) <= 4:
            max_dur = 1.0 if has_line_break else 0.8
        else:
            max_dur = 1.5 if has_line_break else 1.2
        
        if dur <= max_dur:
            continue
        
        # Cap this syllable's duration
        new_end = results[i]["start"] + max_dur
        old_end = results[i]["end"]
        results[i]["end"] = round(new_end, 4)
        
        # Check if there's a large gap to the next syllable
        # If so, shift subsequent syllables earlier — but ONLY to close
        # the gap that was *created* by capping, not pre-existing gaps.
        # A pre-existing gap > 1.0s indicates a real musical pause between
        # phrases that MFA correctly identified; closing it destroys timing.
        if i + 1 < len(results):
            gap_after_cap = results[i + 1]["start"] - new_end
            gap_before_cap = results[i + 1]["start"] - old_end
            
            # Only shift if the original gap was small (not a real phrase break)
            max_gap = 0.3
            REAL_PAUSE_THRESHOLD = 1.0  # seconds — gap larger than this is a real pause
            
            if gap_after_cap > max_gap and gap_before_cap < REAL_PAUSE_THRESHOLD:
                # Preserve at least the original gap (or max_gap, whichever is larger)
                target_gap = max(max_gap, gap_before_cap)
                shift = gap_after_cap - target_gap
                
                if shift > 0:
                    # Find how many subsequent syllables to shift:
                    # Shift until we hit a natural gap (> 1s) in the original data,
                    # which indicates a real phrase break
                    shift_end = i + 1
                    for j in range(i + 1, len(results)):
                        shift_end = j + 1
                        # Stop shifting at phrase/line boundaries
                        if j + 1 < len(results):
                            inter_gap = results[j + 1]["start"] - results[j]["end"]
                            if inter_gap > 1.0:
                                break
                    
                    # Apply the shift
                    for j in range(i + 1, shift_end):
                        results[j]["start"] = round(results[j]["start"] - shift, 4)
                        results[j]["end"] = round(results[j]["end"] - shift, 4)
                    
                    log_step("ALIGN", f"  Shifted {shift_end - i - 1} syllables earlier by {shift:.2f}s after '{results[i]['syllable'].strip()}'")
            elif gap_after_cap > max_gap:
                log_step("ALIGN", f"  Preserved real pause ({gap_before_cap:.1f}s) after '{results[i]['syllable'].strip()}'")
        
        fixes_applied += 1
        log_step("ALIGN", f"  Trimmed '{results[i]['syllable'].strip()}' from {dur:.2f}s to {max_dur:.1f}s")
    
    if fixes_applied:
        log_step("ALIGN", f"Fixed {fixes_applied} bloated syllables (threshold: {bloat_threshold:.2f}s, median: {median_dur:.3f}s)")
    
    return results


def _fix_boundary_crammed_words(results: List[dict]) -> List[dict]:
    """Fix words crammed at segment boundaries with tiny durations.
    
    When MFA can't match a word within a segment (e.g., Whisper heard a
    different word), it squeezes the word into a tiny sliver at the segment
    boundary. These show up as:
    - Very short duration (< 0.15s)
    - Followed by a large gap (> 1.5s) to the next word
    
    Fix: Move the crammed word to just before the next word, giving it a
    reasonable duration based on its text length.
    """
    if not results or len(results) < 3:
        return results
    
    CRAMMED_MAX_DUR = 0.15   # seconds — word is "crammed" if shorter
    GAP_THRESHOLD = 1.5      # seconds — gap must be this large after crammed word
    
    fixes = 0
    for i in range(len(results) - 1):
        dur = results[i]["end"] - results[i]["start"]
        gap = results[i + 1]["start"] - results[i]["end"]
        
        if dur >= CRAMMED_MAX_DUR or gap < GAP_THRESHOLD:
            continue
        
        # This word has tiny duration followed by large gap → likely crammed
        # Move it to just before the next word
        text = results[i]["syllable"].strip()
        estimated_dur = max(0.3, min(0.6, len(text) * 0.1))
        
        new_start = results[i + 1]["start"] - estimated_dur - 0.05
        new_end = new_start + estimated_dur
        
        # Sanity: don't move before previous word's end
        if i > 0 and new_start < results[i - 1]["end"]:
            continue
        
        old_start = results[i]["start"]
        results[i]["start"] = round(new_start, 4)
        results[i]["end"] = round(new_end, 4)
        fixes += 1
        log_step("ALIGN", f"  Moved crammed '{text}' from {old_start:.2f}s to {new_start:.2f}s "
                 f"(was {dur:.3f}s, gap was {gap:.1f}s)")
    
    if fixes:
        log_step("ALIGN", f"Fixed {fixes} boundary-crammed word(s)")
    
    return results


def _close_intra_line_gaps(results: List[dict], phones: List[dict] = None) -> List[dict]:
    """Close false silence gaps between consecutive syllables.
    
    MFA systematically inserts silence gaps between words, even when the
    singer sings them continuously. It also inflates vowels in held notes
    (e.g., "sky" stretched to 4 seconds), and after bloated-syllable trimming
    large artificial gaps remain. These accumulate and shift everything right.
    
    Strategy:
    1. Small gaps (< SMALL_GAP): Always close — these are MFA padding artifacts
    2. Medium gaps (< LARGE_GAP): Close unless they're between different lyric
       sections (detected by large original gap > SECTION_BREAK in raw data)
    3. Large gaps (>= LARGE_GAP): Keep — these indicate real musical breaks
       (instrumental interludes, etc.)
    
    The key insight: after _fix_bloated_syllables has already capped inflated
    words, any remaining gap that doesn't correspond to a genuine long silence
    in the song should be closed. We detect genuine silence by checking the
    raw (pre-processed) gap between this syllable's MFA end and the next
    syllable's MFA start — if MFA originally placed them far apart AND
    there's real silence in the audio, keep the gap.
    """
    if not results:
        return results
    
    SMALL_GAP = 0.8     # always close gaps smaller than this
    LARGE_GAP = 2.0     # never close gaps larger than this (real breaks)
    REAL_SILENCE_MIN = 1.0  # min silence in phone data to consider gap "real"
    
    fixed = [dict(r) for r in results]  # shallow copy
    
    total_closed = 0.0
    gap_count = 0
    kept_count = 0
    
    for i in range(1, len(fixed)):
        gap = fixed[i]["start"] - fixed[i - 1]["end"]
        
        if gap <= 0.01:  # no meaningful gap
            continue
        
        if gap >= LARGE_GAP:
            # Large gap — check phone data to see if it's real silence
            real_silence = _has_real_silence(
                phones, fixed[i - 1]["end"], fixed[i]["start"], REAL_SILENCE_MIN
            ) if phones else True
            if real_silence:
                kept_count += 1
                continue
        
        if gap < SMALL_GAP:
            # Small gap — always an artifact, close it
            pass  # fall through to closing
        else:
            # Medium gap — check phone data
            real_silence = _has_real_silence(
                phones, fixed[i - 1]["end"], fixed[i]["start"], REAL_SILENCE_MIN
            ) if phones else False
            if real_silence:
                kept_count += 1
                continue
        
        # Close this gap
        duration = fixed[i]["end"] - fixed[i]["start"]
        fixed[i]["start"] = round(fixed[i - 1]["end"], 4)
        fixed[i]["end"] = round(fixed[i]["start"] + duration, 4)
        total_closed += gap
        gap_count += 1
    
    if gap_count > 0 or kept_count > 0:
        log_step("ALIGN", f"Gap analysis: closed {gap_count} false gaps "
                 f"({total_closed:.2f}s drift removed), "
                 f"kept {kept_count} real silence gaps")
    
    return fixed


def _has_real_silence(phones: List[dict], gap_start: float, gap_end: float,
                      threshold: float) -> bool:
    """Check if a time region contains real silence based on phone data.
    
    Looks at the phone tier to see if there are continuous phones spanning
    the gap region (= no real silence, just MFA padding) or if there's a
    genuine gap with no phones (= real silence).
    
    Returns True if the gap contains silence >= threshold seconds.
    """
    if not phones:
        return True  # no phone data — assume gap is real (conservative)
    
    # Find the maximum continuous silence within [gap_start, gap_end]
    # by looking at gaps between consecutive phones in that region
    
    # Get all phones that overlap or are within the gap region
    # (with a small buffer to catch edge phones)
    buffer = 0.05
    relevant = []
    for p in phones:
        if p["end"] < gap_start - buffer:
            continue
        if p["start"] > gap_end + buffer:
            break
        relevant.append(p)
    
    if not relevant:
        # No phones at all in this region — it's real silence
        return (gap_end - gap_start) >= threshold
    
    # Check the coverage: find the longest gap between phones in this region
    # First, check silence from gap_start to first phone
    max_silence = max(0, relevant[0]["start"] - gap_start)
    
    # Check gaps between consecutive phones
    for j in range(1, len(relevant)):
        silence = relevant[j]["start"] - relevant[j - 1]["end"]
        if silence > max_silence:
            max_silence = silence
    
    # Check silence from last phone to gap_end
    trailing = gap_end - relevant[-1]["end"]
    if trailing > max_silence:
        max_silence = trailing
    
    return max_silence >= threshold


def _anchor_to_whisper(results: List[dict], whisper_words: list) -> List[dict]:
    """Correct MFA timing drift using Whisper word timestamps as anchors.
    
    MFA is a forced aligner — precise at phone level but prone to systematic
    drift when singing includes held notes, vibrato, or continuous phrasing.
    Whisper is an ASR model — it independently detects word onsets from audio,
    which are less precise but free of cumulative drift.
    
    Strategy:
    1. Build a list of "word start" events from the MFA results (first syllable
       of each word, identified by leading space in syllable text).
    2. Match them to Whisper words using fuzzy text matching.
    3. For each matched pair, compute the drift = MFA_start - Whisper_start.
    4. Apply drift correction: shift groups of syllables so their word starts
       align with Whisper's timing. Interpolate between anchor points to
       avoid discontinuities.
    """
    if not whisper_words or not results:
        return results
    
    # ── Step 1: Extract word-start positions from MFA results ──
    mfa_words = []  # [{idx, word, start, end}]
    current_word = ""
    word_start_idx = 0
    prev_line_idx = -1
    
    for i, r in enumerate(results):
        syl = r["syllable"]
        line_idx = r.get("line_index", 0)
        # Detect word start: leading space, first syllable, OR first word of new line
        is_word_start = (syl.startswith(" ") or i == 0 or line_idx != prev_line_idx)
        prev_line_idx = line_idx
        
        if is_word_start:
            # Start of a new word
            if current_word and mfa_words:
                pass  # previous word already recorded
            clean = syl.strip().lower().rstrip(",.'!?;:")
            if not clean:
                continue
            # Find the full word by collecting subsequent syllables
            full_word = clean
            for j in range(i + 1, len(results)):
                next_syl = results[j]["syllable"]
                next_line = results[j].get("line_index", 0)
                if next_syl.startswith(" ") or next_line != line_idx:
                    break
                full_word += next_syl.strip().lower().rstrip(",.'!?;:")
            mfa_words.append({
                "idx": i,
                "word": full_word,
                "start": r["start"],
            })
    
    log_step("ALIGN", f"Whisper anchoring: {len(mfa_words)} MFA words, {len(whisper_words)} Whisper words")
    
    # ── Step 2: Match MFA words to Whisper words ──
    # Sequential matching with fuzzy text comparison
    anchors = []  # [{mfa_idx, mfa_start, whisper_start, word, drift}]
    w_idx = 0  # pointer into whisper_words
    
    for mw in mfa_words:
        mfa_word = mw["word"]
        best_match = None
        best_dist = 999
        
        # Search ahead in Whisper words (within a window)
        search_start = max(0, w_idx - 3)
        search_end = min(len(whisper_words), w_idx + 15)
        
        for wi in range(search_start, search_end):
            ww = whisper_words[wi]["word"].lower().strip().rstrip(",.'!?;:\"-")
            
            # Exact match
            if ww == mfa_word:
                best_match = wi
                best_dist = 0
                break
            
            # Prefix match (handles truncation)
            if len(mfa_word) >= 3 and (ww.startswith(mfa_word[:3]) or mfa_word.startswith(ww[:3])):
                dist = abs(len(ww) - len(mfa_word))
                if dist < best_dist:
                    best_match = wi
                    best_dist = dist
        
        if best_match is not None and best_dist <= 3:
            ww = whisper_words[best_match]
            drift = mw["start"] - ww["start"]
            anchors.append({
                "mfa_idx": mw["idx"],
                "mfa_start": mw["start"],
                "whisper_start": ww["start"],
                "word": mw["word"],
                "drift": drift,
            })
            w_idx = best_match + 1
    
    if not anchors:
        log_step("ALIGN", "Whisper anchoring: no matches found, skipping")
        return results
    
    # ── Step 2b: Filter outlier anchors ──
    # Anchors with drift wildly different from neighbors are false matches.
    # Use a sliding window to detect and remove outliers.
    import numpy as np
    raw_anchor_count = len(anchors)
    if len(anchors) >= 5:
        drifts_arr = np.array([a["drift"] for a in anchors])
        WINDOW = 5
        filtered = []
        for ai, a in enumerate(anchors):
            # Get local neighbors' drifts
            lo = max(0, ai - WINDOW)
            hi = min(len(anchors), ai + WINDOW + 1)
            neighbors = [anchors[j]["drift"] for j in range(lo, hi) if j != ai]
            if not neighbors:
                filtered.append(a)
                continue
            local_median = float(np.median(neighbors))
            deviation = abs(a["drift"] - local_median)
            # Keep if deviation is < 3s from local median
            if deviation < 3.0:
                filtered.append(a)
            else:
                log_step("ALIGN", f"  Filtered outlier anchor '{a['word']}' "
                         f"drift={a['drift']:+.2f}s (local median={local_median:+.2f}s, "
                         f"deviation={deviation:.2f}s)")
        anchors = filtered
        log_step("ALIGN", f"Anchors after outlier filter: {len(anchors)}/{raw_anchor_count}")
    
    if not anchors:
        log_step("ALIGN", "Whisper anchoring: all anchors filtered as outliers, skipping")
        return results
    
    # ── Step 3: Log anchor comparison ──
    debug_path = os.path.join(os.path.dirname(__file__), '..', 'downloads', 'whisper_anchor_debug.txt')
    try:
        with open(debug_path, 'w') as f:
            f.write(f"WHISPER ANCHOR COMPARISON ({len(anchors)}/{raw_anchor_count} anchors after filtering)\n{'='*70}\n\n")
            f.write(f"{'Word':<20} {'MFA':>8} {'Whisper':>8} {'Drift':>8} {'Action':>10}\n")
            f.write(f"{'-'*60}\n")
            for a in anchors:
                action = "OK" if abs(a["drift"]) < 0.5 else ("SHIFT" if abs(a["drift"]) < 3.0 else "BIG DRIFT")
                f.write(f"{a['word']:<20} {a['mfa_start']:8.2f} {a['whisper_start']:8.2f} {a['drift']:+8.2f}s  {action:>10}\n")
        log_step("ALIGN", f"Anchor debug written to {debug_path}")
    except Exception:
        pass
    
    # Log summary
    drifts = [a["drift"] for a in anchors]
    median_drift = float(np.median(drifts))
    max_drift = max(abs(d) for d in drifts)
    log_step("ALIGN", f"Whisper anchoring: {len(anchors)} anchors, "
             f"median drift={median_drift:+.2f}s, max={max_drift:.2f}s")
    
    # ── Step 4: Apply drift correction ──
    # Strategy: For each pair of consecutive anchors, apply uniform correction
    # based on the FIRST anchor's drift (piecewise constant). This avoids
    # interpolation artifacts when adjacent anchors have opposite drifts.
    
    fixed = [dict(r) for r in results]
    corrections = 0
    
    DRIFT_THRESHOLD = 0.2  # only correct if drift exceeds this
    MAX_CORRECTION = 5.0   # don't shift more than this (safety)
    
    if len(anchors) >= 1:
        # Build regions: each anchor controls syllables until the next anchor
        regions = []
        
        # Region before first anchor: use first anchor's drift
        regions.append((0, anchors[0]["mfa_idx"], anchors[0]["drift"]))
        
        # Regions between anchors: use the starting anchor's drift
        for ai in range(len(anchors)):
            start_idx = anchors[ai]["mfa_idx"]
            end_idx = anchors[ai + 1]["mfa_idx"] if ai + 1 < len(anchors) else len(fixed)
            drift = anchors[ai]["drift"]
            regions.append((start_idx, end_idx, drift))
        
        for region_start, region_end, drift in regions:
            if abs(drift) < DRIFT_THRESHOLD:
                continue
            
            correction = -drift
            if abs(correction) > MAX_CORRECTION:
                correction = MAX_CORRECTION if correction > 0 else -MAX_CORRECTION
            
            for j in range(region_start, min(region_end, len(fixed))):
                fixed[j]["start"] = round(fixed[j]["start"] + correction, 4)
                fixed[j]["end"] = round(fixed[j]["end"] + correction, 4)
                corrections += 1
    
    # ── Step 5: Enforce monotonic ordering ──
    # After correction, some syllables may be out of order.
    # Force each syllable to start no earlier than the previous one.
    ordering_fixes = 0
    for i in range(len(fixed)):
        fixed[i]["start"] = max(0, fixed[i]["start"])
        fixed[i]["end"] = max(fixed[i]["start"] + 0.01, fixed[i]["end"])
    
    for i in range(1, len(fixed)):
        prev_end = fixed[i - 1]["end"]
        if fixed[i]["start"] < prev_end - 0.01:
            # This syllable starts before previous ends — fix it
            dur = fixed[i]["end"] - fixed[i]["start"]
            fixed[i]["start"] = round(prev_end, 4)
            fixed[i]["end"] = round(fixed[i]["start"] + max(0.02, dur), 4)
            ordering_fixes += 1
    
    if corrections > 0:
        log_step("ALIGN", f"Whisper anchoring: corrected {corrections} syllables")
    if ordering_fixes > 0:
        log_step("ALIGN", f"Whisper anchoring: fixed {ordering_fixes} ordering violations")
    
    return fixed


def align_fallback(audio_path: str, parsed_lines: List[List[dict]]) -> List[dict]:
    """Fallback alignment using energy-based voiced segment detection.
    
    Instead of distributing syllables evenly across the full audio,
    this finds segments where there's vocal energy and places syllables
    within those segments — leaving gaps where the audio is silent.
    """
    import librosa
    import numpy as np
    
    y, sr = librosa.load(audio_path, sr=16000, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)
    
    log_step("ALIGN", f"Energy-based fallback alignment ({duration:.1f}s audio)")
    
    flat_syllables = [s for line in parsed_lines for s in line]
    total_syllables = len(flat_syllables)
    
    if total_syllables == 0:
        return []
    
    # ── Step 1: Use shared vocal section detection ──
    segments = _detect_vocal_sections(y, sr, min_silence_sec=1.5, min_section_sec=1.0)
    
    total_voiced_time = sum(end - start for start, end in segments)
    log_step("ALIGN", f"Found {len(segments)} voiced segments, total voiced time: {total_voiced_time:.1f}s")
    if segments:
        log_step("ALIGN", f"  First segment: {segments[0][0]:.2f}s - {segments[0][1]:.2f}s")
        log_step("ALIGN", f"  Last segment:  {segments[-1][0]:.2f}s - {segments[-1][1]:.2f}s")
    
    if len(segments) == 0:
        # Fallback to simple even distribution if no segments found
        log_step("ALIGN", "No voiced segments found, using simple distribution")
        return _align_even(y, sr, parsed_lines)
    
    # ── Step 3: Distribute lines across segments ──
    # Strategy: map each lyrics line to the nearest voiced segment(s)
    num_lines = len(parsed_lines)
    results = []
    
    if num_lines <= len(segments):
        # More segments than lines: assign each line to a segment proportionally
        # Distribute lines across segments weighted by segment duration
        seg_durations = [end - start for start, end in segments]
        total_seg_dur = sum(seg_durations)
        
        # Assign lines to segments proportionally to segment duration
        line_segment_map = []
        cumulative_lines = 0
        for seg_idx, seg_dur in enumerate(seg_durations):
            # How many lines belong to this segment?
            proportion = seg_dur / total_seg_dur
            n_lines = proportion * num_lines
            
            # At least assign fractional lines
            line_segment_map.append({
                'seg_idx': seg_idx,
                'start': segments[seg_idx][0],
                'end': segments[seg_idx][1],
                'line_count': n_lines,
            })
        
        # Now round-robin assign lines to segments
        line_assignments = []  # (line_idx, seg_start, seg_end)
        line_idx = 0
        
        # Sort by proportional assignment
        fractional_acc = 0.0
        for seg_info in line_segment_map:
            fractional_acc += seg_info['line_count']
            while line_idx < num_lines and line_idx < round(fractional_acc):
                line_assignments.append((line_idx, seg_info['start'], seg_info['end']))
                line_idx += 1
        
        # Assign remaining lines to last segment
        while line_idx < num_lines:
            last = segments[-1]
            line_assignments.append((line_idx, last[0], last[1]))
            line_idx += 1
    else:
        # More lines than segments: distribute multiple lines per segment
        lines_per_seg = num_lines / len(segments)
        line_assignments = []
        for line_idx in range(num_lines):
            seg_idx = min(int(line_idx / lines_per_seg), len(segments) - 1)
            line_assignments.append((line_idx, segments[seg_idx][0], segments[seg_idx][1]))
    
    # ── Step 4: Place syllables within assigned segments ──
    # Group assignments by segment
    from collections import defaultdict
    seg_lines = defaultdict(list)
    for line_idx, seg_start, seg_end in line_assignments:
        seg_lines[(seg_start, seg_end)].append(line_idx)
    
    for (seg_start, seg_end), line_indices in seg_lines.items():
        seg_duration = seg_end - seg_start
        
        # Count total syllables across all lines in this segment
        seg_syllables = []
        for li in line_indices:
            for syl in parsed_lines[li]:
                seg_syllables.append((li, syl))
        
        if not seg_syllables:
            continue
        
        # Leave 5% gap between lines within the segment
        num_line_breaks = len(set(li for li, _ in seg_syllables)) - 1
        gap_total = seg_duration * 0.05 * num_line_breaks / max(1, len(seg_syllables))
        syllable_time = (seg_duration - gap_total * num_line_breaks) / len(seg_syllables)
        syllable_time = max(0.1, syllable_time)  # minimum 100ms per syllable
        
        current_time = seg_start
        prev_line_idx = seg_syllables[0][0] if seg_syllables else None
        
        for li, syl in seg_syllables:
            # Add gap between lines
            if prev_line_idx is not None and li != prev_line_idx:
                current_time += gap_total
            
            start = current_time
            end = min(current_time + syllable_time, seg_end)
            
            results.append({
                "syllable": syl["text"],
                "start": start,
                "end": end,
                "confidence": 0.4,  # Slightly better than pure even
                "is_rap": syl.get("is_rap", False),
                "method": "fallback_energy",
                "line_index": li
            })
            
            current_time = end
            prev_line_idx = li
    
    # Sort by start time (segments may not be perfectly ordered)
    results.sort(key=lambda r: r["start"])
    
    log_step("ALIGN", f"Energy fallback: placed {len(results)} syllables in {len(segments)} voiced segments")
    log_step("ALIGN", f"  Time range: {results[0]['start']:.1f}s - {results[-1]['end']:.1f}s")
    
    return results


def _align_even(y, sr, parsed_lines):
    """Simple even distribution fallback (last resort)."""
    import librosa
    
    duration = librosa.get_duration(y=y, sr=sr)
    y_trimmed, trim_indices = librosa.effects.trim(y, top_db=20)
    trim_start = trim_indices[0] / sr
    trim_end = trim_indices[1] / sr
    active_duration = trim_end - trim_start
    
    flat_syllables = [s for line in parsed_lines for s in line]
    total_syllables = len(flat_syllables)
    
    if total_syllables == 0:
        return []
    
    num_lines = len(parsed_lines)
    line_gap_time = active_duration * 0.10 / max(1, num_lines - 1) if num_lines > 1 else 0
    syllable_time = active_duration * 0.90 / total_syllables
    
    results = []
    current_time = trim_start
    
    for line_idx, line in enumerate(parsed_lines):
        for syl in line:
            start = current_time
            end = current_time + syllable_time
            
            results.append({
                "syllable": syl["text"],
                "start": start,
                "end": end,
                "confidence": 0.3,
                "is_rap": syl.get("is_rap", False),
                "method": "fallback_even",
                "line_index": line_idx
            })
            
            current_time = end
        
        if line_idx < num_lines - 1:
            current_time += line_gap_time
    
    return results
