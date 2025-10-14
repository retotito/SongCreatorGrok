"""
MIDI file generation service.
Handles the creation of MIDI files from pitch analysis data.
"""
import os
import time
from typing import Dict, Any, Optional
import numpy as np
import pretty_midi

from config import UltrastarConfig
from tests.test_baseline import ChangeTracker


class MidiGeneratorService:
    """Isolated MIDI file generation service"""
    
    def __init__(self, config: UltrastarConfig = UltrastarConfig):
        self.config = config
        self.timing_settings = config.get_timing_settings()
    
    def generate_midi_file(
        self,
        pitch_data: Dict[str, Any],
        downloads_dir: str = "downloads",
        filename_prefix: str = "pitches"
    ) -> str:
        """
        Generate MIDI file from pitch analysis data.
        Returns the path to the generated MIDI file.
        """
        ChangeTracker.log_change(
            component="midi_generation",
            change_type="file_generation",
            reason="Generating MIDI file from pitch analysis",
            expected_impact="Create MIDI file for musical reference"
        )
        
        try:
            # Extract pitch data
            midi_notes = pitch_data["midi_notes"]
            voiced_flag = pitch_data["voiced_flag"]
            times = pitch_data["times"]
            
            # Create MIDI object
            midi_data = self._create_midi_from_pitches(midi_notes, voiced_flag, times)
            
            # Generate unique filename
            filename = self._generate_unique_filename(downloads_dir, filename_prefix)
            
            # Save MIDI file
            midi_data.write(filename)
            
            # Validate the generated file
            validation = self._validate_midi_file(filename)
            
            if not validation["valid"]:
                ChangeTracker.log_change(
                    component="midi_generation",
                    change_type="validation_error",
                    reason=f"Generated MIDI failed validation: {validation['errors']}",
                    expected_impact="MIDI file may not play correctly"
                )
            else:
                ChangeTracker.log_change(
                    component="midi_generation",
                    change_type="success",
                    reason=f"Generated MIDI file: {filename}",
                    expected_impact="MIDI file ready for use"
                )
            
            return filename
            
        except Exception as e:
            ChangeTracker.log_change(
                component="midi_generation",
                change_type="error",
                reason=f"MIDI generation failed: {str(e)}",
                expected_impact="Cannot create MIDI file"
            )
            raise
    
    def _create_midi_from_pitches(
        self,
        midi_notes: np.ndarray,
        voiced_flag: np.ndarray,
        times: np.ndarray
    ) -> pretty_midi.PrettyMIDI:
        """Create PrettyMIDI object from pitch data"""
        
        # Create MIDI object
        midi_data = pretty_midi.PrettyMIDI()
        
        # Create instrument (voice)
        voice_instrument = pretty_midi.Instrument(program=0)  # Acoustic Grand Piano
        
        # Process pitch data to create notes
        current_note = None
        current_start_time = None
        
        for i, (time, pitch, is_voiced) in enumerate(zip(times, midi_notes, voiced_flag)):
            if is_voiced and not np.isnan(pitch):
                # Valid pitched segment
                rounded_pitch = int(np.round(pitch))
                
                if current_note is None:
                    # Start new note
                    current_note = rounded_pitch
                    current_start_time = time
                elif current_note != rounded_pitch:
                    # Pitch changed - end current note and start new one
                    if current_start_time is not None:
                        self._add_note_to_instrument(
                            voice_instrument,
                            current_note,
                            current_start_time,
                            time
                        )
                    current_note = rounded_pitch
                    current_start_time = time
                # else: continue current note
            else:
                # Unvoiced segment - end current note if exists
                if current_note is not None and current_start_time is not None:
                    self._add_note_to_instrument(
                        voice_instrument,
                        current_note,
                        current_start_time,
                        time
                    )
                    current_note = None
                    current_start_time = None
        
        # Handle final note
        if current_note is not None and current_start_time is not None:
            self._add_note_to_instrument(
                voice_instrument,
                current_note,
                current_start_time,
                times[-1]
            )
        
        # Add instrument to MIDI
        midi_data.instruments.append(voice_instrument)
        
        return midi_data
    
    def _add_note_to_instrument(
        self,
        instrument: pretty_midi.Instrument,
        pitch: int,
        start_time: float,
        end_time: float,
        velocity: int = 64
    ):
        """Add a note to the MIDI instrument"""
        
        # Validate parameters
        if end_time <= start_time:
            return  # Skip invalid notes
        
        # Clamp pitch to valid MIDI range
        pitch = max(0, min(127, pitch))
        
        # Create note
        note = pretty_midi.Note(
            velocity=velocity,
            pitch=pitch,
            start=start_time,
            end=end_time
        )
        
        instrument.notes.append(note)
    
    def _generate_unique_filename(self, downloads_dir: str, prefix: str) -> str:
        """Generate unique filename for MIDI file"""
        
        # Ensure downloads directory exists
        os.makedirs(downloads_dir, exist_ok=True)
        
        # Generate timestamp-based filename
        timestamp = int(time.time() * 1000)  # Milliseconds for uniqueness
        filename = f"{prefix}_{timestamp}.mid"
        filepath = os.path.join(downloads_dir, filename)
        
        # Ensure uniqueness (unlikely but possible collision)
        counter = 1
        while os.path.exists(filepath):
            filename = f"{prefix}_{timestamp}_{counter}.mid"
            filepath = os.path.join(downloads_dir, filename)
            counter += 1
        
        return filepath
    
    def _validate_midi_file(self, filename: str) -> Dict[str, Any]:
        """Validate generated MIDI file"""
        
        validation_result = {
            "valid": True,
            "errors": [],
            "warnings": []
        }
        
        try:
            # Check file exists and has content
            if not os.path.exists(filename):
                validation_result["valid"] = False
                validation_result["errors"].append("MIDI file was not created")
                return validation_result
            
            file_size = os.path.getsize(filename)
            if file_size == 0:
                validation_result["valid"] = False
                validation_result["errors"].append("MIDI file is empty")
                return validation_result
            
            # Try to load the MIDI file
            midi_data = pretty_midi.PrettyMIDI(filename)
            
            # Check for instruments
            if len(midi_data.instruments) == 0:
                validation_result["warnings"].append("MIDI file has no instruments")
            else:
                # Check for notes
                total_notes = sum(len(inst.notes) for inst in midi_data.instruments)
                if total_notes == 0:
                    validation_result["warnings"].append("MIDI file has no notes")
                
                # Check duration
                if midi_data.get_end_time() == 0:
                    validation_result["warnings"].append("MIDI file has zero duration")
            
        except Exception as e:
            validation_result["valid"] = False
            validation_result["errors"].append(f"Failed to validate MIDI file: {str(e)}")
        
        return validation_result
    
    def get_midi_info(self, filename: str) -> Optional[Dict[str, Any]]:
        """Get information about a MIDI file"""
        
        try:
            if not os.path.exists(filename):
                return None
            
            midi_data = pretty_midi.PrettyMIDI(filename)
            
            info = {
                "filename": filename,
                "duration": midi_data.get_end_time(),
                "instruments": len(midi_data.instruments),
                "total_notes": sum(len(inst.notes) for inst in midi_data.instruments),
                "file_size": os.path.getsize(filename)
            }
            
            # Get pitch range if notes exist
            all_pitches = []
            for instrument in midi_data.instruments:
                all_pitches.extend([note.pitch for note in instrument.notes])
            
            if all_pitches:
                info["pitch_range"] = {
                    "min": min(all_pitches),
                    "max": max(all_pitches),
                    "count": len(all_pitches)
                }
            
            return info
            
        except Exception as e:
            ChangeTracker.log_change(
                component="midi_generation",
                change_type="info_error",
                reason=f"Failed to get MIDI info: {str(e)}",
                expected_impact="Cannot provide MIDI file details"
            )
            return None