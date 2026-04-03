# Ultrastar Song Generator v2.0

AI-powered tool that generates Ultrastar karaoke .txt files from audio + lyrics input. Uses deep learning models for pitch detection, vocal separation, and forced alignment.

## Architecture

- **Frontend**: Svelte + Vite (port 5173) — 5-step wizard with built-in piano roll editor
- **Backend**: Python FastAPI (port 8001) — service-based with isolated AI workers

## AI Models

| Model | Purpose | Status |
|-------|---------|--------|
| CREPE | Pitch detection (deep learning) | Required |
| Demucs v4 | Vocal separation | Optional (can upload vocals directly) |
| MFA | Forced alignment (syllable timing) | Optional (fallback: even distribution) |

## Quick Start

### 1. Backend

```bash
# Create virtual environment (if not present)
python3 -m venv .venv
source .venv/bin/activate

# Install core dependencies
pip install fastapi uvicorn python-multipart librosa soundfile numpy mido pyphen aiofiles pydantic crepe

# Optional: Install Demucs for vocal separation
pip install demucs

# Optional: Install MFA for forced alignment
pip install montreal-forced-aligner

# Start backend
cd backend && python main.py
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Workflow

1. **Upload** — Upload a song (full mix or vocals-only). Optionally run Demucs to separate vocals.
2. **Lyrics** — Enter or paste lyrics with syllable hyphenation (e.g. `beau-ti-ful`).
3. **Generate** — Run the AI pipeline: BPM detection → pitch analysis → alignment → Ultrastar format.
4. **Editor** — Review and correct notes in the built-in piano roll editor.
5. **Export** — Download the Ultrastar .txt file, MIDI, and processing summary.

## VS Code Tasks

Use the pre-configured tasks to start servers:
- **Start Frontend Dev Server** — `cd frontend && npm run dev`
- **Start Backend Server** — `cd backend && python main.py`

## Project Structure

```
frontend/           Svelte app
  src/
    components/     Step1Upload, Step2Lyrics, Step3Generate, Step4Editor, Step5Export
    stores/         Shared state (appStore.js)
    services/       API client (api.js)
backend/            FastAPI server
  services/         AI service modules (pitch, alignment, BPM, vocals, ultrastar, midi)
  workers/          Subprocess isolation for AI tasks
  utils/            Logging, error handling
frontendTest/       Test audio + lyrics files
docs/               Architecture docs, plan
```
