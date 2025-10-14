"""
Lyrics processing service for Ultrastar Song Generator.
Handles lyrics transcription and syllable splitting.
"""
import whisper
import pyphen
from typing import List, Optional, Dict, Any

from config import UltrastarConfig
from tests.test_baseline import ChangeTracker


class LyricsProcessingService:
    """Isolated lyrics processing service"""
    
    def __init__(self, config: UltrastarConfig = None):
        self.config = config if config is not None else UltrastarConfig()
        self._whisper_model = None
        self._pyphen_dict = {}
    
    def get_whisper_model(self):
        """Lazy load Whisper model"""
        if self._whisper_model is None:
            self._whisper_model = whisper.load_model(self.config.WHISPER_MODEL)
        return self._whisper_model
    
    def get_pyphen_dict(self, language: str):
        """Lazy load Pyphen dictionary for given language"""
        if language not in self._pyphen_dict:
            self._pyphen_dict[language] = pyphen.Pyphen(lang=language)
        return self._pyphen_dict[language]
    
    def transcribe_lyrics(self, audio_path: str, language: str = "en") -> str:
        """
        Transcribe lyrics from audio file.
        Returns cleaned lyrics text.
        """
        result = self.transcribe_with_timing(audio_path, language)
        return result["text"]
    
    def transcribe_with_timing(self, audio_path: str, language: str = "en") -> Dict[str, Any]:
        """
        Transcribe lyrics from audio file with timing information.
        Returns dictionary with text, segments, and word-level timing.
        """
        ChangeTracker.log_change(
            component="lyrics_processing",
            change_type="transcription_with_timing",
            reason="Transcribing lyrics from audio with timing data",
            expected_impact="Extract lyrics and timing for Ultrastar generation"
        )
        
        try:
            # Try whisper-timestamped first for better timing
            try:
                import whisper_timestamped as whisper_ts
                model = whisper_ts.load_model(self.config.WHISPER_MODEL)
                initial_prompt = f"Lyrics in {language}"
                result = whisper_ts.transcribe(model, audio_path, initial_prompt=initial_prompt)
                
                ChangeTracker.log_change(
                    component="lyrics_processing",
                    change_type="success",
                    reason="Used whisper-timestamped for transcription with timing",
                    expected_impact="Better timing accuracy for alignment"
                )
                
                # Clean the lyrics text
                cleaned_text = self._clean_lyrics(result["text"])
                
                return {
                    "text": cleaned_text,
                    "segments": result.get("segments", []),
                    "language": result.get("language", language),
                    "has_timing": True
                }
                
            except ImportError:
                # Fallback to regular whisper
                model = self.get_whisper_model()
                result = model.transcribe(audio_path, initial_prompt=f"Lyrics in {language}")
                
                ChangeTracker.log_change(
                    component="lyrics_processing",
                    change_type="fallback",
                    reason="Used regular whisper (whisper-timestamped not available)",
                    expected_impact="Will use word-level timing instead of word-segment timing"
                )
                
                # Clean the lyrics text
                cleaned_text = self._clean_lyrics(result["text"])
                
                return {
                    "text": cleaned_text,
                    "segments": result.get("segments", []),
                    "language": result.get("language", language),
                    "has_timing": len(result.get("segments", [])) > 0
                }
            
        except Exception as e:
            ChangeTracker.log_change(
                component="lyrics_processing",
                change_type="error",
                reason=f"Lyrics transcription with timing failed: {str(e)}",
                expected_impact="Cannot generate Ultrastar file without lyrics timing"
            )
            raise
    
    def split_into_syllables(self, lyrics: str, language: str = "en") -> List[List[str]]:
        """
        Split lyrics into syllables line by line.
        Returns list of lines, each containing list of syllables.
        """
        ChangeTracker.log_change(
            component="lyrics_processing",
            change_type="syllable_splitting",
            reason="Splitting lyrics into syllables for fine-grained timing",
            expected_impact="Better synchronization with audio"
        )
        
        try:
            dic = self.get_pyphen_dict(language)
            lines = lyrics.split('\n')
            syllable_lines = []
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                words = line.split()
                syllables = []
                
                for word in words:
                    # Split word into syllables
                    word_syllables = dic.inserted(word).split('-')
                    syllables.extend(word_syllables)
                
                if syllables:  # Only add non-empty lines
                    syllable_lines.append(syllables)
            
            total_syllables = sum(len(line) for line in syllable_lines)
            ChangeTracker.log_change(
                component="lyrics_processing",
                change_type="success",
                reason=f"Split {len(syllable_lines)} lines into {total_syllables} syllables",
                expected_impact="Provides fine-grained timing control"
            )
            
            return syllable_lines
            
        except Exception as e:
            ChangeTracker.log_change(
                component="lyrics_processing",
                change_type="error",
                reason=f"Syllable splitting failed: {str(e)}",
                expected_impact="Will use word-level timing instead"
            )
            # Fallback to word-level splitting
            return self._fallback_word_splitting(lyrics)
    
    def _clean_lyrics(self, lyrics: str) -> str:
        """Clean transcribed lyrics"""
        # Remove Ultrastar metadata lines that might appear in transcription
        lines = lyrics.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            # Skip lines that look like Ultrastar metadata
            if line.startswith('#') or line.startswith(':') or line.startswith('-'):
                continue
            if line:  # Skip empty lines
                cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines)
    
    def _fallback_word_splitting(self, lyrics: str) -> List[List[str]]:
        """Fallback to word-level splitting if syllable splitting fails"""
        lines = lyrics.split('\n')
        word_lines = []
        
        for line in lines:
            line = line.strip()
            if line:
                words = line.split()
                if words:
                    word_lines.append(words)
        
        return word_lines
    
    def validate_lyrics(self, lyrics: str) -> Dict[str, Any]:
        """Validate lyrics content"""
        validation_result = {
            "valid": True,
            "warnings": [],
            "errors": [],
            "stats": {}
        }
        
        if not lyrics or not lyrics.strip():
            validation_result["valid"] = False
            validation_result["errors"].append("No lyrics content provided")
            return validation_result
        
        lines = [line.strip() for line in lyrics.split('\n') if line.strip()]
        words = lyrics.split()
        
        validation_result["stats"] = {
            "line_count": len(lines),
            "word_count": len(words),
            "character_count": len(lyrics)
        }
        
        # Check for reasonable content
        if len(lines) < 2:
            validation_result["warnings"].append("Very few lyric lines detected")
        
        if len(words) < 10:
            validation_result["warnings"].append("Very few words detected")
        
        # Check for potential transcription issues
        if any(char in lyrics for char in ['[', ']', '(', ')', '*']):
            validation_result["warnings"].append("Lyrics contain special characters that may indicate transcription artifacts")
        
        return validation_result
    
    def align_lyrics_with_timing(
        self, 
        user_lyrics: str, 
        whisper_timing: Dict[str, Any], 
        language: str = "en"
    ) -> List[Dict[str, Any]]:
        """
        Align user-provided lyrics with Whisper timing information.
        
        Args:
            user_lyrics: Clean, correct lyrics provided by user
            whisper_timing: Result from transcribe_with_timing()
            language: Language for syllable splitting
            
        Returns:
            List of syllables with timing information: [{"syllable": "word", "start": 1.2, "end": 1.5, "pitch_time": 1.35}, ...]
        """
        ChangeTracker.log_change(
            component="lyrics_processing",
            change_type="timing_alignment",
            reason="Aligning user lyrics with Whisper timing",
            expected_impact="Create time-aligned syllables for Ultrastar generation"
        )
        
        try:
            # Split user lyrics into syllables
            user_syllable_lines = self.split_into_syllables(user_lyrics, language)
            user_syllables = []
            for line in user_syllable_lines:
                user_syllables.extend(line)
            
            # Extract timing from Whisper segments
            whisper_segments = whisper_timing.get("segments", [])
            if not whisper_segments:
                # Fallback: create uniform timing if no segments available
                return self._create_uniform_timing(user_syllables, whisper_timing.get("text", ""))
            
            # Align syllables with timing
            aligned_syllables = self._align_syllables_to_segments(user_syllables, whisper_segments)
            
            ChangeTracker.log_change(
                component="lyrics_processing",
                change_type="alignment_success",
                reason=f"Successfully aligned {len(aligned_syllables)} syllables with timing",
                expected_impact="Ready for Ultrastar generation with proper timing"
            )
            
            return aligned_syllables
            
        except Exception as e:
            ChangeTracker.log_change(
                component="lyrics_processing",
                change_type="alignment_error",
                reason=f"Failed to align lyrics with timing: {str(e)}",
                expected_impact="Will fall back to uniform timing"
            )
            # Fallback to uniform timing
            user_syllable_lines = self.split_into_syllables(user_lyrics, language)
            user_syllables = []
            for line in user_syllable_lines:
                user_syllables.extend(line)
            return self._create_uniform_timing(user_syllables, whisper_timing.get("text", ""))
    
    def _align_syllables_to_segments(self, user_syllables: List[str], whisper_segments: List[Dict]) -> List[Dict[str, Any]]:
        """Align user syllables with Whisper timing segments"""
        aligned = []
        syllable_idx = 0
        
        for segment in whisper_segments:
            segment_start = segment.get("start", 0.0)
            segment_end = segment.get("end", segment_start + 1.0)
            segment_words = segment.get("words", [])
            
            if not segment_words:
                # No word-level timing, distribute syllables evenly across segment
                segment_text = segment.get("text", "").strip()
                if segment_text:
                    # Count words in segment
                    words_in_segment = len(segment_text.split())
                    syllables_per_word = 1.5  # Estimate
                    estimated_syllables = max(1, int(words_in_segment * syllables_per_word))
                    
                    # Distribute available syllables across this segment
                    syllables_to_assign = min(estimated_syllables, len(user_syllables) - syllable_idx)
                    segment_duration = segment_end - segment_start
                    syllable_duration = segment_duration / max(1, syllables_to_assign)
                    
                    for i in range(syllables_to_assign):
                        if syllable_idx < len(user_syllables):
                            start_time = segment_start + (i * syllable_duration)
                            end_time = start_time + syllable_duration
                            aligned.append({
                                "syllable": user_syllables[syllable_idx],
                                "start": start_time,
                                "end": end_time,
                                "pitch_time": (start_time + end_time) / 2
                            })
                            syllable_idx += 1
            else:
                # Use word-level timing from Whisper
                for word_info in segment_words:
                    word_start = word_info.get("start", segment_start)
                    word_end = word_info.get("end", segment_end)
                    word_text = word_info.get("word", "").strip()
                    
                    if word_text and syllable_idx < len(user_syllables):
                        # For now, assign one syllable per word (can be improved)
                        aligned.append({
                            "syllable": user_syllables[syllable_idx],
                            "start": word_start,
                            "end": word_end,
                            "pitch_time": (word_start + word_end) / 2
                        })
                        syllable_idx += 1
        
        # Handle any remaining syllables with uniform timing
        if syllable_idx < len(user_syllables):
            remaining = user_syllables[syllable_idx:]
            last_end = aligned[-1]["end"] if aligned else 0.0
            uniform_duration = 0.5  # 500ms per syllable
            
            for i, syllable in enumerate(remaining):
                start_time = last_end + (i * uniform_duration)
                end_time = start_time + uniform_duration
                aligned.append({
                    "syllable": syllable,
                    "start": start_time,
                    "end": end_time,
                    "pitch_time": (start_time + end_time) / 2
                })
        
        return aligned
    
    def _create_uniform_timing(self, syllables: List[str], reference_text: str) -> List[Dict[str, Any]]:
        """Create uniform timing when Whisper timing is not available"""
        aligned = []
        syllable_duration = 0.5  # 500ms per syllable
        
        for i, syllable in enumerate(syllables):
            start_time = i * syllable_duration
            end_time = start_time + syllable_duration
            aligned.append({
                "syllable": syllable,
                "start": start_time,
                "end": end_time,
                "pitch_time": (start_time + end_time) / 2
            })
        
        return aligned