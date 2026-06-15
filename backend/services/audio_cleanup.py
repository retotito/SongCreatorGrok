"""Audio cleanup service — mute specified ms ranges while preserving duration."""

import os
import numpy as np
import librosa
import soundfile as sf
from utils.logger import log_step


def _load_audio_any_format(path: str):
    """Load audio while being tolerant of formats unsupported by libsndfile.

    librosa prefers soundfile first; on some systems MP3 may raise a generic
    "System error". Fall back to audioread/ffmpeg explicitly for those files.
    """
    try:
        return librosa.load(path, sr=None, mono=False)
    except Exception as primary_err:
        try:
            import audioread
            with audioread.audio_open(path) as stream:
                return librosa.load(stream, sr=None, mono=False)
        except Exception:
            raise primary_err


def merge_overlapping_segments(segments: list) -> list:
    """Merge overlapping cleanup segments into non-overlapping ranges.
    
    Args:
        segments: List of dicts with {start_ms, end_ms}
    
    Returns:
        Sorted list of non-overlapping segments
    """
    if not segments:
        return []
    
    sorted_segs = sorted(segments, key=lambda s: s.get("start_ms", 0))
    
    merged = []
    current_start = sorted_segs[0].get("start_ms", 0)
    current_end = sorted_segs[0].get("end_ms", 0)
    
    for seg in sorted_segs[1:]:
        seg_start = seg.get("start_ms", 0)
        seg_end = seg.get("end_ms", 0)
        
        if seg_start <= current_end:
            current_end = max(current_end, seg_end)
        else:
            merged.append({"start_ms": current_start, "end_ms": current_end})
            current_start = seg_start
            current_end = seg_end
    
    merged.append({"start_ms": current_start, "end_ms": current_end})
    return merged


def generate_cleaned_audio(
    vocal_audio_path: str,
    cleanup_segments: list,
    output_path: str,
    # Legacy params kept for backward compat but no longer used
    bpm: float = None,
    gap_ms: int = None,
) -> dict:
    """Generate cleaned audio by muting specified ms ranges.
    
    Preserves total audio duration by muting (setting to 0.0) rather than cutting.
    
    Args:
        vocal_audio_path: Path to vocals audio file
        cleanup_segments: List of dicts with {start_ms, end_ms}
        output_path: Where to save cleaned audio
    
    Returns:
        Dict with status, sample_rate, num_samples, muted_segments
    """
    if not os.path.isfile(vocal_audio_path):
        raise FileNotFoundError(f"Vocal audio not found: {vocal_audio_path}")
    
    if not cleanup_segments:
        raise ValueError("No cleanup segments provided")
    
    log_step("CLEANUP", f"Loading audio: {vocal_audio_path}")
    audio, sr = _load_audio_any_format(vocal_audio_path)
    
    if audio.ndim == 1:
        audio = np.expand_dims(audio, axis=0)
    
    log_step("CLEANUP", f"Loaded: {audio.shape[0]} channels, {sr} Hz, {audio.shape[1]} samples")
    
    merged_segments = merge_overlapping_segments(cleanup_segments)
    log_step("CLEANUP", f"Merged {len(cleanup_segments)} segments → {len(merged_segments)} non-overlapping ranges")
    
    muted_segments_time = []
    for seg in merged_segments:
        start_sec = seg.get("start_ms", 0) / 1000.0
        end_sec   = seg.get("end_ms",   0) / 1000.0
        
        start_sample = max(0, min(int(start_sec * sr), audio.shape[1] - 1))
        end_sample   = max(0, min(int(end_sec   * sr), audio.shape[1]))
        
        if start_sample < end_sample:
            audio[:, start_sample:end_sample] = 0.0
            muted_segments_time.append({
                "start_time": start_sec,
                "end_time":   end_sec,
            })
            log_step("CLEANUP", f"Muted: {start_sec:.3f}s → {end_sec:.3f}s (samples {start_sample}→{end_sample})")
    
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
