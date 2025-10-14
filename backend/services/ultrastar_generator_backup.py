"""
Ultrastar file generation service
"""

import random
import numpy as np
from typing import List, Dict, Any, Optional


class UltrastarGeneratorService:
    def __init__(self):
        pass

    def generate_ultrastar_file(self, lyrics: str, pitch_data: Dict[str, Any], 
                              bpm: float, voice_type: str, artist: str = "Unknown Artist", 
                              title: str = "Unknown Song", language: str = "English",
                              lyrics_result: Optional[Dict[str, Any]] = None) -> str:
        """Generate UltraStar file format from lyrics and pitch data"""
        
        print(f"DEBUG: Generating Ultrastar with BPM: {bpm} (already doubled)")
        print(f"DEBUG: Voice type: {voice_type}")
        
        # Check for Whisper timing data
        if lyrics_result and 'segments' in lyrics_result:
            print(f"DEBUG: Found {len(lyrics_result['segments'])} Whisper timing segments")
        else:
            print("DEBUG: No Whisper timing data found, using pitch-only mode")
        
        # Split lyrics into syllables
        syllable_lines = self._split_into_syllables(lyrics)
        
        # Generate note lines with timing
        note_lines = self._generate_note_lines(syllable_lines, pitch_data, bpm, voice_type, lyrics_result)
        
        # Format the complete Ultrastar content
        return self._format_ultrastar_content(artist, title, bpm, note_lines, language)

    def _split_into_syllables(self, lyrics: str) -> List[List[str]]:
        """Split lyrics into lines and syllables"""
        lines = lyrics.strip().split('\n')
        syllable_lines = []
        
        for line in lines:
            if line.strip():
                words = line.strip().split()
                syllables = []
                for word in words:
                    syllables.extend(word.split('-'))
                syllable_lines.append(syllables)
        
        return syllable_lines

    def _generate_note_lines(self, syllable_lines: List[List[str]], pitch_data: Dict[str, Any], 
                           bpm: float, voice_type: str, lyrics_result: Optional[Dict[str, Any]] = None) -> List[str]:
        note_lines = []
        start_beat = 0
        
        # Calculate target final beat from actual audio/pitch duration
        target_final_beat = self._calculate_target_final_beat(pitch_data, bpm)
        print(f"DEBUG: Target final beat calculated as: {target_final_beat}")
        
        # Calculate total syllables to determine scaling factor
        total_syllables = sum(len(line) for line in syllable_lines)
        
        # Calculate scaling factor to reach target timing
        # Account for syllables + breaks between lines
        estimated_beats_without_scaling = total_syllables * 4 + len(syllable_lines) * 20  # Rough estimate
        scale_factor = target_final_beat / max(estimated_beats_without_scaling, 1)
        print(f"DEBUG: Scale factor: {scale_factor} (target: {target_final_beat}, estimated: {estimated_beats_without_scaling})")
        
        # Extract pitch information
        midi_notes = pitch_data.get("midi_notes", np.array([]))
        voiced_flag = pitch_data.get("voiced_flag", np.array([]))
        times = pitch_data.get("times", np.array([]))
        
        for line_syllables in syllable_lines:
            for syllable in line_syllables:
                # Get actual pitch for this syllable
                pitch = self._get_pitch_for_syllable(midi_notes, voiced_flag, times, start_beat, bpm)
                
                # Use scaled duration (2-6 beats range like original)
                base_duration = random.randint(2, 6)
                duration_beats = max(1, int(base_duration * scale_factor))
                
                note_lines.append(f": {start_beat} {duration_beats} {pitch} {syllable}")
                start_beat += duration_beats
            
            # Add break with proper scaling
            break_start = start_beat
            base_break_duration = random.randint(15, 40)
            break_duration = max(5, int(base_break_duration * scale_factor))
            break_end = break_start + break_duration
            
            note_lines.append(f"- {break_start} {break_end}")
            start_beat = break_end
        
        print(f"DEBUG: Final beat reached: {start_beat}, target was: {target_final_beat}")
        return note_lines
    
    def _calculate_target_final_beat(self, pitch_data: Dict[str, Any], bpm: float) -> int:
        """Calculate target final beat based on actual audio duration"""
        
        # Try to get the actual duration from pitch data
        times = pitch_data.get("times", np.array([]))
        
        if len(times) > 0:
            # Use the last time point from pitch analysis
            last_time_seconds = float(times[-1])
            print(f"DEBUG: Last pitch time: {last_time_seconds} seconds")
        else:
            # Fallback: use a default duration
            last_time_seconds = 212.0  # Default based on your pitch summary
            print(f"DEBUG: Using default duration: {last_time_seconds} seconds")
        
        # Convert time to beats: beats = (time_seconds * BPM * 4) / 60
        # BPM is already doubled, so this gives us quarter-note resolution
        target_beats = int((last_time_seconds * bpm * 4) / 60)
        print(f"DEBUG: Calculated target beats: {target_beats} from {last_time_seconds}s at {bpm} BPM")
        
        return target_beats
    
    def _get_pitch_for_syllable(self, midi_notes: np.ndarray, voiced_flag: np.ndarray, 
                               times: np.ndarray, start_beat: int, bpm: float) -> int:
        """Get appropriate pitch for a syllable based on its timing"""
        
        if len(midi_notes) == 0 or len(times) == 0:
            return 63  # Default pitch
        
        # Convert beat back to time to find corresponding pitch
        beat_time = (start_beat * 60.0) / (bpm * 4.0)  # Convert beat to seconds
        
        # Find closest time index
        time_idx = np.argmin(np.abs(times - beat_time))
        
        # Check if this time has valid pitch data
        if time_idx < len(voiced_flag) and time_idx < len(midi_notes):
            if voiced_flag[time_idx]:
                midi_note = midi_notes[time_idx]
                if not np.isnan(midi_note) and midi_note > 0:
                    return int(midi_note)
        
        # Fallback: look for nearby voiced frames
        search_range = min(10, len(voiced_flag) // 2)
        for offset in range(1, search_range):
            # Check before
            idx = time_idx - offset
            if idx >= 0 and idx < len(voiced_flag) and idx < len(midi_notes):
                if voiced_flag[idx]:
                    midi_note = midi_notes[idx]
                    if not np.isnan(midi_note) and midi_note > 0:
                        return int(midi_note)
            
            # Check after
            idx = time_idx + offset
            if idx < len(voiced_flag) and idx < len(midi_notes):
                if voiced_flag[idx]:
                    midi_note = midi_notes[idx]
                    if not np.isnan(midi_note) and midi_note > 0:
                        return int(midi_note)
        
        # Final fallback: use median of all valid pitches
        valid_mask = voiced_flag & ~np.isnan(midi_notes) & (midi_notes > 0)
        valid_pitches = midi_notes[valid_mask]
        if len(valid_pitches) > 0:
            return int(np.median(valid_pitches))
        
        return 63  # Default pitch
from typing import List, Dict, Any, Optional
import numpy as np
from config import UltrastarConfig
from tests.test_baseline import ChangeTracker, BaselineValidator

class UltrastarGeneratorService:
    def __init__(self, config: UltrastarConfig = UltrastarConfig):
        self.config = config
        self.timing_settings = config.get_timing_settings()
    
    def generate_ultrastar_file(self, audio_analysis: Dict[str, Any], syllable_lines: List[List[str]], 
                              artist: Optional[str] = None, title: Optional[str] = None, 
                              voice_type: str = "solo", lyrics_result: Optional[Dict[str, Any]] = None) -> str:
        try:
            original_bpm = audio_analysis["bpm"]
            pitch_data = audio_analysis["pitch_data"]
            ultrastar_bpm = original_bpm * 2.0
            gap_ms = self.config.REFERENCE_GAP
            
            # Check for Whisper timing
            if lyrics_result and lyrics_result.get("has_timing"):
                print(f"DEBUG: Using Whisper timing with {len(lyrics_result.get('aligned_syllables', []))} syllables")
            else:
                print(f"DEBUG: No Whisper timing, using fallback method")
            
            note_lines = self._generate_note_lines(syllable_lines, pitch_data, ultrastar_bpm, voice_type, lyrics_result)
            content = self._format_ultrastar_content(artist or "Unknown Artist", title or "Unknown Title", 
                                                   ultrastar_bpm, gap_ms, note_lines)
            return content
        except Exception as e:
            print(f"ERROR in generate_ultrastar_file: {e}")
            raise
    
    def _generate_note_lines(self, syllable_lines: List[List[str]], pitch_data: Dict[str, Any], 
                           bpm: float, voice_type: str, lyrics_result: Optional[Dict[str, Any]] = None) -> List[str]:
        note_lines = []
        start_beat = 0
        
        # Simple fallback method
        for line_syllables in syllable_lines:
            for syllable in line_syllables:
                pitch = 63  # Fixed pitch for now
                duration_beats = 4  # Fixed duration
                note_lines.append(f": {start_beat} {duration_beats} {pitch} {syllable}")
                start_beat += duration_beats
            
            # Add break
            break_end = start_beat + 20
            note_lines.append(f"- {start_beat} {break_end}")
            start_beat = break_end
        
        return note_lines
    
    def _format_ultrastar_content(self, artist: str, title: str, bpm: float, gap_ms: int, note_lines: List[str]) -> str:
        header = f"#ARTIST:{artist}\n#TITLE:{title}\n#BPM:{bpm:.2f}\n#GAP:{gap_ms}\n\n"
        content = header + "\n".join(note_lines) + "\nE"
        return content
