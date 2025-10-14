"""
Baseline tests to prevent regressions in Ultrastar file generation.
These tests lock in the current working behavior to prevent circular debugging.
"""
import pytest
import json
import os
from datetime import datetime
from typing import Dict, Any


class BaselineValidator:
    """Validates generated Ultrastar files against known working patterns"""
    
    EXPECTED_BPM_RANGE = (270, 275)  # Acceptable BPM range
    EXPECTED_GAP_RANGE = (13000, 14000)  # Acceptable GAP range
    MAX_TIMING_DRIFT = 2.0  # Maximum acceptable timing drift in seconds
    
    @staticmethod
    def validate_ultrastar_format(content: str) -> Dict[str, Any]:
        """Validate basic Ultrastar file format compliance"""
        lines = content.strip().split('\n')
        
        result = {
            "valid": True,
            "errors": [],
            "metadata": {},
            "note_count": 0,
            "break_count": 0
        }
        
        # Check for required headers
        has_artist = any(line.startswith('#ARTIST:') for line in lines)
        has_title = any(line.startswith('#TITLE:') for line in lines)
        has_bpm = any(line.startswith('#BPM:') for line in lines)
        has_gap = any(line.startswith('#GAP:') for line in lines)
        
        if not all([has_artist, has_title, has_bpm, has_gap]):
            result["valid"] = False
            result["errors"].append("Missing required headers")
        
        # Extract metadata
        for line in lines:
            if line.startswith('#BPM:'):
                try:
                    bpm = float(line.split(':')[1])
                    result["metadata"]["bpm"] = bpm
                    if not (BaselineValidator.EXPECTED_BPM_RANGE[0] <= bpm <= BaselineValidator.EXPECTED_BPM_RANGE[1]):
                        result["errors"].append(f"BPM {bpm} outside expected range")
                except ValueError:
                    result["errors"].append("Invalid BPM format")
            
            elif line.startswith('#GAP:'):
                try:
                    gap = int(line.split(':')[1])
                    result["metadata"]["gap"] = gap
                    if not (BaselineValidator.EXPECTED_GAP_RANGE[0] <= gap <= BaselineValidator.EXPECTED_GAP_RANGE[1]):
                        result["errors"].append(f"GAP {gap} outside expected range")
                except ValueError:
                    result["errors"].append("Invalid GAP format")
            
            elif line.startswith(':'):
                result["note_count"] += 1
                # Validate note line format: ": start duration pitch syllable"
                parts = line.split(' ', 4)
                if len(parts) < 5:
                    result["errors"].append(f"Invalid note line format: {line}")
            
            elif line.startswith('-'):
                result["break_count"] += 1
                # Validate break line format: "- start duration"
                parts = line.split(' ')
                if len(parts) != 3:
                    result["errors"].append(f"Invalid break line format: {line}")
        
        if result["errors"]:
            result["valid"] = False
        
        return result
    
    @staticmethod
    def validate_timing_consistency(content: str, audio_duration: float = None) -> Dict[str, Any]:
        """Validate timing consistency and duration matching"""
        lines = content.strip().split('\n')
        
        result = {
            "valid": True,
            "errors": [],
            "timing_info": {}
        }
        
        note_lines = [line for line in lines if line.startswith(':') or line.startswith('-')]
        
        if not note_lines:
            result["valid"] = False
            result["errors"].append("No note or break lines found")
            return result
        
        # Check for proper beat progression
        last_end_beat = 0
        for line in note_lines:
            parts = line.split(' ')
            if len(parts) >= 3:
                try:
                    start_beat = int(parts[1])
                    duration = int(parts[2])
                    
                    if start_beat < last_end_beat:
                        result["errors"].append(f"Beat regression detected: {start_beat} < {last_end_beat}")
                    
                    last_end_beat = start_beat + duration
                    
                except ValueError:
                    result["errors"].append(f"Invalid timing values in line: {line}")
        
        result["timing_info"]["last_beat"] = last_end_beat
        result["timing_info"]["total_beats"] = last_end_beat
        
        if result["errors"]:
            result["valid"] = False
        
        return result


class ChangeTracker:
    """Track changes to prevent circular debugging"""
    
    LOG_FILE = "change_log.json"
    
    @staticmethod
    def log_change(component: str, change_type: str, reason: str, expected_impact: str):
        """Log each change with context"""
        timestamp = datetime.now().isoformat()
        log_entry = {
            "timestamp": timestamp,
            "component": component,
            "change_type": change_type,
            "reason": reason,
            "expected_impact": expected_impact
        }
        
        # Append to change log file
        try:
            with open(ChangeTracker.LOG_FILE, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            print(f"Warning: Could not log change: {e}")
    
    @staticmethod
    def get_recent_changes(limit: int = 10) -> list:
        """Get recent changes from log"""
        if not os.path.exists(ChangeTracker.LOG_FILE):
            return []
        
        try:
            with open(ChangeTracker.LOG_FILE, "r") as f:
                lines = f.readlines()
                recent_lines = lines[-limit:] if len(lines) > limit else lines
                return [json.loads(line.strip()) for line in recent_lines if line.strip()]
        except Exception as e:
            print(f"Warning: Could not read change log: {e}")
            return []


# Test fixtures for baseline validation
@pytest.fixture
def sample_ultrastar_content():
    """Sample valid Ultrastar content for testing"""
    return """#ARTIST:U2
#TITLE:Beautiful Day
#BPM:272.00
#GAP:13208

: 0 4 45 The
: 4 2 52  heart
: 8 4 52  is
: 12 4 50  a
: 16 14 52  bloom
- 32 56
: 76 6 47 Shoots
: 84 6 54  up
: 92 2 52  through
"""


def test_baseline_format_validation(sample_ultrastar_content):
    """Test that format validation works correctly"""
    result = BaselineValidator.validate_ultrastar_format(sample_ultrastar_content)
    assert result["valid"] == True
    assert result["metadata"]["bpm"] == 272.0
    assert result["metadata"]["gap"] == 13208
    assert result["note_count"] > 0
    assert result["break_count"] > 0


def test_baseline_timing_validation(sample_ultrastar_content):
    """Test that timing validation works correctly"""
    result = BaselineValidator.validate_timing_consistency(sample_ultrastar_content)
    assert result["valid"] == True
    assert result["timing_info"]["total_beats"] > 0


def test_change_tracking():
    """Test that change tracking works"""
    ChangeTracker.log_change(
        component="test",
        change_type="test_change",
        reason="testing",
        expected_impact="none"
    )
    
    recent = ChangeTracker.get_recent_changes(1)
    assert len(recent) >= 1
    assert recent[-1]["component"] == "test"


if __name__ == "__main__":
    # Run basic validation on sample content
    sample = """#ARTIST:Test
#TITLE:Test
#BPM:272.00
#GAP:13208

: 0 4 60 Test
- 4 56
"""
    
    format_result = BaselineValidator.validate_ultrastar_format(sample)
    timing_result = BaselineValidator.validate_timing_consistency(sample)
    
    print("Format validation:", format_result)
    print("Timing validation:", timing_result)