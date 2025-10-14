"""
Audio processing service for Ultrastar Song Generator.
Handles BPM detection, pitch analysis, and vocal separation.
"""
import librosa
import numpy as np
from pathlib import Path
from typing import Tuple, Optional, Dict, Any
from demucs import separate
import tempfile
import os

from config import UltrastarConfig
from tests.test_baseline import ChangeTracker


class AudioProcessingService:
    """Isolated audio processing service"""
    
    def __init__(self, config: UltrastarConfig = None):
        self.config = config if config is not None else UltrastarConfig()
        self.audio_settings = self.config.get_audio_settings()
    
    def separate_vocals(self, audio_path: str, output_dir: str) -> str:
        """
        Separate vocals from audio using Demucs.
        Returns path to separated vocals file.
        """
        ChangeTracker.log_change(
            component="audio_processing",
            change_type="vocal_separation",
            reason="Separating vocals for pitch detection",
            expected_impact="Extract clean vocals for better pitch analysis"
        )
        
        try:
            print(f"Starting vocal separation for: {audio_path}")
            print(f"Output directory: {output_dir}")
            print(f"Using Demucs model: {self.config.DEMUCS_MODEL}")
            
            # Use Demucs for vocal separation
            separate.main(["--two-stems=vocals", "-o", output_dir, audio_path])
            
            # Demucs outputs to a subdirectory: output_dir/model_name/filename/vocals.wav
            audio_filename = Path(audio_path).stem
            demucs_output = os.path.join(output_dir, self.config.DEMUCS_MODEL, audio_filename)
            vocals_file = os.path.join(demucs_output, "vocals.wav")
            
            print(f"Looking for vocals at: {vocals_file}")
            print(f"Vocals file exists: {os.path.exists(vocals_file)}")
            
            # List all files in the output directory for debugging
            if os.path.exists(demucs_output):
                print(f"Files in {demucs_output}: {os.listdir(demucs_output)}")
            else:
                print(f"Demucs output directory doesn't exist: {demucs_output}")
                # Check what directories were actually created
                if os.path.exists(output_dir):
                    print(f"Files in output_dir {output_dir}: {os.listdir(output_dir)}")
            
            if os.path.exists(vocals_file):
                # Move to expected location
                vocals_path = os.path.join(output_dir, "vocals.wav")
                import shutil
                shutil.move(vocals_file, vocals_path)
                
                print(f"Successfully moved vocals to: {vocals_path}")
                
                ChangeTracker.log_change(
                    component="audio_processing",
                    change_type="vocal_separation_success",
                    reason=f"Successfully separated vocals using {self.config.DEMUCS_MODEL}",
                    expected_impact="Clean vocals available for pitch analysis"
                )
                
                return vocals_path
            else:
                raise FileNotFoundError(f"Demucs vocal separation failed - vocals.wav not found at {vocals_file}")
                
        except Exception as e:
            print(f"ERROR in vocal separation: {str(e)}")
            print(f"Exception type: {type(e)}")
            import traceback
            traceback.print_exc()
            
            ChangeTracker.log_change(
                component="audio_processing",
                change_type="error",
                reason=f"Vocal separation failed: {str(e)}",
                expected_impact="CRITICAL: Returning original audio instead of vocals!"
            )
            
            # CRITICAL: This returns the original file when separation fails!
            print(f"WARNING: Returning original audio file due to separation failure: {audio_path}")
            return audio_path
    
    def analyze_audio(self, audio_path: str) -> Dict[str, Any]:
        """
        Analyze audio file for BPM and pitch information.
        Returns analysis results in a standardized format.
        """
        ChangeTracker.log_change(
            component="audio_processing",
            change_type="audio_analysis",
            reason="Analyzing audio for BPM and pitch detection",
            expected_impact="Extract timing and pitch data for Ultrastar generation"
        )
        
        # Load audio with consistent settings
        y, sr = librosa.load(
            audio_path, 
            sr=self.audio_settings["sample_rate"]
        )
        
        # Trim leading and trailing silence
        y, _ = librosa.effects.trim(y, top_db=self.audio_settings["trim_top_db"])
        
        # Analyze BPM and beats
        bpm, beats = self._detect_bpm_and_beats(y, sr)
        
        # Detect pitches using PYIN
        pitch_data = self._detect_pitches(y, sr)
        
        # Calculate first pitch time for GAP
        first_pitch_time = self._calculate_first_pitch_time(pitch_data)
        
        result = {
            "bpm": float(bpm),
            "beats": beats.tolist() if isinstance(beats, np.ndarray) else beats,
            "pitch_data": pitch_data,
            "first_pitch_time": first_pitch_time,
            "audio_duration": len(y) / sr,
            "sample_rate": sr
        }
        
        # Validate BPM is in expected range
        if not self.config.validate_bpm(result["bpm"]):
            ChangeTracker.log_change(
                component="audio_processing",
                change_type="warning",
                reason=f"BPM {result['bpm']} outside expected range {self.config.BPM_TOLERANCE_RANGE}",
                expected_impact="May indicate analysis issue or unusual audio"
            )
        
        return result
    
    def _detect_bpm_and_beats(self, y: np.ndarray, sr: int) -> Tuple[float, np.ndarray]:
        """Detect BPM and beat positions"""
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beats, sr=sr)
        return float(tempo), beat_times
    
    def _detect_pitches(self, y: np.ndarray, sr: int) -> Dict[str, Any]:
        """Detect pitches using PYIN algorithm"""
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y,
            fmin=self.audio_settings["pyin_fmin"],
            fmax=self.audio_settings["pyin_fmax"],
            sr=sr,
            frame_length=self.audio_settings["pyin_frame_length"],
            hop_length=self.audio_settings["pyin_hop_length"],
            fill_na=np.nan
        )
        
        # Convert frequencies to MIDI notes
        midi_notes = np.full_like(f0, np.nan)
        valid_freqs = ~np.isnan(f0)
        midi_notes[valid_freqs] = librosa.hz_to_midi(f0[valid_freqs])
        
        # Round to nearest MIDI note
        midi_notes = np.round(midi_notes).astype(float)
        
        # Create time array
        times = librosa.times_like(f0, sr=sr, hop_length=self.audio_settings["pyin_hop_length"])
        
        return {
            "frequencies": f0,
            "voiced_flag": voiced_flag,
            "voiced_probabilities": voiced_probs,
            "midi_notes": midi_notes,
            "times": times
        }
    
    def _calculate_first_pitch_time(self, pitch_data: Dict[str, Any]) -> float:
        """Calculate the time of the first detected pitch"""
        voiced_flag = pitch_data["voiced_flag"]
        times = pitch_data["times"]
        
        if np.any(voiced_flag):
            return float(times[voiced_flag][0])
        else:
            return 0.0
    
    def validate_audio_analysis(self, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate audio analysis results against baseline expectations"""
        validation_result = {
            "valid": True,
            "warnings": [],
            "errors": []
        }
        
        # Check BPM
        bpm = analysis_result.get("bpm", 0)
        if not self.config.validate_bpm(bpm):
            validation_result["warnings"].append(
                f"BPM {bpm} outside expected range {self.config.BPM_TOLERANCE_RANGE}"
            )
        
        # Check if we have pitch data
        pitch_data = analysis_result.get("pitch_data", {})
        if not pitch_data.get("voiced_flag", []).any():
            validation_result["errors"].append("No voiced segments detected in audio")
            validation_result["valid"] = False
        
        # Check audio duration
        duration = analysis_result.get("audio_duration", 0)
        if duration < 30:  # Less than 30 seconds
            validation_result["warnings"].append(f"Audio duration {duration}s is very short")
        
        return validation_result