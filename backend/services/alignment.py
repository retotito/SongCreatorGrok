"""Forced alignment using Montreal Forced Aligner (MFA).

Aligns lyrics text to audio to get per-syllable timing.
Falls back to even distribution if MFA is not available.
"""

import os
import json
import tempfile
import subprocess
from typing import List, Tuple, Optional
from utils.logger import log_step, log_progress

# Check if MFA is available
try:
    result = subprocess.run(["mfa", "version"], capture_output=True, text=True, timeout=10)
    MFA_AVAILABLE = result.returncode == 0
    if MFA_AVAILABLE:
        log_step("INIT", f"MFA available: {result.stdout.strip()}")
except (FileNotFoundError, subprocess.TimeoutExpired):
    MFA_AVAILABLE = False
    log_step("INIT", "MFA not installed, will use fallback alignment")


def align_lyrics_to_audio(
    audio_path: str,
    lyrics_text: str,
    language: str = "english"
) -> List[dict]:
    """Align lyrics to audio, returning timing for each syllable.
    
    Args:
        audio_path: Path to vocal audio file
        lyrics_text: Full lyrics text with lines and hyphenated syllables
        language: Language for MFA model
        
    Returns:
        List of dicts: [{"syllable": "beau", "start": 1.23, "end": 1.56, "confidence": 0.95}, ...]
    """
    # Parse lyrics into syllables with line structure
    parsed = parse_lyrics(lyrics_text)
    flat_syllables = [s for line in parsed for s in line]
    
    log_step("ALIGN", f"Parsed {len(flat_syllables)} syllables across {len(parsed)} lines")
    
    if MFA_AVAILABLE:
        try:
            return align_with_mfa(audio_path, lyrics_text, flat_syllables, language)
        except Exception as e:
            log_step("ALIGN", f"MFA failed: {e}, using fallback")
    
    # Fallback: distribute syllables evenly across audio duration
    return align_fallback(audio_path, parsed)


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
                    "word": word
                })
        
        if syllables:
            lines.append(syllables)
    
    return lines


