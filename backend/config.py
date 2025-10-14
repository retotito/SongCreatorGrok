"""
Configuration management for Ultrastar Song Generator.
Provides centralized configuration with locked working values to prevent parameter drift.
"""
from typing import Dict, Any


class UltrastarConfig:
    """
    Configuration class with locked working values.
    
    These values have been tested and proven to work correctly.
    DO NOT CHANGE without running baseline tests first.
    """
    
    # === LOCKED WORKING VALUES ===
    # These values are known to work - DO NOT CHANGE without baseline testing
    REFERENCE_BPM = 272.0
    REFERENCE_GAP = 13208
    BPM_MULTIPLIER = 1  # No doubling - locked as working
    
    # Timing settings (locked as working)
    SYLLABLE_DURATION_BEATS = 1  # Fixed duration per syllable
    BREAK_DURATION_RANGE = (10, 30)  # Random range for break durations
    SCALE_FACTOR = 1  # No scaling - locked as working
    
    # Audio processing settings
    SAMPLE_RATE = 22050
    TRIM_TOP_DB = 30
    
    # PYIN pitch detection settings
    PYIN_FMIN_HZ = 65  # C2
    PYIN_FMAX_HZ = 2093  # C7
    PYIN_FRAME_LENGTH = 2048
    PYIN_HOP_LENGTH = 512
    
    # Validation thresholds
    BPM_TOLERANCE_RANGE = (200, 350)  # Acceptable BPM range
    GAP_TOLERANCE_RANGE = (10000, 20000)  # Acceptable GAP range in ms
    DURATION_TOLERANCE_PERCENT = 15  # Acceptable duration difference %
    
    # Voice processing
    VOICE_VELOCITY = 64  # MIDI velocity for vocal notes
    DEFAULT_PITCH = 60  # C4 - default when no pitch detected
    
    # File handling
    TEMP_DIR_PREFIX = "ultrastar_"
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
    ALLOWED_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.webm'}
    
    # Whisper model settings
    WHISPER_MODEL = "base"  # Whisper model for lyrics transcription
    
    # Demucs model settings
    DEMUCS_MODEL = "htdemucs"  # Default Demucs model for vocal separation

    @classmethod
    def get_audio_settings(cls) -> Dict[str, Any]:
        """Get audio processing settings as a dictionary"""
        return {
            "sample_rate": cls.SAMPLE_RATE,
            "trim_top_db": cls.TRIM_TOP_DB,
            "pyin_fmin": cls.PYIN_FMIN_HZ,
            "pyin_fmax": cls.PYIN_FMAX_HZ,
            "pyin_frame_length": cls.PYIN_FRAME_LENGTH,
            "pyin_hop_length": cls.PYIN_HOP_LENGTH
        }
    
    @classmethod
    def get_timing_settings(cls) -> Dict[str, Any]:
        """Get timing settings as a dictionary"""
        return {
            "syllable_duration_beats": cls.SYLLABLE_DURATION_BEATS,
            "break_duration_range": cls.BREAK_DURATION_RANGE,
            "scale_factor": cls.SCALE_FACTOR,
            "bpm_multiplier": cls.BPM_MULTIPLIER
        }
    
    @classmethod
    def validate_bpm(cls, bpm: float) -> bool:
        """Validate BPM is within acceptable range"""
        return cls.BPM_TOLERANCE_RANGE[0] <= bpm <= cls.BPM_TOLERANCE_RANGE[1]
    
    @classmethod
    def validate_gap(cls, gap: int) -> bool:
        """Validate GAP is within acceptable range"""
        return cls.GAP_TOLERANCE_RANGE[0] <= gap <= cls.GAP_TOLERANCE_RANGE[1]
    
    @classmethod
    def validate_duration_match(cls, calculated: float, actual: float) -> bool:
        """Validate calculated duration matches actual within tolerance"""
        percent_diff = abs(calculated - actual) / actual * 100
        return percent_diff <= cls.DURATION_TOLERANCE_PERCENT
    
    @classmethod
    def get_validation_thresholds(cls) -> Dict[str, Any]:
        """Get validation thresholds as a dictionary"""
        return {
            "bpm_range": cls.BPM_TOLERANCE_RANGE,
            "gap_range": cls.GAP_TOLERANCE_RANGE,
            "duration_tolerance_percent": cls.DURATION_TOLERANCE_PERCENT
        }
    
    @classmethod
    def is_locked_value(cls, parameter: str) -> bool:
        """Check if a parameter is a locked working value"""
        locked_params = {
            'REFERENCE_BPM', 'REFERENCE_GAP', 'BPM_MULTIPLIER',
            'SYLLABLE_DURATION_BEATS', 'SCALE_FACTOR'
        }
        return parameter in locked_params


class DevelopmentConfig(UltrastarConfig):
    """Development-specific configuration with debug settings"""
    
    DEBUG_MODE = True
    VERBOSE_LOGGING = True
    CHANGE_TRACKING_ENABLED = True


class ProductionConfig(UltrastarConfig):
    """Production-specific configuration with optimized settings"""
    
    DEBUG_MODE = False
    VERBOSE_LOGGING = False
    CHANGE_TRACKING_ENABLED = False


# Add methods to the UltrastarConfig class
# (extending the existing class with methods)
