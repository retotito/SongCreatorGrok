"""
Pydantic models for request/response validation.
Defines the data structures for API endpoints.
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, validator


class AudioUploadRequest(BaseModel):
    """Request model for audio upload"""
    voice_type: str = Field(default="solo", description="Type of voice: solo, duet, rap, rap_singing, background")
    artist: Optional[str] = Field(default=None, description="Artist name")
    title: Optional[str] = Field(default=None, description="Song title")
    
    @validator('voice_type')
    def validate_voice_type(cls, v):
        allowed_types = ["solo", "duet", "rap", "rap_singing", "background"]
        if v not in allowed_types:
            raise ValueError(f"voice_type must be one of: {allowed_types}")
        return v


class ProcessingStatus(BaseModel):
    """Status model for processing updates"""
    stage: str = Field(description="Current processing stage")
    progress: float = Field(ge=0, le=100, description="Progress percentage")
    message: str = Field(description="Status message")
    error: Optional[str] = Field(default=None, description="Error message if any")


class AudioAnalysisResult(BaseModel):
    """Result model for audio analysis"""
    bpm: float = Field(description="Detected BPM")
    audio_duration: float = Field(description="Audio duration in seconds")
    pitch_data: Dict[str, Any] = Field(description="Pitch analysis data")
    vocal_file_path: Optional[str] = Field(default=None, description="Path to separated vocal file")


class LyricsResult(BaseModel):
    """Result model for lyrics processing"""
    transcription: str = Field(description="Raw transcription text")
    syllable_lines: List[List[str]] = Field(description="Syllables organized by lines")
    syllable_count: int = Field(description="Total number of syllables")
    line_count: int = Field(description="Number of lyric lines")


class UltrastarGenerationResult(BaseModel):
    """Result model for Ultrastar file generation"""
    content: str = Field(description="Ultrastar file content")
    filename: str = Field(description="Generated filename")
    validation: Dict[str, Any] = Field(description="Validation results")
    statistics: Dict[str, Any] = Field(description="Generation statistics")


class MidiGenerationResult(BaseModel):
    """Result model for MIDI file generation"""
    filename: str = Field(description="Path to generated MIDI file")
    file_info: Dict[str, Any] = Field(description="MIDI file information")
    validation: Dict[str, Any] = Field(description="Validation results")


class ProcessingResult(BaseModel):
    """Complete processing result"""
    success: bool = Field(description="Whether processing succeeded")
    audio_analysis: Optional[AudioAnalysisResult] = Field(default=None)
    lyrics: Optional[LyricsResult] = Field(default=None)
    ultrastar: Optional[UltrastarGenerationResult] = Field(default=None)
    midi: Optional[MidiGenerationResult] = Field(default=None)
    error: Optional[str] = Field(default=None, description="Error message if failed")
    processing_time: float = Field(description="Total processing time in seconds")


class ValidationResult(BaseModel):
    """Model for validation results"""
    valid: bool = Field(description="Whether validation passed")
    errors: List[str] = Field(default_factory=list, description="List of errors")
    warnings: List[str] = Field(default_factory=list, description="List of warnings")


class ConfigurationInfo(BaseModel):
    """Model for configuration information"""
    bpm_settings: Dict[str, Any] = Field(description="BPM configuration")
    timing_settings: Dict[str, Any] = Field(description="Timing configuration")
    audio_settings: Dict[str, Any] = Field(description="Audio processing settings")
    validation_thresholds: Dict[str, Any] = Field(description="Validation thresholds")


class ChangeLogEntry(BaseModel):
    """Model for change tracking entries"""
    timestamp: str = Field(description="When the change occurred")
    component: str = Field(description="Which component was changed")
    change_type: str = Field(description="Type of change")
    reason: str = Field(description="Reason for the change")
    expected_impact: str = Field(description="Expected impact of the change")


class SystemStatus(BaseModel):
    """Model for system status information"""
    services_status: Dict[str, str] = Field(description="Status of each service")
    configuration: ConfigurationInfo = Field(description="Current configuration")
    recent_changes: List[ChangeLogEntry] = Field(description="Recent changes")
    statistics: Dict[str, Any] = Field(description="System statistics")


class ErrorResponse(BaseModel):
    """Standard error response model"""
    error: str = Field(description="Error message")
    error_type: str = Field(description="Type of error")
    component: Optional[str] = Field(default=None, description="Component that caused the error")
    suggestions: List[str] = Field(default_factory=list, description="Suggested solutions")


class SuccessResponse(BaseModel):
    """Standard success response model"""
    success: bool = Field(default=True, description="Success flag")
    message: str = Field(description="Success message")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Response data")


# Request models for specific endpoints
class ValidateConfigRequest(BaseModel):
    """Request to validate configuration"""
    config_data: Dict[str, Any] = Field(description="Configuration to validate")


class UpdateConfigRequest(BaseModel):
    """Request to update configuration"""
    updates: Dict[str, Any] = Field(description="Configuration updates")
    validate_only: bool = Field(default=False, description="Only validate, don't apply")


class BaselineTestRequest(BaseModel):
    """Request to run baseline tests"""
    test_types: List[str] = Field(default_factory=lambda: ["format", "timing"], description="Types of tests to run")
    ultrastar_content: Optional[str] = Field(default=None, description="Ultrastar content to test")
    audio_duration: Optional[float] = Field(default=None, description="Audio duration for timing tests")


# Response models for specific endpoints
class HealthResponse(BaseModel):
    """Health check response"""
    status: str = Field(description="Service health status")
    services: Dict[str, str] = Field(description="Individual service statuses")
    uptime: float = Field(description="Service uptime in seconds")
    version: str = Field(description="Service version")


class FileListResponse(BaseModel):
    """Response for file listing"""
    files: List[Dict[str, Any]] = Field(description="List of files with metadata")
    total_count: int = Field(description="Total number of files")
    total_size: int = Field(description="Total size in bytes")


class DownloadResponse(BaseModel):
    """Response for file download endpoints"""
    filename: str = Field(description="Downloaded filename")
    content_type: str = Field(description="File content type")
    size: int = Field(description="File size in bytes")