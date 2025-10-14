"""
Configuration management for Ultrastar Song Generator.
    @classmethod
    def get_timing_settings(cls) -> Dict[str, Any]:
        """Get timing settings as a dictionary"""
        return {
            "syllable_duration_beats": cls.SYLLABLE_DURATION_BEATS,
            "break_duration_range": cls.BREAK_DURATION_RANGE,
            "scale_factor": cls.SCALE_FACTOR,
            "bpm_multiplier": cls.BPM_MULTIPLIER
        }ed settings to prevent parameter drift during development.
"""
from typing import Tuple, Dict, Any
import os


class UltrastarConfig:
    """Centralized configuration to prevent drift during development"""
    
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
    
    # === VALIDATION THRESHOLDS ===
    # Used to detect when changes break existing functionality
    BPM_TOLERANCE_RANGE = (270, 275)
    GAP_TOLERANCE_RANGE = (13000, 14000)
    MAX_TIMING_DRIFT_SECONDS = 2.0
    MAX_DURATION_MISMATCH_SECONDS = 10.0
    
    # === FILE PATHS ===
    DOWNLOADS_DIR = "downloads"
    CHANGE_LOG_FILE = "change_log.json"
    
    # === AUDIO PROCESSING ===
    DEMUCS_MODEL = "htdemucs"
    WHISPER_MODEL = "base"
    
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
    def validate_duration_match(cls, calculated_duration: float, audio_duration: float) -> bool:
        """Validate calculated duration matches audio duration"""
        return abs(calculated_duration - audio_duration) <= cls.MAX_DURATION_MISMATCH_SECONDS
    
    @classmethod
    def get_validation_summary(cls) -> Dict[str, Any]:
        """Get all validation thresholds for logging"""
        return {
            "bpm_range": cls.BPM_TOLERANCE_RANGE,
            "gap_range": cls.GAP_TOLERANCE_RANGE,
            "max_timing_drift": cls.MAX_TIMING_DRIFT_SECONDS,
            "max_duration_mismatch": cls.MAX_DURATION_MISMATCH_SECONDS
        }


class DevelopmentConfig(UltrastarConfig):
    """Development-specific configuration"""
    
    DEBUG = True
    ENABLE_CHANGE_TRACKING = True
    VERBOSE_LOGGING = True
    
    # Development overrides (if needed)
    # Only use these for testing new features
    # NEVER commit changes to base UltrastarConfig without testing
    
    @classmethod
    def enable_experimental_features(cls):
        """Enable experimental features for development"""
        # This method can be used to toggle experimental features
        # without affecting the base configuration
        pass


class ProductionConfig(UltrastarConfig):
    """Production-specific configuration"""
    
    DEBUG = False
    ENABLE_CHANGE_TRACKING = False
    VERBOSE_LOGGING = False
    
    # Stricter validation for production
    BPM_TOLERANCE_RANGE = (271, 273)  # Tighter range
    GAP_TOLERANCE_RANGE = (13100, 13300)  # Tighter range


def get_config():
    """Get appropriate configuration based on environment"""
    env = os.getenv("ENVIRONMENT", "development").lower()
    
    if env == "production":
        return ProductionConfig
    else:
        return DevelopmentConfig