def align_with_mfa(
    audio_path: str,
    lyrics_text: str,
    flat_syllables: list,
    language: str
) -> List[dict]:
    """Use MFA to align lyrics to audio."""
    log_step("ALIGN", "Running Montreal Forced Aligner...")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # MFA needs a specific directory structure
        corpus_dir = os.path.join(temp_dir, "corpus")
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(corpus_dir)
        os.makedirs(output_dir)
        
        # Copy audio and create transcript
        import shutil
        audio_dest = os.path.join(corpus_dir, "song.wav")
        
        # Convert to WAV if needed
        if not audio_path.endswith('.wav'):
            import librosa
            import soundfile as sf
            y, sr = librosa.load(audio_path, sr=16000)
            sf.write(audio_dest, y, sr)
        else:
            shutil.copy2(audio_path, audio_dest)
        
        # Write transcript (MFA format: plain text, words separated by spaces)
        # For MFA we need words, not syllables
        words_text = lyrics_text.replace('-', '').replace('\n', ' ')
        words_text = ' '.join(words_text.split())  # Normalize whitespace
        
        transcript_path = os.path.join(corpus_dir, "song.txt")
        with open(transcript_path, 'w') as f:
            f.write(words_text)
        
        # Run MFA
        cmd = [
            "mfa", "align",
            corpus_dir,
            f"{language}_mfa",  # acoustic model
            f"{language}_mfa",  # dictionary
            output_dir,
            "--clean",
            "--single_speaker"
        ]
        
        log_step("ALIGN", f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            raise RuntimeError(f"MFA failed: {result.stderr}")
        
        # Parse TextGrid output
        textgrid_path = os.path.join(output_dir, "song.TextGrid")
        if not os.path.exists(textgrid_path):
            # Try alternate path
            for root, dirs, files in os.walk(output_dir):
                for f in files:
                    if f.endswith('.TextGrid'):
                        textgrid_path = os.path.join(root, f)
                        break
        
        return parse_textgrid_to_syllables(textgrid_path, flat_syllables)


def parse_textgrid_to_syllables(textgrid_path: str, flat_syllables: list) -> List[dict]:
    """Parse MFA TextGrid output and map back to syllables."""
    # Simple TextGrid parser
    import re
    
    word_intervals = []
    phone_intervals = []
    
    with open(textgrid_path, 'r') as f:
        content = f.read()
    
    # Extract word tier intervals
    # This is a simplified parser - production would use textgrid library
    word_pattern = r'xmin = ([\d.]+)\s+xmax = ([\d.]+)\s+text = "([^"]*)"'
    matches = re.findall(word_pattern, content)
    
    for xmin, xmax, text in matches:
        if text and text != "":
            word_intervals.append({
                "start": float(xmin),
                "end": float(xmax),
                "text": text
            })
    
    log_step("ALIGN", f"MFA found {len(word_intervals)} word intervals")
    
    # Map word intervals back to syllables
    # Each word may contain multiple syllables
    results = []
    word_idx = 0
    
    for syllable_info in flat_syllables:
        if word_idx < len(word_intervals) and syllable_info.get("is_word_start", True):
            word = word_intervals[word_idx]
            # Distribute word duration across its syllables
            # Count syllables in this word
            word_text = syllable_info.get("word", "")
            num_syllables = max(1, len(word_text.split('-')))
            syl_duration = (word["end"] - word["start"]) / num_syllables
            
            syl_start = word["start"]
            results.append({
                "syllable": syllable_info["text"],
                "start": syl_start,
                "end": syl_start + syl_duration,
                "confidence": 0.9,
                "is_rap": syllable_info.get("is_rap", False),
                "method": "mfa"
            })
            
            if not syllable_info.get("is_word_start", True) or num_syllables == 1:
                word_idx += 1
        else:
            # Continue within the same word
            if results:
                prev_end = results[-1]["end"]
                word = word_intervals[min(word_idx, len(word_intervals) - 1)]
                word_text = syllable_info.get("word", "")
                num_syllables = max(1, len(word_text.split('-')))
                syl_duration = (word["end"] - word["start"]) / num_syllables
                
                results.append({
                    "syllable": syllable_info["text"],
                    "start": prev_end,
                    "end": prev_end + syl_duration,
                    "confidence": 0.85,
                    "is_rap": syllable_info.get("is_rap", False),
                    "method": "mfa_interpolated"
                })
    
    log_step("ALIGN", f"Mapped {len(results)} syllable timings from MFA")
    return results


def align_fallback(audio_path: str, parsed_lines: List[List[dict]]) -> List[dict]:
    """Fallback alignment: distribute syllables evenly across audio duration.
    
    Uses librosa to get audio duration, then spaces syllables evenly
    with slight gaps between lines.
    """
    import librosa
    
    y, sr = librosa.load(audio_path, sr=22050)
    duration = librosa.get_duration(y=y, sr=sr)
    
    # Trim leading/trailing silence
    y_trimmed, trim_indices = librosa.effects.trim(y, top_db=20)
    trim_start = trim_indices[0] / sr
    trim_end = trim_indices[1] / sr
    active_duration = trim_end - trim_start
    
    log_step("ALIGN", f"Fallback alignment over {active_duration:.1f}s (trimmed from {duration:.1f}s)")
    
    flat_syllables = [s for line in parsed_lines for s in line]
    total_syllables = len(flat_syllables)
    
    if total_syllables == 0:
        return []
    
    # Allocate time: 90% for syllables, 10% for line gaps
    num_lines = len(parsed_lines)
    line_gap_time = active_duration * 0.10 / max(1, num_lines - 1) if num_lines > 1 else 0
    syllable_time = active_duration * 0.90 / total_syllables
    
    results = []
    current_time = trim_start
    syllable_idx = 0
    
    for line_idx, line in enumerate(parsed_lines):
        for syl in line:
            start = current_time
            end = current_time + syllable_time
            
            results.append({
                "syllable": syl["text"],
                "start": start,
                "end": end,
                "confidence": 0.3,  # Low confidence for fallback
                "is_rap": syl.get("is_rap", False),
                "method": "fallback_even",
                "line_index": line_idx
            })
            
            current_time = end
            syllable_idx += 1
        
        # Add gap between lines
        if line_idx < num_lines - 1:
            current_time += line_gap_time
    
    log_step("ALIGN", f"Fallback: distributed {len(results)} syllables over {active_duration:.1f}s")
    return results
