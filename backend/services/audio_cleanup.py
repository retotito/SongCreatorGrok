"""Audio cleanup service — mute specified beat ranges while preserving duration."""

import os
import numpy as np
import librosa
import soundfile as sf
from utils.logger import log_step


def merge_overlapping_segments(segments: list) -> list:
    """Merge overlapping cleanup segments into non-overlapping ranges.
    
    Args:
        segments: List of dicts with {start_beat, end_beat}
    
    Returns:
        Sorted list of non-overlapping segments
    """
    if not segments:
        return []
    
    # Sort by start_beat
    sorted_segs = sorted(segments, key=lambda s: s.get("start_beat", 0))
    
    merged = []
    current_start = sorted_segs[0].get("start_beat", 0)
    current_end = sorted_segs[0].get("end_beat", 0)
    
    for seg in sorted_segs[1:]:
        seg_start = seg.get("start_beat", 0)
        seg_end = seg.get("end_beat", 0)
        
        # If this segment overlaps or touches the current one, merge
        if seg_start <= current_end:
            current_end = max(current_end, seg_end)
        else:
            # No overlap, save the current segment and start a new one
            merged.append({"start_beat": current_start, "end_beat": current_end})
            current_start = seg_start
            current_end = seg_end
    
    # Add the last segment
    merged.append({"start_beat": current_start, "end_beat": current_end})
    
    return merged


def beat_to_time(beat: float, bpm: float, gap_ms: int) -> float:
    """Convert beat value to time in seconds.
    
    Formula (inverse of time_to_beat):
        time_sec = (beat * 15 / bpm) + (gap_ms / 1000)
    
    Args:
        beat: Beat value (quarter-beat resolution, can be float)
        bpm: Song BPM (Ultrastar doubled)
        gap_ms: Gap in milliseconds
    
    Returns:
        Time in seconds
    """
    gap_sec = gap_ms / 1000.0
    time_sec = (beat * 15.0 / bpm) + gap_sec
    return time_sec


def generate_cleaned_audio(
    vocal_audio_path: str,
    cleanup_segments: list,
    bpm: float,
    gap_ms: int,
    output_path: str
) -> dict:
    """Generate cleaned audio by muting specified beat ranges.
    
    Preserves total audio duration by muting (setting to 0.0) rather than cutting.
    
    Args:
        vocal_audio_path: Path to vocals audio file
        cleanup_segments: List of dicts with {start_beat, end_beat}
        bpm: Song BPM (Ultrastar doubled)
        gap_ms: Gap in milliseconds
        output_path: Where to save cleaned audio
    
    Returns:
        Dict with status, sample_rate, num_samples, muted_segments (in time)
    """
    if not os.path.isfile(vocal_audio_path):
        raise FileNotFoundError(f"Vocal audio not found: {vocal_audio_path}")
    
    if not cleanup_segments:
        raise ValueError("No cleanup segments provided")
    
    # Load audio
    log_step("CLEANUP", f"Loading audio: {vocal_audio_path}")
    audio, sr = librosa.load(vocal_audio_path, sr=None, mono=False)
    
    # Ensure stereo
    if audio.ndim == 1:
        audio = np.expand_dims(audio, axis=0)
    
    log_step("CLEANUP", f"Loaded: {audio.shape[0]} channels, {sr} Hz, {audio.shape[1]} samples")
    
    # Merge overlapping segments
    merged_segments = merge_overlapping_segments(cleanup_segments)
    log_step("CLEANUP", f"Merged {len(cleanup_segments)} segments → {len(merged_segments)} non-overlapping ranges")
    
    # Convert beat ranges to time ranges and mute
    muted_segments_time = []
    for seg in merged_segments:
        start_beat = float(seg.get("start_beat", 0))
        end_beat = float(seg.get("end_beat", 0))
        
        # Convert to seconds
        start_sec = beat_to_time(start_beat, bpm, gap_ms)
        end_sec = beat_to_time(end_beat, bpm, gap_ms)
        
        # Convert to sample indices
        start_sample = int(start_sec * sr)
        end_sample = int(end_sec * sr)
        
        # Clamp to valid range
        start_sample = max(0, min(start_sample, audio.shape[1] - 1))
        end_sample = max(0, min(end_sample, audio.shape[1]))
        
        # Mute (set to 0.0)
        if start_sample < end_sample:
            audio[:, start_sample:end_sample] = 0.0
            muted_segments_time.append({
                "start_time": start_sec,
                "end_time": end_sec,
                "start_beat": start_beat,
                "end_beat": end_beat,
            })
            log_step("CLEANUP", f"Muted: beat {start_beat:.2f}→{end_beat:.2f} ({start_sec:.2f}s→{end_sec:.2f}s) = samples {start_sample}→{end_sample}")
    
    # Save cleaned audio
    log_step("CLEANUP", f"Saving cleaned audio: {output_path}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    sf.write(output_path, audio.T, sr, subtype='PCM_16')
    log_step("CLEANUP", f"Saved: {output_path}")
    
    return {
        "status": "ok",
        "output_path": output_path,
        "sample_rate": sr,
        "num_samples": audio.shape[1],
        "num_channels": audio.shape[0],
        "muted_segments": muted_segments_time,
        "segments_count": len(muted_segments_time),
    }
