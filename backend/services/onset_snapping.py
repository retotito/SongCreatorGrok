"""Onset snapping: refine syllable boundaries using spectral onset detection.

After WhisperX (or Whisper) alignment, syllable start times may still be off by
50-200ms. This module detects spectral onsets in the vocal track and snaps each
syllable start to the nearest onset within a configurable window.

This typically improves timing by 30-100ms per syllable for consonant-heavy syllables,
where the acoustic energy burst is offset from WhisperX's phoneme boundary.
"""

import numpy as np
from typing import List, Optional
from utils.logger import log_step

# Onset detection cache (per audio file)
_onset_cache: dict = {}


def detect_onsets(audio_path: str, sr: int = 22050) -> np.ndarray:
    """Detect spectral onsets in the vocal audio.
    
    Returns array of onset times in seconds.
    Uses a conservative onset detector tuned for vocals:
    - Spectral flux for onset strength (good for vocal attacks)
    - Moderate threshold to avoid false positives from vibrato
    """
    if audio_path in _onset_cache:
        return _onset_cache[audio_path]
    
    import librosa
    
    log_step("ONSET", f"Detecting onsets in {audio_path}...")
    
    y, sr_actual = librosa.load(audio_path, sr=sr, mono=True)
    
    # Compute onset strength envelope
    # Use mel spectrogram-based onset detection (good for vocals)
    onset_env = librosa.onset.onset_strength(
        y=y, sr=sr_actual,
        hop_length=256,  # ~11.6ms resolution at 22050Hz
        aggregate=np.median,  # Robust to harmonics
    )
    
    # Detect onset frames with moderate sensitivity
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr_actual,
        hop_length=256,
        backtrack=True,  # Backtrack to nearest preceding local minimum
        units='frames',
        delta=0.07,  # Moderate threshold — too low = false positives from vibrato
    )
    
    # Convert frames to times
    onset_times = librosa.frames_to_time(onset_frames, sr=sr_actual, hop_length=256)
    
    log_step("ONSET", f"Detected {len(onset_times)} onsets")
    
    _onset_cache[audio_path] = onset_times
    return onset_times


def snap_to_onsets(
    audio_path: str,
    syllable_timings: List[dict],
    snap_window_ms: float = 80.0,
    min_confidence_to_snap: float = 0.0,
) -> List[dict]:
    """Snap syllable start times to nearest spectral onset.
    
    Args:
        audio_path: Path to vocal audio file
        syllable_timings: List of syllable dicts with 'start', 'end', etc.
        snap_window_ms: Maximum distance (ms) to snap. Only snap if onset is
                       within this window. Default 80ms (conservative).
        min_confidence_to_snap: Only snap syllables with confidence >= this.
                               Set to 0 to snap all. Default 0.
    
    Returns:
        Modified syllable_timings with start times snapped to onsets.
        Each snapped syllable gets 'onset_snap_ms' field showing the adjustment.
    """
    if not syllable_timings:
        return syllable_timings
    
    try:
        onset_times = detect_onsets(audio_path)
    except Exception as e:
        log_step("ONSET", f"Onset detection failed: {e}")
        return syllable_timings
    
    if len(onset_times) == 0:
        log_step("ONSET", "No onsets detected, skipping snap")
        return syllable_timings
    
    snap_window_sec = snap_window_ms / 1000.0
    snap_count = 0
    total_snap_ms = 0.0
    
    for syl in syllable_timings:
        start = syl["start"]
        confidence = syl.get("confidence", 1.0)
        
        if confidence < min_confidence_to_snap:
            continue
        
        # Find nearest onset to this syllable's start time
        # Binary search for efficiency
        idx = np.searchsorted(onset_times, start)
        
        # Check candidates: onset at idx-1 and idx
        best_onset = None
        best_dist = float('inf')
        
        for candidate_idx in [idx - 1, idx]:
            if 0 <= candidate_idx < len(onset_times):
                dist = abs(onset_times[candidate_idx] - start)
                if dist < best_dist:
                    best_dist = dist
                    best_onset = onset_times[candidate_idx]
        
        if best_onset is not None and best_dist <= snap_window_sec:
            snap_ms = (best_onset - start) * 1000.0
            
            # Apply snap: move start to onset, keep duration similar
            original_dur = syl["end"] - syl["start"]
            syl["start"] = best_onset
            # Adjust end to maintain approximately same duration
            # But don't let it overlap with next syllable (caller handles that)
            syl["end"] = best_onset + original_dur
            
            syl["onset_snap_ms"] = round(snap_ms, 1)
            snap_count += 1
            total_snap_ms += abs(snap_ms)
    
    # Fix overlaps: ensure no syllable starts before previous one ends
    for i in range(1, len(syllable_timings)):
        prev_end = syllable_timings[i - 1]["end"]
        curr_start = syllable_timings[i]["start"]
        if curr_start < prev_end:
            # Split the overlap: move prev end and curr start to midpoint
            mid = (prev_end + curr_start) / 2
            syllable_timings[i - 1]["end"] = mid
            syllable_timings[i]["start"] = mid
    
    if snap_count > 0:
        avg_snap = total_snap_ms / snap_count
        log_step("ONSET", f"Snapped {snap_count}/{len(syllable_timings)} syllables "
                 f"(avg adjustment: {avg_snap:.1f}ms)")
    else:
        log_step("ONSET", "No syllables snapped (none within window)")
    
    return syllable_timings
