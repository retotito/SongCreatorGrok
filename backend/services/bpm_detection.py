"""BPM detection using librosa beat tracking."""

import librosa
import numpy as np
from utils.logger import log_step


def detect_bpm(audio_path: str) -> float:
    """Detect BPM from an audio file.
    
    Returns Ultrastar BPM (librosa BPM × 2 for precision).
    """
    log_step("BPM", f"Loading audio: {audio_path}")
    
    y, sr = librosa.load(audio_path, sr=22050)
    
    # Trim silence
    y_trimmed, _ = librosa.effects.trim(y, top_db=20)
    log_step("BPM", f"Audio loaded: {len(y_trimmed)/sr:.1f}s at {sr}Hz")
    
    # Detect tempo
    tempo, _ = librosa.beat.beat_track(y=y_trimmed, sr=sr)
    
    # librosa may return an array
    if hasattr(tempo, '__len__'):
        bpm = float(tempo[0])
    else:
        bpm = float(tempo)
    
    # Ultrastar uses BPM × 2 for higher precision
    ultrastar_bpm = bpm * 2
    
    log_step("BPM", f"Detected BPM: {bpm:.1f} -> Ultrastar BPM: {ultrastar_bpm:.1f}")
    
    return ultrastar_bpm


def get_audio_duration(audio_path: str) -> float:
    """Get the duration of an audio file in seconds."""
    y, sr = librosa.load(audio_path, sr=22050)
    return librosa.get_duration(y=y, sr=sr)
