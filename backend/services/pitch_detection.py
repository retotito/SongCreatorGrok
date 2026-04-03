"""Pitch detection using CREPE deep learning model."""

import numpy as np
from utils.logger import log_step, log_progress

# Try to import CREPE, fall back to basic pitch detection
try:
    import crepe
    CREPE_AVAILABLE = True
    log_step("INIT", "CREPE pitch detection available")
except ImportError:
    CREPE_AVAILABLE = False
    log_step("INIT", "CREPE not installed, will use librosa PYIN fallback")


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


def detect_pitches_crepe(audio_path: str, step_size: int = 10) -> dict:
    """Detect pitches using CREPE deep learning model.
    
    Args:
        audio_path: Path to audio file
        step_size: Time step in milliseconds between predictions
        
    Returns:
        dict with keys: times, frequencies, confidences, midi_notes
    """
    import librosa
    import soundfile as sf
    
    log_step("PITCH", "Loading audio for CREPE pitch detection")
    
    # CREPE needs 16kHz mono audio
    y, sr = librosa.load(audio_path, sr=16000, mono=True)
    log_step("PITCH", f"Audio loaded: {len(y)/sr:.1f}s at {sr}Hz")
    
    if CREPE_AVAILABLE:
        log_step("PITCH", "Running CREPE pitch detection (this may take a moment)...")
        time_arr, frequency, confidence, _ = crepe.predict(
            y, sr,
            step_size=step_size,
            viterbi=True,  # Smoother pitch tracking
            model_capacity='medium'  # Balance speed vs accuracy
        )
        log_step("PITCH", f"CREPE detected {len(time_arr)} frames")
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
    
    # Convert to MIDI notes
    midi_notes = np.array([hz_to_midi(f) for f in frequency])
    
    # Filter out low-confidence detections
    high_conf_mask = confidence >= 0.5
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
    mask &= (midi_notes > 0) & (confidences >= 0.5)
    
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
    mask &= (midi_notes > 0) & (confidences >= 0.5)
    
    segment_notes = midi_notes[mask]
    
    if len(segment_notes) == 0:
        return 0
    
    return int(np.median(segment_notes))
