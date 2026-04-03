"""MIDI file generation from syllable timings and pitches."""

from mido import MidiFile, MidiTrack, MetaMessage, Message
from typing import List
from utils.logger import log_step


def generate_midi(
    syllable_timings: List[dict],
    pitch_data: dict,
    bpm: float,
    output_path: str
) -> str:
    """Generate a MIDI file from syllable timings and pitches.
    
    Args:
        syllable_timings: List from alignment service
        pitch_data: Result from pitch detection
        bpm: Ultrastar BPM (doubled)
        output_path: Path to save MIDI file
        
    Returns:
        Path to the generated MIDI file
    """
    from services.pitch_detection import get_pitch_for_segment
    
    log_step("MIDI", f"Generating MIDI: {len(syllable_timings)} notes")
    
    midi = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    midi.tracks.append(track)
    
    # Set tempo (actual BPM, not Ultrastar doubled)
    actual_bpm = bpm / 2
    tempo = int(60_000_000 / actual_bpm)  # microseconds per beat
    track.append(MetaMessage('set_tempo', tempo=tempo, time=0))
    
    # Track name
    track.append(MetaMessage('track_name', name='Vocals', time=0))
    
    prev_end_tick = 0
    
    for timing in syllable_timings:
        start_sec = timing["start"]
        end_sec = timing["end"]
        is_rap = timing.get("is_rap", False)
        
        # Get pitch
        if is_rap:
            midi_note = 60  # C4 for rap
        else:
            midi_note = get_pitch_for_segment(pitch_data, start_sec, end_sec)
            if midi_note == 0:
                midi_note = 60
        
        # Clamp to valid MIDI range
        midi_note = max(0, min(127, midi_note))
        
        # Convert time to ticks
        start_tick = int(start_sec * 480 * actual_bpm / 60)
        end_tick = int(end_sec * 480 * actual_bpm / 60)
        duration_ticks = max(1, end_tick - start_tick)
        
        # Delta time from previous event
        delta = max(0, start_tick - prev_end_tick)
        
        # Note on
        track.append(Message('note_on', note=midi_note, velocity=80, time=delta))
        # Note off
        track.append(Message('note_off', note=midi_note, velocity=0, time=duration_ticks))
        
        prev_end_tick = start_tick + duration_ticks
    
    # End of track
    track.append(MetaMessage('end_of_track', time=0))
    
    midi.save(output_path)
    log_step("MIDI", f"Saved MIDI: {output_path}")
    
    return output_path
