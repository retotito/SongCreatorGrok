"""Pitch detection using librosa PYIN."""

import time
import numpy as np
import librosa
from utils.logger import log_step

# Settings
CONFIDENCE_THRESHOLD = 0.4
log_step("INIT", "Using librosa PYIN for pitch detection")


def hz_to_midi(frequency: float) -> int:
    """Convert frequency in Hz to MIDI note number."""
    if frequency <= 0 or np.isnan(frequency):
        return 0
    return int(round(69 + 12 * np.log2(frequency / 440.0)))


def midi_to_note_name(midi_note: int) -> str:
    """Convert MIDI note number to note name (e.g., C4, D#5)."""
    if midi_note <= 0:
        return "---"
    notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    octave = (midi_note // 12) - 1
    note = notes[midi_note % 12]
    return f"{note}{octave}"


def detect_pitches(audio_path: str) -> dict:
    """Detect pitches using librosa PYIN.

    Args:
        audio_path: Path to audio file

    Returns:
        dict with keys: times, frequencies, confidences, midi_notes, sample_rate
    """
    log_step("PITCH", "Loading audio for pitch detection (PYIN)")
    start_time = time.time()

    y, sr = librosa.load(audio_path, sr=22050)
    total_duration = len(y) / sr
    log_step("PITCH", f"Audio loaded: {total_duration:.1f}s at {sr}Hz")

    f0, voiced_flag, voiced_probs = librosa.pyin(
        y, fmin=65, fmax=2093,
        sr=sr, frame_length=2048, hop_length=512
    )
    time_arr = librosa.times_like(f0, sr=sr, hop_length=512)
    frequency = np.where(voiced_flag, f0, 0)
    confidence = voiced_probs

    elapsed = time.time() - start_time
    log_step("PITCH", f"PYIN complete in {elapsed:.1f}s")

    # Convert to MIDI notes
    midi_notes = np.array([hz_to_midi(f) for f in frequency])

    # Stats
    high_conf_mask = confidence >= CONFIDENCE_THRESHOLD
    voiced_count = np.sum(high_conf_mask & (frequency > 0))
    log_step("PITCH", f"Voiced frames: {voiced_count}/{len(time_arr)} ({voiced_count/len(time_arr)*100:.0f}%)")

    return {
        "times": time_arr,
        "frequencies": frequency,
        "confidences": confidence,
        "midi_notes": midi_notes,
        "sample_rate": sr
    }


# Keep old name as alias for backward compatibility
detect_pitches_crepe = detect_pitches


def get_pitch_at_time(pitch_data: dict, time_sec: float, window: float = 0.05) -> int:
    """Get the median MIDI pitch at a specific time point.

    Args:
        pitch_data: Result from detect_pitches
        time_sec: Time in seconds
        window: Window size in seconds for averaging

    Returns:
        MIDI note number (0 if no pitch detected)
    """
    times = pitch_data["times"]
    midi_notes = pitch_data["midi_notes"]
    confidences = pitch_data["confidences"]

    mask = (times >= time_sec - window) & (times <= time_sec + window)
    mask &= (midi_notes > 0) & (confidences >= CONFIDENCE_THRESHOLD)

    window_notes = midi_notes[mask]

    if len(window_notes) == 0:
        return 0

    return int(np.median(window_notes))


def get_pitch_for_segment(pitch_data: dict, start_time: float, end_time: float) -> int:
    """Get the median MIDI pitch for a time segment.

    Args:
        pitch_data: Result from detect_pitches
        start_time: Segment start in seconds
        end_time: Segment end in seconds

    Returns:
        MIDI note number (0 if no pitch detected)
    """
    times = pitch_data["times"]
    midi_notes = pitch_data["midi_notes"]
    confidences = pitch_data["confidences"]

    mask = (times >= start_time) & (times <= end_time)
    mask &= (midi_notes > 0) & (confidences >= CONFIDENCE_THRESHOLD)

    segment_notes = midi_notes[mask]

    if len(segment_notes) == 0:
        return 0

    return int(np.median(segment_notes))


def get_pitch_subsegments(
    pitch_data: dict,
    start_time: float,
    end_time: float,
    min_duration_sec: float = 0.24,
    target_slice_sec: float = 0.18,
    max_segments: int = 4,
    min_pitch_span_semitones: int = 2,
) -> list:
    """Extract a small set of intra-syllable pitch subsegments.

    This is used to preserve vibrato-like movement for longer syllables by
    emitting continuation notes instead of a single median pitch note.
    """
    duration = max(0.0, float(end_time) - float(start_time))
    if duration < min_duration_sec:
        return []

    times = pitch_data["times"]
    midi_notes = pitch_data["midi_notes"]
    confidences = pitch_data["confidences"]

    base_mask = (times >= start_time) & (times <= end_time)
    base_mask &= (midi_notes > 0) & (confidences >= CONFIDENCE_THRESHOLD)

    seg_times = times[base_mask]
    seg_notes = midi_notes[base_mask]

    if len(seg_notes) < 4:
        return []

    pitch_span = int(np.max(seg_notes) - np.min(seg_notes))
    if pitch_span < int(min_pitch_span_semitones):
        return []

    # Derive a compact segment count from duration and clamp aggressively.
    raw_segments = int(round(duration / target_slice_sec))
    segment_count = max(2, min(max_segments, raw_segments))
    edges = np.linspace(start_time, end_time, segment_count + 1)

    chunks = []
    for idx in range(segment_count):
        left = float(edges[idx])
        right = float(edges[idx + 1])
        if idx < segment_count - 1:
            mask = (seg_times >= left) & (seg_times < right)
        else:
            mask = (seg_times >= left) & (seg_times <= right)

        if not np.any(mask):
            continue

        chunk_pitch = int(np.median(seg_notes[mask]))
        chunks.append({"start": left, "end": right, "pitch": chunk_pitch})

    if len(chunks) < 2:
        return []

    # Merge adjacent bins that quantize to the same pitch.
    merged = [chunks[0]]
    for chunk in chunks[1:]:
        prev = merged[-1]
        if chunk["pitch"] == prev["pitch"]:
            prev["end"] = chunk["end"]
        else:
            merged.append(chunk)

    if len(merged) < 2:
        return []

    merged_span = int(max(c["pitch"] for c in merged) - min(c["pitch"] for c in merged))
    if merged_span < int(min_pitch_span_semitones):
        return []

    return merged
