"""Vocal separation using Demucs v4."""

import os
import subprocess
import tempfile
import shutil
from utils.logger import log_step

# Check if Demucs is available
try:
    import demucs
    DEMUCS_AVAILABLE = True
    log_step("INIT", "Demucs vocal separation available")
except ImportError:
    DEMUCS_AVAILABLE = False
    log_step("INIT", "Demucs not installed, vocal separation unavailable")


def separate_vocals(audio_path: str, output_dir: str) -> str:
    """Extract vocals from a full song using Demucs v4.
    
    Args:
        audio_path: Path to the input audio file (MP3/WAV)
        output_dir: Directory to save the extracted vocals
        
    Returns:
        Path to the extracted vocal audio file
    """
    if not DEMUCS_AVAILABLE:
        raise RuntimeError("Demucs is not installed. Install with: pip install demucs")
    
    log_step("SEPARATE", f"Starting vocal separation: {os.path.basename(audio_path)}")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Run Demucs
        cmd = [
            "python", "-m", "demucs",
            "--two-stems", "vocals",  # Only separate vocals vs accompaniment
            "-o", temp_dir,
            "--mp3",  # Output as MP3 for smaller size
            audio_path
        ]
        
        log_step("SEPARATE", "Running Demucs (this may take a few minutes)...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode != 0:
            raise RuntimeError(f"Demucs failed: {result.stderr}")
        
        # Find the output vocal file
        # Demucs outputs to: temp_dir/htdemucs/filename/vocals.mp3
        song_name = os.path.splitext(os.path.basename(audio_path))[0]
        
        vocal_path = None
        for root, dirs, files in os.walk(temp_dir):
            for f in files:
                if "vocals" in f.lower():
                    vocal_path = os.path.join(root, f)
                    break
        
        if vocal_path is None:
            raise RuntimeError("Demucs did not produce a vocals file")
        
        # Copy to output directory
        output_path = os.path.join(output_dir, f"{song_name}_vocals.wav")
        shutil.copy2(vocal_path, output_path)
        
        log_step("SEPARATE", f"Vocals extracted: {output_path}")
        return output_path
