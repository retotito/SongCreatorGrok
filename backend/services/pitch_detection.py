"""Pitch detection using CREPE deep learning model, with PYIN fallback."""

import os
import sys
import time
import subprocess
import numpy as np
from utils.logger import log_step, log_progress

# Fix TensorFlow threading issues that cause mutex deadlocks on some systems
os.environ["TF_NUM_INTEROP_THREADS"] = "1"
os.environ["TF_NUM_INTRAOP_THREADS"] = "1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

# CREPE is broken on this system (TF mutex crash on macOS + Python 3.9)
# Skip it entirely and use PYIN which is fast and reliable
CREPE_AVAILABLE = False
log_step("INIT", "Using librosa PYIN for pitch detection (fast & reliable)")

# Settings
CREPE_MODEL = "tiny"
CREPE_STEP_SIZE = 30
CONFIDENCE_THRESHOLD = 0.4
log_step("INIT", f"Pitch config: CREPE={'yes' if CREPE_AVAILABLE else 'no (using PYIN)'}, model={CREPE_MODEL}, step={CREPE_STEP_SIZE}ms")


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


def detect_pitches_crepe(audio_path: str, step_size: int = None) -> dict:
    """Detect pitches using CREPE deep learning model.
    
    Args:
        audio_path: Path to audio file
        step_size: Time step in milliseconds between predictions (default: CREPE_STEP_SIZE)
        
    Returns:
        dict with keys: times, frequencies, confidences, midi_notes
    """
    import librosa

    if step_size is None:
        step_size = CREPE_STEP_SIZE
    
    log_step("PITCH", "Loading audio for pitch detection")
    start_time = time.time()
    
    # CREPE needs 16kHz mono audio
    y, sr = librosa.load(audio_path, sr=16000, mono=True)
    total_duration = len(y) / sr
    log_step("PITCH", f"Audio loaded: {total_duration:.1f}s at {sr}Hz")
    
    # Trim silence to reduce processing time
    y_trimmed, trim_indices = librosa.effects.trim(y, top_db=25)
    trim_offset = trim_indices[0] / sr
    trimmed_duration = len(y_trimmed) / sr
    log_step("PITCH", f"Trimmed silence: {trimmed_duration:.1f}s to process (skipped {trim_offset:.1f}s leading silence)")
    
    if CREPE_AVAILABLE:
        log_step("PITCH", f"Running CREPE (model={CREPE_MODEL}, step={step_size}ms)...")
        time_arr, frequency, confidence, _ = crepe.predict(
            y_trimmed, sr,
            step_size=step_size,
            viterbi=True,        # Smoother pitch tracking
            model_capacity=CREPE_MODEL
        )
        # Adjust timestamps for trimmed silence
        time_arr = time_arr + trim_offset
        
        elapsed = time.time() - start_time
        log_step("PITCH", f"CREPE complete in {elapsed:.1f}s — {len(time_arr)} frames from {trimmed_duration:.1f}s audio")
    else:
        log_step("PITCH", "Using librosa PYIN fallback")
        y_22k, sr_22k = librosa.load(audio_path, sr=22050)
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y_22k, fmin=65, fmax=2093,
            sr=sr_22k, frame_length=2048, hop_length=512
        )
        time_arr = librosa.times_like(f0, sr=sr_22k, hop_length=512)
        frequency = np.where(voiced_flag, f0, 0)
        confidence = voiced_probs
        elapsed = time.time() - start_time
        log_step("PITCH", f"PYIN fallback complete in {elapsed:.1f}s")
    
    # Convert to MIDI notes
    midi_notes = np.array([hz_to_midi(f) for f in frequency])
    
    # Filter out low-confidence detections
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


def get_pitch_at_time(pitch_data: dict, time_sec: float, window: float = 0.05) -> int:
    """Get the median MIDI pitch at a specific time point.
    
    Args:
        pitch_data: Result from detect_pitches_crepe
        time_sec: Time in seconds
        window: Window size in seconds for averaging
        
    Returns:
        MIDI note number (0 if no pitch detected)
    """
    times = pitch_data["times"]
    midi_notes = pitch_data["midi_notes"]
    confidences = pitch_data["confidences"]
    
    # Find frames within the window
    mask = (times >= time_sec - window) & (times <= time_sec + window)
    mask &= (midi_notes > 0) & (confidences >= CONFIDENCE_THRESHOLD)
    
    window_notes = midi_notes[mask]
    
    if len(window_notes) == 0:
        return 0
    
    return int(np.median(window_notes))


def get_pitch_for_segment(pitch_data: dict, start_time: float, end_time: float) -> int:
    """Get the median MIDI pitch for a time segment.
    
    Args:
        pitch_data: Result from detect_pitches_crepe
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
