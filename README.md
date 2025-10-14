# Ultrastar Song Generator

An AI-powered tool to generate Ultrastar karaoke song files from audio sources (MP3 or YouTube URLs).

## Features

- Upload MP3 files or provide YouTube URLs
- Automatic vocal separation using Demucs
- Pitch and note detection with librosa PYIN
- Lyrics extraction via Whisper
- Beat and rhythm analysis with Librosa
- Generate Ultrastar .txt files and MIDI files
- Progress visualization and preview
- Download generated files
- **NEW**: Structured service architecture with change tracking
- **NEW**: Baseline validation to prevent regressions
- **NEW**: Health monitoring and status endpoints

## Tech Stack

- **Frontend**: Svelte with Vite
- **Backend**: Python FastAPI with structured services
- **AI Models**: Local (Whisper, Demucs, librosa, Transformers)
- **Architecture**: Service-based with configuration management and change tracking

## Architecture

### Service Architecture (v2.0)
The backend has been refactored from a monolithic structure into specialized services:

- **AudioProcessingService**: Handles vocal separation, BPM detection, and pitch analysis
- **LyricsProcessingService**: Manages transcription and syllable splitting
- **UltrastarGeneratorService**: Creates properly formatted Ultrastar files
- **MidiGeneratorService**: Generates MIDI files from pitch data
- **Configuration Management**: Centralized config with locked working values
- **Change Tracking**: Prevents circular debugging by logging all modifications
- **Baseline Validation**: Ensures no regressions during development

### API Endpoints
- `POST /process_audio2` - Main processing endpoint (maintains compatibility)
- `GET /health` - Service health check
- `GET /status` - System status with configuration and recent changes
- `GET /files` - List available download files
- `POST /validate` - Validate Ultrastar content
- `GET /download/{filename}` - Download generated files

## Setup

### Prerequisites
- Node.js (for frontend)
- Python 3.8+ (for backend)
- FFmpeg (for audio processing)

### Installation

1. Clone the repository
2. Install frontend dependencies:
   ```bash
   npm install
   ```
3. Install backend dependencies:
   ```bash
   cd backend
   pip install -r requirements.txt
   ```

### Running

1. Start the backend:
   ```bash
   cd backend
   python main.py
   ```
2. Start the frontend:
   ```bash
   npm run dev
   ```

## Usage

Upload an audio file or paste a YouTube URL, and the app will process it to generate an Ultrastar song file.

## Development

### Systematic Development Practices

To prevent circular debugging and maintain development momentum:

1. **Use Baseline Testing**: Before making changes, run baseline tests:
   ```bash
   cd backend
   python -m pytest tests/test_baseline.py
   ```

2. **Monitor Changes**: All modifications are logged via the change tracking system. Check recent changes:
   ```bash
   curl http://localhost:8000/status
   ```

3. **Validate Before Changes**: Use the validation endpoint to test modifications:
   ```bash
   curl -X POST http://localhost:8000/validate \
     -H "Content-Type: application/json" \
     -d '{"content": "your_ultrastar_content"}'
   ```

4. **Locked Configuration**: Working values in `config.py` are locked. Only change after baseline testing:
   - `REFERENCE_BPM = 272.0`
   - `REFERENCE_GAP = 13208`
   - `SYLLABLE_DURATION_BEATS = 1`

### Service Structure

```
backend/
├── main.py                    # Refactored API endpoints
├── config.py                  # Configuration management
├── models.py                  # Pydantic models
├── services/
│   ├── audio_processor.py     # Audio analysis
│   ├── lyrics_processor.py    # Lyrics processing
│   ├── ultrastar_generator.py # File generation
│   └── midi_generator.py      # MIDI creation
└── tests/
    └── test_baseline.py       # Baseline validation
```

This architecture prevents the circular debugging loops by:
- Isolating functionality into services
- Tracking all changes with timestamps and reasons
- Validating against known working outputs
- Locking proven configuration values