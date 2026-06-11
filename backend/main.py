"""Ultrastar Song Generator — FastAPI Backend Server.

Thin route layer. All processing logic lives in services/.
"""

import multiprocessing
# Must be called before any other code when frozen with PyInstaller on macOS.
# Prevents child processes (e.g. Demucs workers) from re-executing the full app.
multiprocessing.freeze_support()

import os
# Disable HuggingFace XET protocol so large blobs are downloaded via plain HTTP,
# enabling progressive filesystem-based progress tracking.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
import sys

# On Windows, Tauri spawns the backend with piped stdout/stderr.
# Python's logging StreamHandler.flush() raises OSError [Errno 22] when the pipe
# read-end is dropped by Rust, which eventually crashes the uvicorn event loop.
# Wrap sys.stdout/stderr so those errors are silently swallowed.
if getattr(sys, 'frozen', False) and sys.platform == 'win32':
    class _PipeSafeStream:
        def __init__(self, stream):
            self._s = stream
        def write(self, data):
            try: return self._s.write(data)
            except OSError: return 0
        def flush(self):
            try: self._s.flush()
            except OSError: pass
        def fileno(self):
            try: return self._s.fileno()
            except Exception: return -1
        def isatty(self): return False
        def readable(self): return False
        def writable(self): return True
        def __getattr__(self, name): return getattr(self._s, name)
    sys.stdout = _PipeSafeStream(sys.stdout)
    sys.stderr = _PipeSafeStream(sys.stderr)

# Fix SSL certificate verification on macOS when running as a frozen PyInstaller app.
# Python bundles certifi but doesn't always point SSL_CERT_FILE at it automatically.
try:
    import certifi
    _certifi_ca = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", _certifi_ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _certifi_ca)
except ImportError:
    pass


def _fix_frozen_path():
    """When running as a PyInstaller frozen binary the macOS GUI launch environment
    strips most of PATH (no /opt/homebrew/bin etc.).  WhisperX calls ffmpeg as a
    subprocess by name, so we need to make sure it's findable."""
    if not getattr(sys, 'frozen', False):
        return
    # 1. bundled ffmpeg extracted alongside our binary by PyInstaller
    if hasattr(sys, '_MEIPASS'):
        mei = sys._MEIPASS
        ffmpeg_name = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'
        if os.path.isfile(os.path.join(mei, ffmpeg_name)):
            os.environ['PATH'] = mei + os.pathsep + os.environ.get('PATH', '')
            return
    # 2. common macOS install locations (Homebrew arm64, Homebrew x86, MacPorts)
    for d in ('/opt/homebrew/bin', '/usr/local/bin', '/opt/local/bin'):
        if os.path.isfile(os.path.join(d, 'ffmpeg')):
            os.environ['PATH'] = d + os.pathsep + os.environ.get('PATH', '')
            return


_fix_frozen_path()
import time
import math
import json
import uuid
import shutil
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from utils.logger import log, log_step
from utils.error_handler import (
    global_exception_handler,
    service_exception_handler,
    ServiceError,
)

# ────────────────────────────────────────────────────────────
# App setup
# ────────────────────────────────────────────────────────────
app = FastAPI(title="Ultrastar Song Generator", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173", "tauri://localhost", "http://tauri.localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.add_exception_handler(Exception, global_exception_handler)
app.add_exception_handler(ServiceError, service_exception_handler)

# Directories — use persistent user data dir when running as frozen sidecar,
# so sessions/uploads survive backend restarts between app launches.
def _user_data_dir() -> str:
    """Return a persistent data directory that survives PyInstaller temp extraction."""
    if getattr(sys, 'frozen', False):
        if sys.platform == 'win32':
            # Windows: store in %APPDATA%\com.ultrastar.creator
            base = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'com.ultrastar.creator')
        else:
            # macOS: store in ~/Library/Application Support/com.ultrastar.creator
            base = os.path.expanduser("~/Library/Application Support/com.ultrastar.creator")
    else:
        # Dev mode — store alongside source files as before
        base = os.path.dirname(__file__)
    return base

_DATA_DIR = _user_data_dir()
DOWNLOADS_DIR = os.path.join(_DATA_DIR, "downloads")
CORRECTIONS_DIR = os.path.join(_DATA_DIR, "corrections")
UPLOAD_DIR = os.path.join(_DATA_DIR, "uploads")
REFERENCE_DIR = os.path.join(_DATA_DIR, "reference_songs")
SESSIONS_DIR = os.path.join(_DATA_DIR, "sessions")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(CORRECTIONS_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(REFERENCE_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)

# In-memory session store
sessions: dict = {}


def save_session(session_id: str):
    """Persist a session to disk as JSON."""
    session = sessions.get(session_id)
    if not session:
        return
    path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session, f, default=str)
    except Exception as e:
        log_step("PERSIST", f"Failed to save session {session_id}: {e}")


def _safe_unlink(path: str):
    if not path:
        return
    try:
        if os.path.exists(path) and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _safe_unlink_download_name(name: str):
    if not name:
        return
    _safe_unlink(os.path.join(DOWNLOADS_DIR, os.path.basename(name)))


def safe_json(data):
    """Round-trip through JSON with default=str to sanitize numpy types etc."""
    return json.loads(json.dumps(data, default=str))


def load_sessions():
    """Load all sessions from disk on startup."""
    count = 0
    for fname in os.listdir(SESSIONS_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(SESSIONS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                session = json.load(f)
            sid = session.get("id", fname.replace(".json", ""))
            sessions[sid] = session
            count += 1
        except Exception as e:
            log_step("PERSIST", f"Failed to load {fname}: {e}")
    if count:
        log_step("PERSIST", f"Restored {count} sessions from disk")


# Load saved sessions on import
load_sessions()


# ────────────────────────────────────────────────────────────
# Health check
# ────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    from services.vocal_separation import DEMUCS_AVAILABLE
    
    return {
        "status": "ok",
        "version": "2.0.0",
        "models": {
            "pitch": "PYIN",
            "alignment": "WhisperX",
            "demucs": DEMUCS_AVAILABLE,
        }
    }


# ────────────────────────────────────────────────────────────
# First-run setup: model download status + SSE download stream
# ────────────────────────────────────────────────────────────

def _file_size_accurate(path: str) -> int:
    """Return the current size of a file.
    On Windows, uses CreateFileW + GetFileSizeEx which reads from the kernel's
    in-memory FCB (updated in real-time), bypassing the lazy NTFS MFT metadata
    that os.stat() reads and which stays at 0 for in-progress downloads.
    Falls back to os.path.getsize() on other platforms.
    """
    if sys.platform == 'win32':
        import ctypes
        GENERIC_READ      = 0x80000000
        FILE_SHARE_ALL    = 0x07  # READ | WRITE | DELETE
        OPEN_EXISTING     = 3
        FILE_ATTR_NORMAL  = 0x80
        handle = ctypes.windll.kernel32.CreateFileW(
            str(path), GENERIC_READ, FILE_SHARE_ALL,
            None, OPEN_EXISTING, FILE_ATTR_NORMAL, None,
        )
        INVALID = ctypes.c_size_t(-1).value
        if ctypes.c_size_t(handle).value == INVALID:
            return 0
        size = ctypes.c_int64(0)
        if ctypes.windll.kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
            ctypes.windll.kernel32.CloseHandle(handle)
            return max(0, size.value)
        ctypes.windll.kernel32.CloseHandle(handle)
        return 0
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _dir_bytes_accurate(directory: str) -> int:
    """Sum accurate file sizes for all files in a directory tree."""
    if not os.path.isdir(directory):
        return 0
    total = 0
    for root, _, files in os.walk(directory):
        for fname in files:
            total += _file_size_accurate(os.path.join(root, fname))
    return total


def _check_model_status() -> dict:
    """Return which AI models are already downloaded."""
    import shutil

    # ffmpeg
    ffmpeg_ok = shutil.which('ffmpeg') is not None
    log_step("MODEL_STATUS", f"ffmpeg: {'found at ' + shutil.which('ffmpeg') if ffmpeg_ok else 'NOT FOUND'}")

    # WhisperX / faster-whisper medium model
    whisperx_ok = False
    try:
        from huggingface_hub.constants import HF_HUB_CACHE as hf_cache
    except ImportError:
        hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
    log_step("MODEL_STATUS", f"HF cache path: {hf_cache}")
    faster_whisper_dir = os.path.join(hf_cache, "models--Systran--faster-whisper-medium")
    log_step("MODEL_STATUS", f"faster-whisper dir exists: {os.path.isdir(faster_whisper_dir)} ({faster_whisper_dir})")
    if os.path.isdir(faster_whisper_dir):
        # Check that there's at least one snapshot with the required model files
        snapshots = os.path.join(faster_whisper_dir, "snapshots")
        if os.path.isdir(snapshots):
            for snap in os.listdir(snapshots):
                snap_path = os.path.join(snapshots, snap)
                if os.path.isdir(snap_path):
                    config_ok = os.path.isfile(os.path.join(snap_path, "config.json"))
                    model_bin = os.path.join(snap_path, "model.bin")
                    model_exists = os.path.isfile(model_bin)
                    model_size = _file_size_accurate(model_bin) if model_exists else 0
                    model_ok = model_exists and model_size > 100_000_000
                    log_step("MODEL_STATUS", f"  snapshot {snap[:12]}: config={config_ok} model.bin={model_exists} size={model_size:,} bytes ok={model_ok}")
                    if config_ok and model_ok:
                        whisperx_ok = True
                        break
    # Fallback: vanilla whisper medium
    if not whisperx_ok:
        vanilla_path = os.path.expanduser("~/.cache/whisper/medium.pt")
        vanilla_size = _file_size_accurate(vanilla_path) if os.path.isfile(vanilla_path) else 0
        vanilla_ok = vanilla_size > 100_000_000
        log_step("MODEL_STATUS", f"vanilla whisper fallback: exists={os.path.isfile(vanilla_path)} size={vanilla_size:,} ok={vanilla_ok}")
        whisperx_ok = vanilla_ok

    # Demucs htdemucs
    demucs_ok = False
    torch_hub = os.path.expanduser("~/.cache/torch/hub/checkpoints")
    log_step("MODEL_STATUS", f"torch hub dir exists: {os.path.isdir(torch_hub)} ({torch_hub})")
    if os.path.isdir(torch_hub):
        for f in os.listdir(torch_hub):
            if f.endswith(".th") and (f.startswith("955717e8") or "htdemucs" in f.lower()):
                size = _file_size_accurate(os.path.join(torch_hub, f))
                log_step("MODEL_STATUS", f"  demucs checkpoint: {f} size={size:,} bytes")
                demucs_ok = True
                break

    # WhisperX wav2vec2 alignment model (English)
    wav2vec2_ok = False
    wav2vec2_path = os.path.expanduser(
        "~/.cache/torch/hub/checkpoints/wav2vec2_fairseq_base_ls960_asr_ls960.pth"
    )
    wav2vec2_size = _file_size_accurate(wav2vec2_path) if os.path.isfile(wav2vec2_path) else 0
    wav2vec2_ok = wav2vec2_size > 100_000_000
    log_step("MODEL_STATUS", f"wav2vec2: exists={os.path.isfile(wav2vec2_path)} size={wav2vec2_size:,} bytes ok={wav2vec2_ok}")

    result = {
        "ffmpeg": ffmpeg_ok,
        "whisperx": whisperx_ok,
        "demucs": demucs_ok,
        "wav2vec2": wav2vec2_ok,
        "ready": ffmpeg_ok and whisperx_ok and demucs_ok and wav2vec2_ok,
    }
    log_step("MODEL_STATUS", f"final: {result}")
    return result


@app.get("/api/setup/status")
async def setup_status():
    """Check which AI models are already downloaded."""
    return _check_model_status()


@app.get("/api/setup/download")
async def setup_download():
    """SSE stream: download any missing AI models and report progress."""
    import asyncio

    async def event_stream():
        def send(type: str, step: str = "", message: str = "", done: bool = False, error: bool = False, percent: int = None):
            import json
            data = {"type": type, "step": step, "message": message}
            if percent is not None:
                data["percent"] = percent
            return f"data: {json.dumps(data)}\n\n"

        status = _check_model_status()
        log_step("DOWNLOAD", f"setup/download triggered. status={status}")
        if not status["ffmpeg"]:
            yield send("progress", "ffmpeg", "ffmpeg not found — please reinstall the app", error=True)
        else:
            yield send("done", "ffmpeg", "ffmpeg found")

        await asyncio.sleep(0.05)

        # ── WhisperX ──
        if not status["whisperx"]:
            yield send("progress", "whisperx", "Downloading WhisperX medium model (~1.5 GB)…", percent=0)
            await asyncio.sleep(0.05)
            try:
                from huggingface_hub import snapshot_download

                WHISPERX_TOTAL_BYTES = 1_528_000_000  # ~1.5 GB
                try:
                    from huggingface_hub.constants import HF_HUB_CACHE as _hf_cache
                except ImportError:
                    _hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
                _wx_model_dir = os.path.join(_hf_cache, "models--Systran--faster-whisper-medium")

                loop = asyncio.get_event_loop()
                fut = loop.run_in_executor(
                    None,
                    lambda: snapshot_download("Systran/faster-whisper-medium")
                )
                while not fut.done():
                    await asyncio.sleep(2.0)
                    downloaded = _dir_bytes_accurate(_wx_model_dir)
                    pct = min(99, int(downloaded * 100 / WHISPERX_TOTAL_BYTES))
                    mb_done = downloaded / 1_000_000
                    mb_total = WHISPERX_TOTAL_BYTES / 1_000_000
                    log_step("DOWNLOAD", f"whisperx downloaded={downloaded} bytes ({mb_done:.1f} MB) pct={pct}%")
                    yield send("progress", "whisperx",
                               f"Downloading… {mb_done:.0f} / {mb_total:.0f} MB",
                               percent=pct)
                await fut

                # Windows hardlink repair: huggingface_hub stores real content in blobs/
                # and creates hardlinks in snapshots/. On Windows this can silently fail,
                # leaving snapshot files as 0 bytes while blobs/ has the full content.
                # Detect and repair by copying blobs directly to snapshot files.
                import shutil as _shutil
                _snap_root = os.path.join(_wx_model_dir, "snapshots")
                _blobs_dir = os.path.join(_wx_model_dir, "blobs")
                if os.path.isdir(_snap_root) and os.path.isdir(_blobs_dir):
                    # Build a size→blob_path lookup from the blobs folder
                    _blob_by_size = {}
                    for _blob_name in os.listdir(_blobs_dir):
                        _blob_path = os.path.join(_blobs_dir, _blob_name)
                        if os.path.isfile(_blob_path):
                            _blob_by_size[os.path.getsize(_blob_path)] = _blob_path
                    for _snap in os.listdir(_snap_root):
                        _snap_path = os.path.join(_snap_root, _snap)
                        if not os.path.isdir(_snap_path):
                            continue
                        for _fname in os.listdir(_snap_path):
                            _fpath = os.path.join(_snap_path, _fname)
                            if os.path.isfile(_fpath) and _file_size_accurate(_fpath) == 0:
                                # Find a blob whose size matches what this file should be.
                                # For model.bin specifically we know it must be > 100 MB.
                                _candidate = None
                                if _fname == "model.bin":
                                    # Pick the largest blob (the model weights)
                                    _candidate = max(
                                        (_p for _p in _blob_by_size.values() if _blob_by_size and _file_size_accurate(_p) > 100_000_000),
                                        key=_file_size_accurate, default=None
                                    )
                                else:
                                    # For small files, match by checking refs/ pointer
                                    _refs_path = os.path.join(_wx_model_dir, "refs")
                                    # Try to find the blob hash from the pointer file in the snapshot
                                    # huggingface stores pointer files — the snapshot file IS the blob via hardlink
                                    # We can't rely on content matching, so skip non-model files
                                    pass
                                if _candidate:
                                    try:
                                        _shutil.copy2(_candidate, _fpath)
                                        log_step("DOWNLOAD", f"Repaired 0-byte {_fname} by copying blob ({_file_size_accurate(_fpath):,} bytes)")
                                    except Exception as _e:
                                        log_step("DOWNLOAD", f"Failed to repair {_fname}: {_e}")

                # Final integrity check
                _model_bin_size = 0
                if os.path.isdir(_snap_root):
                    for _snap in os.listdir(_snap_root):
                        _mb = os.path.join(_snap_root, _snap, "model.bin")
                        if os.path.isfile(_mb):
                            _model_bin_size = _file_size_accurate(_mb)
                            break
                log_step("DOWNLOAD", f"whisperx download complete. model.bin size={_model_bin_size:,} bytes")
                if _model_bin_size < 100_000_000:
                    log_step("DOWNLOAD", f"WARNING: model.bin still too small ({_model_bin_size:,} bytes) after repair attempt")
                    yield send("error", "whisperx", f"Download incomplete — model.bin is {_model_bin_size:,} bytes (expected ~1.5 GB). Try again.")
                else:
                    yield send("done", "whisperx", "WhisperX medium model ready")
            except ImportError as e:
                yield send("done", "whisperx", f"WhisperX not installed — {e}", error=True)
            except Exception as e:
                yield send("error", "whisperx", f"Download failed: {e}")
        else:
            yield send("done", "whisperx", "WhisperX medium model already downloaded")

        await asyncio.sleep(0.05)

        # ── Demucs ──
        if not status["demucs"]:
            yield send("progress", "demucs", "Downloading Demucs vocal separation model (~80 MB)…", percent=0)
            await asyncio.sleep(0.05)
            try:
                DEMUCS_TOTAL_BYTES = 85_000_000
                _torch_checkpoints = os.path.expanduser("~/.cache/torch/hub/checkpoints")

                def _download_demucs():
                    from demucs.pretrained import get_model
                    get_model("htdemucs")

                loop = asyncio.get_event_loop()
                fut = loop.run_in_executor(None, _download_demucs)
                while not fut.done():
                    await asyncio.sleep(2.0)
                    downloaded = _dir_bytes_accurate(_torch_checkpoints)
                    pct = min(99, int(downloaded * 100 / DEMUCS_TOTAL_BYTES))
                    mb_done = downloaded / 1_000_000
                    mb_total = DEMUCS_TOTAL_BYTES / 1_000_000
                    yield send("progress", "demucs",
                               f"Downloading… {mb_done:.0f} / {mb_total:.0f} MB",
                               percent=pct)
                await fut

                yield send("done", "demucs", "Demucs model ready")
            except ImportError:
                yield send("done", "demucs", "Demucs not installed — vocals must be provided manually", error=True)
            except Exception as e:
                yield send("error", "demucs", f"Download failed: {e}")
        else:
            yield send("done", "demucs", "Demucs model already downloaded")

        await asyncio.sleep(0.05)

        # ── wav2vec2 alignment model ──
        if not status.get("wav2vec2"):
            yield send("progress", "wav2vec2", "Downloading wav2vec2 alignment model (~360 MB)…", percent=0)
            await asyncio.sleep(0.05)
            try:
                WAV2VEC2_TOTAL_BYTES = 360_000_000  # ~360 MB
                _torch_checkpoints = os.path.expanduser("~/.cache/torch/hub/checkpoints")
                _bytes_before_wv = _dir_bytes_accurate(_torch_checkpoints)

                def _download_wav2vec2():
                    import whisperx
                    align_model, align_metadata = whisperx.load_align_model(language_code="en", device="cpu")
                    del align_model, align_metadata

                loop = asyncio.get_event_loop()
                fut = loop.run_in_executor(None, _download_wav2vec2)
                while not fut.done():
                    await asyncio.sleep(2.0)
                    downloaded = max(0, _dir_bytes_accurate(_torch_checkpoints) - _bytes_before_wv)
                    pct = min(99, int(downloaded * 100 / WAV2VEC2_TOTAL_BYTES))
                    mb_done = downloaded / 1_000_000
                    mb_total = WAV2VEC2_TOTAL_BYTES / 1_000_000
                    yield send("progress", "wav2vec2",
                               f"Downloading… {mb_done:.0f} / {mb_total:.0f} MB",
                               percent=pct)
                await fut

                yield send("done", "wav2vec2", "Alignment model ready")
            except ImportError:
                yield send("done", "wav2vec2", "WhisperX not installed — skipping", error=True)
            except Exception as e:
                yield send("error", "wav2vec2", f"Download failed: {e}")
        else:
            yield send("done", "wav2vec2", "Alignment model already downloaded")

        await asyncio.sleep(0.05)
        import json
        yield f"data: {json.dumps({'type': 'complete'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ────────────────────────────────────────────────────────────
# Session management
# ────────────────────────────────────────────────────────────
@app.get("/api/sessions")
async def list_all_sessions():
    """List all sessions (for the launcher page)."""
    result = []
    for sid, s in sessions.items():
        has_result = s.get("result") is not None
        result.append({
            "id": sid,
            "artist": s.get("artist", "Unknown"),
            "title": s.get("title", "Untitled"),
            # If a result exists, always surface as generated regardless of raw status
            "status": "generated" if has_result else s.get("status", "unknown"),
            "created_at": s.get("created_at", 0),
            "has_result": has_result,
        })
    # Sort newest first
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return {"status": "ok", "sessions": result}


@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    """Delete a session and its files."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Remove session file
    session_file = os.path.join(SESSIONS_DIR, f"{session_id}.json")
    if os.path.exists(session_file):
        os.remove(session_file)

    # Remove upload directory if it exists
    upload_dir = os.path.join(UPLOAD_DIR, session_id)
    if os.path.isdir(upload_dir):
        shutil.rmtree(upload_dir, ignore_errors=True)

    # Remove generated files tracked in result
    result = session.get("result", {})
    tracked = set()
    for key in ("txt_file", "midi_file", "summary_file", "corrected_txt_file", "cleaned_vocal_file"):
        fname = result.get(key) if isinstance(result, dict) else None
        if fname:
            tracked.add(fname)
    # cleaned_vocal_path may be absolute; remove by basename from downloads
    cleaned_path = result.get("cleaned_vocal_path") if isinstance(result, dict) else None
    if cleaned_path:
        tracked.add(os.path.basename(cleaned_path))
    # Also remove all filenames accumulated across multiple generation runs
    for fname in session.get("generated_files", []):
        tracked.add(fname)
    for fname in tracked:
        fpath = os.path.join(DOWNLOADS_DIR, fname)
        if os.path.exists(fpath):
            os.remove(fpath)

    # Remove session-owned patched vocal files living under backend/sessions
    # (current vocal_audio plus any historical patched files tracked on session)
    audio_candidates = [
        session.get("vocal_audio"),
        session.get("original_demucs_vocal"),
    ]
    for fpath in session.get("patched_vocal_files", []):
        audio_candidates.append(fpath)

    for fpath in audio_candidates:
        if not fpath:
            continue
        try:
            abs_path = os.path.abspath(fpath)
            if abs_path.startswith(os.path.abspath(SESSIONS_DIR) + os.sep) and os.path.exists(abs_path):
                os.remove(abs_path)
        except OSError:
            pass

    # Remove orphaned downloads: files prefixed with session_id or session_id[:8]
    # Covers mic_trail_*, mic_audio_* (prefixed with session_id[:8])
    # Also covers song_*, pitches_*, summary_* from prior generation runs
    import glob as _glob
    short_id = session_id[:8]
    for pattern in (
        f"mic_trail_{short_id}_*",
        f"mic_audio_{short_id}_*",
        f"comparison_ms_ref_{short_id}_*",
    ):
        for fpath in _glob.glob(os.path.join(DOWNLOADS_DIR, pattern)):
            try:
                os.remove(fpath)
            except OSError:
                pass

    del sessions[session_id]

    # Sweep orphaned patched vocals from previous versions that did not track them.
    referenced = set()
    for s in sessions.values():
        for key in ("vocal_audio", "original_demucs_vocal"):
            p = s.get(key)
            if p:
                referenced.add(os.path.abspath(p))
        for p in s.get("patched_vocal_files", []):
            if p:
                referenced.add(os.path.abspath(p))

    for name in os.listdir(SESSIONS_DIR):
        if not name.startswith("vocal_patched_"):
            continue
        p = os.path.abspath(os.path.join(SESSIONS_DIR, name))
        if p in referenced:
            continue
        try:
            os.remove(p)
        except OSError:
            pass

    log_step("SESSION", f"Deleted session {session_id}")
    return {"status": "ok"}


@app.post("/api/resume/{session_id}")
async def resume_specific_session(session_id: str):
    """Resume an existing session by ID (opens it without cloning)."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    vocal = session.get("vocal_audio")
    original = session.get("original_audio")
    any_audio = vocal or original
    if not any_audio or not os.path.exists(any_audio):
        raise HTTPException(status_code=404, detail="Audio files no longer exist")

    has_vocals = vocal is not None and os.path.exists(vocal)
    has_original = original is not None and os.path.exists(original)
    instrumental = session.get("instrumental_audio")
    has_instrumental = instrumental is not None and os.path.exists(instrumental)

    lyrics = session.get("lyrics", "")
    syllable_count = 0
    line_count = 0
    if session.get("parsed_lyrics"):
        syllable_count = sum(len(line) for line in session["parsed_lyrics"])
        line_count = len(session["parsed_lyrics"])

    # Use best available filename for display
    display_file = vocal if has_vocals else original
    display_filename = os.path.basename(display_file)

    return JSONResponse(safe_json({
        "status": "ok",
        "session_id": session_id,
        "filename": display_filename,
        "has_vocals": has_vocals,
        "vocals_filename": os.path.basename(vocal) if has_vocals else None,
        "has_original": has_original,
        "has_instrumental": has_instrumental,
        "instrumental_filename": os.path.basename(instrumental) if has_instrumental else None,
        "has_lyrics": bool(lyrics),
        "lyrics": lyrics,
        "artist": session.get("artist", "Unknown Artist"),
        "title": session.get("title", "Unknown Song"),
        "language": session.get("language", "en"),
        "genre": session.get("genre", ""),
        "year": session.get("year", ""),
        "edition": session.get("edition", ""),
        "creator": session.get("creator", ""),
        "vocals_header": session.get("vocals_header", ""),
        "instrumental_header": session.get("instrumental_header", ""),
        "syllable_count": syllable_count,
        "line_count": line_count,
        "has_result": session.get("result") is not None,
        "result": session.get("result"),
    }))


def _normalize_audio_to_mp3(file_path: str, orig_filename: str) -> tuple[str, str]:
    """Ensure audio file is a 44100 Hz MP3. Converts in-place if needed.
    Returns (final_file_path, final_filename)."""
    try:
        orig_ext = os.path.splitext(orig_filename)[1].lower()
        needs_convert = orig_ext != '.mp3'

        if not needs_convert:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_streams", "-print_format", "json", file_path],
                capture_output=True, text=True, timeout=30
            )
            probe_data = json.loads(probe.stdout)
            sample_rate = int(probe_data["streams"][0].get("sample_rate", 44100))
            needs_convert = sample_rate != 44100
            if needs_convert:
                log_step("UPLOAD", f"MP3 at {sample_rate}Hz — re-encoding to 44100Hz")

        if needs_convert:
            mp3_filename = os.path.splitext(orig_filename)[0] + ".mp3"
            mp3_path = os.path.join(os.path.dirname(file_path), mp3_filename)
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", file_path, "-ar", "44100", "-ac", "2",
                 "-codec:a", "libmp3lame", "-q:a", "2", mp3_path],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0 and os.path.exists(mp3_path):
                if orig_ext != '.mp3':
                    os.remove(file_path)
                else:
                    os.replace(mp3_path, file_path)
                    mp3_path = file_path
                    mp3_filename = orig_filename
                log_step("UPLOAD", f"Converted → 44100Hz MP3: {mp3_filename}")
                return mp3_path, mp3_filename
            else:
                log_step("UPLOAD", f"Conversion failed, keeping original: {result.stderr[:200]}")
    except Exception as _e:
        log_step("UPLOAD", f"Audio normalization skipped: {_e}")
    return file_path, orig_filename


@app.post("/api/import")
async def import_ultrastar(
    txt_file: UploadFile = File(...),
    audio_file: UploadFile = File(None),
    vocal_file: UploadFile = File(None),
):
    """Import an existing Ultrastar song (.txt + optional audio files) into a new session.

    Accepts:
        txt_file: Required — Ultrastar .txt with notes
        audio_file: Optional — full mix audio
        vocal_file: Optional — isolated vocals audio
    At least one audio file must be provided.

    Parses the Ultrastar .txt to extract notes, BPM, GAP, and metadata.
    Creates a session with a pre-populated result so the editor opens directly.
    """
    from services.reference_comparison import parse_ultrastar_file
    import librosa

    if not audio_file and not vocal_file:
        raise HTTPException(status_code=400, detail="At least one audio file is required (mix or vocals)")

    # Read .txt content
    txt_content = (await txt_file.read()).decode("utf-8", errors="replace")
    parsed = parse_ultrastar_file(txt_content)

    if not parsed["notes"]:
        # Give a specific error depending on what's missing
        has_headers = bool(parsed["headers"])
        has_bpm = parsed["bpm"] > 0
        if not has_headers:
            raise HTTPException(status_code=400, detail="Not a valid Ultrastar file — no #TITLE, #BPM or other headers found")
        elif not has_bpm:
            raise HTTPException(status_code=400, detail="Ultrastar file has no #BPM header — cannot parse notes")
        else:
            raise HTTPException(status_code=400, detail="No notes found in Ultrastar file (expected lines starting with : or * or F:)")

    bpm = parsed["bpm"]
    gap_ms = int(parsed["gap"])
    headers = parsed["headers"]

    artist = headers.get("ARTIST", "Unknown Artist")
    title = headers.get("TITLE", "Unknown Song")
    language = headers.get("LANGUAGE", "en")
    genre = headers.get("GENRE", "")
    year = headers.get("YEAR", "")
    edition = headers.get("EDITION", "")

    # Save audio files
    session_id = str(uuid.uuid4())[:8]
    session_dir = os.path.join(UPLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    original_path = None
    vocal_path = None
    duration_path = None  # whichever audio we use to measure duration

    if audio_file:
        original_path = os.path.join(session_dir, audio_file.filename)
        audio_bytes = await audio_file.read()
        with open(original_path, "wb") as f:
            f.write(audio_bytes)
        original_path, _ = _normalize_audio_to_mp3(original_path, audio_file.filename)
        duration_path = original_path

    if vocal_file:
        vocal_path = os.path.join(session_dir, vocal_file.filename)
        vocal_bytes = await vocal_file.read()
        with open(vocal_path, "wb") as f:
            f.write(vocal_bytes)
        vocal_path, _ = _normalize_audio_to_mp3(vocal_path, vocal_file.filename)
        if not duration_path:
            duration_path = vocal_path

    # Get audio duration
    try:
        audio_duration = librosa.get_duration(filename=duration_path)
    except Exception:
        audio_duration = 0.0

    # Build syllable_timings from parsed notes (convert beats to seconds)
    syllable_timings = []
    for note in parsed["notes"]:
        start_sec = gap_ms / 1000.0 + note["start_beat"] * 15.0 / bpm
        end_sec = gap_ms / 1000.0 + (note["start_beat"] + note["duration"]) * 15.0 / bpm
        syllable_timings.append({
            "syllable": note["syllable"],
            "start": round(start_sec, 4),
            "end": round(end_sec, 4),
            "midi_note": note["pitch"],
            "confidence": 1.0,
            "method": "imported",
            "is_rap": note.get("is_rap", False),
        })

    # Save the .txt to downloads
    timestamp = int(time.time())
    txt_filename = f"song_{timestamp}.txt"
    txt_path = os.path.join(DOWNLOADS_DIR, txt_filename)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt_content)

    # Create session with result — store None for missing files, not aliases
    session = {
        "id": session_id,
        "original_audio": original_path,
        "vocal_audio": vocal_path,
        "lyrics": "",
        "artist": artist,
        "title": title,
        "language": language,
        "genre": genre,
        "year": year,
        "edition": edition,
        "status": "generated",
        "created_at": time.time(),
        "imported": True,
        "result": {
            "txt_file": txt_filename,
            "midi_file": None,
            "summary_file": None,
            "bpm": bpm,
            "gap_ms": gap_ms,
            "syllable_count": len(syllable_timings),
            "audio_duration": audio_duration,
            "pitch_method": "imported",
            "alignment_method": "imported",
            "elapsed_seconds": 0,
            "syllable_timings": syllable_timings,
            "ultrastar_content": txt_content,
        },
    }
    sessions[session_id] = session
    # Auto-set vocals_header from uploaded vocal file (archive name computed in _update_txt_asset_headers)
    if vocal_path:
        session["vocals_header"] = os.path.basename(vocal_path)
    _update_txt_asset_headers(session)
    save_session(session_id)

    has_vocals = vocal_path is not None
    has_original = original_path is not None
    # Use the first available filename for display
    display_filename = (audio_file.filename if audio_file else vocal_file.filename)

    log_step("IMPORT", f"Imported '{artist} - {title}' as session {session_id} "
             f"({len(syllable_timings)} notes, BPM={bpm}, GAP={gap_ms}ms, "
             f"vocals={'yes' if has_vocals else 'no'}, mix={'yes' if has_original else 'no'})")

    return {
        "status": "ok",
        "session_id": session_id,
        "filename": display_filename,
        "artist": artist,
        "title": title,
        "language": language,
        "syllable_count": len(syllable_timings),
        "line_count": len(parsed["breaks"]) + 1,
        "bpm": bpm,
        "gap_ms": gap_ms,
        "has_lyrics": True,
        "lyrics": "",
        "has_result": True,
        "has_vocals": has_vocals,
        "has_original": has_original,
        "result": session["result"],
    }


# ────────────────────────────────────────────────────────────
# Step 1: Upload & Vocal Extraction
# ────────────────────────────────────────────────────────────
@app.post("/api/new-session")
async def new_session():
    """Create a blank session (no audio). Used when uploading vocals without a mix."""
    session_id = str(uuid.uuid4())[:8]
    session_dir = os.path.join(UPLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    sessions[session_id] = {
        "id": session_id,
        "original_audio": None,
        "vocal_audio": None,
        "lyrics": None,
        "status": "new",
        "created_at": time.time(),
    }
    save_session(session_id)
    log_step("UPLOAD", f"Session {session_id}: created blank session")
    return {"status": "ok", "session_id": session_id}


@app.post("/api/upload")
async def upload_audio(audio: UploadFile = File(...)):
    """Upload an audio file (MP3/WAV/M4A etc). Converts to 44100Hz MP3 for universal compatibility."""
    session_id = str(uuid.uuid4())[:8]

    session_dir = os.path.join(UPLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    orig_filename = audio.filename
    file_path = os.path.join(session_dir, orig_filename)
    with open(file_path, "wb") as f:
        content = await audio.read()
        f.write(content)

    file_path, orig_filename = _normalize_audio_to_mp3(file_path, orig_filename)

    sessions[session_id] = {
        "id": session_id,
        "original_audio": file_path,
        "vocal_audio": None,
        "lyrics": None,
        "status": "uploaded",
        "created_at": time.time(),
    }

    log_step("UPLOAD", f"Session {session_id}: {audio.filename} → {orig_filename} ({len(content)} bytes)")
    save_session(session_id)

    return {
        "status": "ok",
        "session_id": session_id,
        "filename": orig_filename,
        "size": len(content),
    }


@app.post("/api/cancel-extract/{session_id}")
async def cancel_extract(session_id: str):
    """Signal the vocal extraction to abort (HTTP-level; Demucs runs to completion internally)."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session["extract_cancelled"] = True
    return {"status": "ok", "message": "Extraction cancellation requested"}


@app.post("/api/extract-vocals/{session_id}")
async def extract_vocals(session_id: str):
    """Run Demucs vocal separation on uploaded audio."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    from services.vocal_separation import separate_vocals, DEMUCS_AVAILABLE
    
    if not DEMUCS_AVAILABLE:
        raise ServiceError("Demucs not installed", "Install with: pip install demucs", 503)
    
    session["extract_cancelled"] = False
    try:
        session["status"] = "extracting_vocals"
        output_dir = os.path.join(UPLOAD_DIR, session_id)
        audio_path = session["original_audio"]

        # Run Demucs in a thread so the event loop (and other requests) stay responsive
        import asyncio
        loop = asyncio.get_event_loop()
        vocal_path, instrumental_path = await loop.run_in_executor(None, separate_vocals, audio_path, output_dir)

        if session.get("extract_cancelled"):
            raise HTTPException(status_code=499, detail="Extraction cancelled")

        session["vocal_audio"] = vocal_path
        if instrumental_path:
            session["instrumental_audio"] = instrumental_path
        if not session.get("vocals_header"):
            session["vocals_header"] = os.path.basename(vocal_path)
        if instrumental_path and not session.get("instrumental_header"):
            session["instrumental_header"] = os.path.basename(instrumental_path)
        session["status"] = "vocals_extracted"
        _update_txt_asset_headers(session)
        save_session(session_id)
        
        return {
            "status": "ok",
            "session_id": session_id,
            "vocal_url": f"/api/preview-audio/{session_id}/vocals",
        }
    except HTTPException:
        raise
    except Exception as e:
        session["status"] = "extraction_failed"
        save_session(session_id)
        raise ServiceError("Vocal extraction failed", str(e))


@app.get("/api/extract-vocals-stream/{session_id}")
async def extract_vocals_stream(session_id: str):
    """SSE stream for vocal extraction — emits phase messages and elapsed-time heartbeats."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    from services.vocal_separation import separate_vocals, DEMUCS_AVAILABLE
    import asyncio

    def _send(phase: str, message: str = "", **kwargs) -> str:
        data = {"phase": phase, "message": message, **kwargs}
        return f"data: {json.dumps(data)}\n\n"

    async def event_generator():
        if not DEMUCS_AVAILABLE:
            yield _send("error", "Demucs not installed. Install with: pip install demucs")
            return

        if not session.get("original_audio"):
            yield _send("error", "No audio file uploaded")
            return

        yield _send("loading", "Loading Demucs model…")
        await asyncio.sleep(0.1)

        session["extract_cancelled"] = False
        session["status"] = "extracting_vocals"

        loop = asyncio.get_event_loop()
        output_dir = os.path.join(UPLOAD_DIR, session_id)
        audio_path = session["original_audio"]

        # Launch demucs in a thread pool so the event loop stays responsive
        executor_future = loop.run_in_executor(None, separate_vocals, audio_path, output_dir)

        yield _send("separating", "Separating vocals — may take 1–5 minutes depending on song length…")

        # Send heartbeats while waiting
        start = loop.time()
        while not executor_future.done():
            if session.get("extract_cancelled"):
                executor_future.cancel()
                yield _send("cancelled", "Extraction cancelled")
                return
            elapsed = int(loop.time() - start)
            yield _send("heartbeat", "", elapsed=elapsed)
            try:
                await asyncio.wait_for(asyncio.shield(executor_future), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            except Exception:
                break

        if session.get("extract_cancelled") or executor_future.cancelled():
            yield _send("cancelled", "Extraction cancelled")
            return

        exc = executor_future.exception() if not executor_future.cancelled() else RuntimeError("cancelled")
        if exc:
            session["status"] = "extraction_failed"
            save_session(session_id)
            yield _send("error", str(exc))
            return

        vocal_path, instrumental_path = executor_future.result()
        session["vocal_audio"] = vocal_path
        if instrumental_path:
            session["instrumental_audio"] = instrumental_path
        if not session.get("vocals_header"):
            session["vocals_header"] = os.path.basename(vocal_path)
        if instrumental_path and not session.get("instrumental_header"):
            session["instrumental_header"] = os.path.basename(instrumental_path)
        session["status"] = "vocals_extracted"
        _update_txt_asset_headers(session)
        save_session(session_id)
        log_step("SEPARATE", f"Session {session_id}: vocals extracted via SSE stream")
        yield _send("done", "Vocals extracted successfully!", vocal_url=f"/api/preview-audio/{session_id}/vocals")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/upload-vocals/{session_id}")
async def upload_corrected_vocals(session_id: str, vocals: UploadFile = File(...)):
    """Upload manually corrected vocals (skip or replace Demucs output)."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session_dir = os.path.join(UPLOAD_DIR, session_id)
    orig_vocal_filename = f"vocals_{vocals.filename}"
    vocal_path = os.path.join(session_dir, orig_vocal_filename)

    with open(vocal_path, "wb") as f:
        content = await vocals.read()
        f.write(content)

    vocal_path, orig_vocal_filename = _normalize_audio_to_mp3(vocal_path, orig_vocal_filename)

    session["vocal_audio"] = vocal_path
    session["status"] = "vocals_extracted"
    if not session.get("vocals_header"):
        session["vocals_header"] = os.path.basename(vocal_path)
    _update_txt_asset_headers(session)
    log_step("UPLOAD", f"Session {session_id}: uploaded corrected vocals ({len(content)} bytes)")
    save_session(session_id)
    
    return {"status": "ok", "session_id": session_id}


@app.post("/api/upload-mix/{session_id}")
async def upload_mix_audio(session_id: str, audio: UploadFile = File(...)):
    """Upload or replace the full mix audio for an existing session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session_dir = os.path.join(UPLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    orig_filename = audio.filename
    file_path = os.path.join(session_dir, orig_filename)

    with open(file_path, "wb") as f:
        content = await audio.read()
        f.write(content)

    file_path, orig_filename = _normalize_audio_to_mp3(file_path, orig_filename)

    session["original_audio"] = file_path
    session["filename"] = orig_filename
    _update_txt_asset_headers(session)
    log_step("UPLOAD", f"Session {session_id}: replaced mix audio with {orig_filename} ({len(content)} bytes)")
    save_session(session_id)

    return {"status": "ok", "session_id": session_id, "filename": orig_filename}


@app.delete("/api/delete-audio/{session_id}/{audio_type}")
async def delete_audio(session_id: str, audio_type: str):
    """Delete an audio file (original or vocals) from a session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if audio_type == "original":
        path = session.get("original_audio")
        if path and os.path.exists(path):
            os.remove(path)
        session["original_audio"] = None
        log_step("DELETE", f"Session {session_id}: deleted original audio")
    elif audio_type == "vocals":
        path = session.get("vocal_audio")
        if path and os.path.exists(path):
            os.remove(path)
        session["vocal_audio"] = None
        session["vocals_header"] = ""
        session["status"] = "uploaded" if session.get("original_audio") else "created"
        log_step("DELETE", f"Session {session_id}: deleted vocals")
    else:
        raise HTTPException(status_code=400, detail="Invalid audio type. Use 'original' or 'vocals'.")

    _update_txt_asset_headers(session)
    save_session(session_id)
    return {
        "status": "ok",
        "has_original": session.get("original_audio") is not None,
        "has_vocals": session.get("vocal_audio") is not None,
    }


@app.get("/api/preview-audio/{session_id}/{audio_type}")
async def preview_audio(session_id: str, audio_type: str, request: Request):
    """Stream audio for preview with range request support for seeking."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if audio_type == "original":
        path = session.get("original_audio")
    elif audio_type == "vocals":
        path = session.get("vocal_audio")
    elif audio_type == "instrumental":
        path = session.get("instrumental_audio")
    elif audio_type == "cleaned":
        # Cleaned audio from cleanup segments
        result = session.get("result")
        path = result.get("cleaned_vocal_path") if result else None
    elif audio_type == "demucs":
        # Original demucs vocal before any splice edits
        path = session.get("original_demucs_vocal") or session.get("vocal_audio")
    else:
        raise HTTPException(status_code=400, detail="Invalid audio type")
    
    if not path or not os.path.exists(path):
        # Try to find the file with different extension (e.g. .wav vs .mp3)
        if path:
            base = os.path.splitext(path)[0]
            for ext in ['.mp3', '.wav', '.flac', '.ogg']:
                alt = base + ext
                if os.path.exists(alt):
                    path = alt
                    # Update session so future requests use the correct path
                    if audio_type == "vocals":
                        session["vocal_audio"] = path
                    elif audio_type == "original":
                        session["original_audio"] = path
                    elif audio_type == "instrumental":
                        session["instrumental_audio"] = path
                    log_step("PREVIEW", f"Found {audio_type} at alternate path: {path}")
                    break
            else:
                raise HTTPException(status_code=404, detail=f"Audio file not found: {os.path.basename(path)}")
        else:
            raise HTTPException(status_code=404, detail="Audio file not found")
    
    file_size = os.path.getsize(path)
    ext = os.path.splitext(path)[1].lower()
    content_type = {
        '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
        '.flac': 'audio/flac', '.ogg': 'audio/ogg',
        '.m4a': 'audio/mp4', '.aac': 'audio/aac',
    }.get(ext, 'application/octet-stream')
    
    # Handle range requests for seeking support
    range_header = request.headers.get('range')
    if range_header:
        # Parse "bytes=start-end"
        range_str = range_header.replace('bytes=', '')
        parts = range_str.split('-')
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1
        
        def iter_range():
            with open(path, 'rb') as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
        
        return StreamingResponse(
            iter_range(),
            status_code=206,
            headers={
                'Content-Range': f'bytes {start}-{end}/{file_size}',
                'Accept-Ranges': 'bytes',
                'Content-Length': str(length),
                'Content-Type': content_type,
                'Cache-Control': 'no-store',
            }
        )
    
    # No range — return full file with accept-ranges header
    # Build a user-friendly download name from artist/title
    artist = session.get("artist", "").strip()
    title_name = session.get("title", "").strip()
    if artist and title_name:
        base = f"{artist} - {title_name}"
    elif title_name:
        base = title_name
    elif artist:
        base = artist
    else:
        base = "Untitled Song"
    suffix = " [Vocals]" if audio_type == "vocals" else ""
    download_name = base + suffix + os.path.splitext(path)[1]
    
    return FileResponse(path, filename=download_name, headers={'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store'})


# ────────────────────────────────────────────────────────────
# Step 2a: WhisperX ASR Transcription + Forced Alignment
# ────────────────────────────────────────────────────────────
@app.post("/api/cancel-transcribe/{session_id}")
async def cancel_transcribe(session_id: str):
    """Signal the transcription to abort."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session["transcribe_cancelled"] = True
    return {"status": "ok", "message": "Transcription cancellation requested"}


@app.get("/api/transcribe-stream/{session_id}")
async def transcribe_stream(session_id: str, language: str = "en", use_cleaned: bool = False):
    """SSE stream for transcription — keeps connection alive during long Whisper runs."""
    # Normalize full language names to ISO codes (e.g. "English" -> "en")
    _LANG_MAP = {
        "english": "en", "german": "de", "french": "fr", "spanish": "es",
        "italian": "it", "portuguese": "pt", "dutch": "nl", "russian": "ru",
        "japanese": "ja", "chinese": "zh", "korean": "ko", "arabic": "ar",
        "turkish": "tr", "polish": "pl", "swedish": "sv", "norwegian": "no",
        "danish": "da", "finnish": "fi", "czech": "cs", "hungarian": "hu",
        "romanian": "ro", "ukrainian": "uk", "greek": "el", "hebrew": "he",
        "hindi": "hi", "thai": "th", "vietnamese": "vi", "indonesian": "id",
    }
    language = _LANG_MAP.get(language.lower(), language.lower()) if language else "en"

    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    import asyncio
    from starlette.responses import StreamingResponse

    def _send(phase: str, message: str = "", **kwargs) -> str:
        data = {"phase": phase, "message": message, **kwargs}
        return f"data: {json.dumps(data)}\n\n"

    async def event_generator():
        if use_cleaned:
            result = session.get("result") or {}
            audio_path = result.get("cleaned_vocal_path")
            if not audio_path or not os.path.exists(audio_path):
                yield _send("error", "No cleaned audio found. Generate a cleaned preview first.")
                return
        else:
            audio_path = session.get("vocal_audio") or session.get("original_audio")
        if not audio_path or not os.path.exists(audio_path):
            yield _send("error", "No audio file found. Upload audio first.")
            return

        session["transcribe_cancelled"] = False
        yield _send("loading", "Loading Whisper model…")

        loop = asyncio.get_event_loop()

        # Run the existing synchronous transcribe logic in a thread
        result_container = {}

        def _run_transcribe():
            WHISPER_LANG_MAP = {
                "en": "English", "de": "German", "fr": "French",
                "es": "Spanish", "it": "Italian", "pt": "Portuguese",
                "nl": "Dutch", "ja": "Japanese", "ko": "Korean",
                "zh": "Chinese",
            }
            log_step("WHISPER", f"Transcribing {os.path.basename(audio_path)} (lang={language})...")

            # Try WhisperX first
            try:
                import whisperx
                import torch
                device = "cpu"
                compute_type = "int8"
                model_name = "medium"
                log_step("WHISPERX", f"Loading WhisperX model '{model_name}' (device={device})...")
                model = whisperx.load_model(model_name, device, compute_type=compute_type)
                log_step("WHISPERX", "Loading audio...")
                audio = whisperx.load_audio(audio_path)
                log_step("WHISPERX", "Running transcription...")
                transcribe_result = model.transcribe(audio, batch_size=4, language=language if language else None)
                segments = transcribe_result.get("segments", [])
                lines = [s["text"].strip() for s in segments if s.get("text", "").strip()]
                all_words = []
                all_chars = []
                try:
                    align_model, align_metadata = whisperx.load_align_model(
                        language_code=transcribe_result.get("language", language or "en"),
                        device=device
                    )
                    aligned = whisperx.align(segments, align_model, align_metadata, audio, device, return_char_alignments=True)
                    for w in aligned.get("word_segments", []):
                        all_words.append({"word": w.get("word","").strip(), "start": round(w.get("start",0),4), "end": round(w.get("end",0),4), "score": round(w.get("score",0),4)})
                    for seg in aligned.get("segments", []):
                        for c in seg.get("chars", []):
                            if c.get("char","").strip():
                                all_chars.append({"char": c["char"], "start": round(c.get("start",0),4), "end": round(c.get("end",0),4)})
                except Exception as align_err:
                    log_step("WHISPERX", f"Alignment failed (non-fatal): {align_err}")
                transcribed_text = "\n".join(lines)
                session["whisper_words"] = all_words
                session["whisper_chars"] = all_chars
                session["whisper_method"] = "whisperx"
                save_session(session_id)
                word_count = sum(len(l.split()) for l in lines)
                result_container["result"] = {
                    "text": transcribed_text, "lines": len(lines), "words": word_count,
                    "language": transcribe_result.get("language", language),
                    "language_name": WHISPER_LANG_MAP.get(transcribe_result.get("language", language), language),
                    "model": f"whisperx-medium", "alignment": "wav2vec2", "char_timestamps": len(all_chars),
                }
                return
            except ImportError as e:
                import traceback
                log_step("WHISPER", f"WhisperX ImportError: {e}, falling back to vanilla Whisper...")
                log_step("WHISPER", traceback.format_exc())
            except Exception as e:
                import traceback
                log_step("WHISPERX", f"WhisperX failed: {e}, falling back to vanilla Whisper")
                log_step("WHISPERX", traceback.format_exc())

            # Fallback: vanilla Whisper
            try:
                import whisper
                model_name = "medium"
                log_step("WHISPER", f"Loading Whisper model '{model_name}'...")
                model = whisper.load_model(model_name)
                log_step("WHISPER", "Running transcription...")
                result = model.transcribe(audio_path, language=language, word_timestamps=True)
                lines = []
                all_words = []
                for segment in result.get("segments", []):
                    text = segment.get("text", "").strip()
                    if text:
                        lines.append(text)
                    for w in segment.get("words", []):
                        all_words.append({"word": w.get("word","").strip(), "start": round(w.get("start",0),4), "end": round(w.get("end",0),4)})
                transcribed_text = "\n".join(lines)
                session["whisper_words"] = all_words
                session["whisper_chars"] = []
                session["whisper_method"] = "whisper"
                save_session(session_id)
                word_count = sum(len(l.split()) for l in lines)
                log_step("WHISPER", f"Fallback transcription complete: {len(lines)} lines, {word_count} words")
                result_container["result"] = {
                    "text": transcribed_text, "lines": len(lines), "words": word_count,
                    "language": language,
                    "language_name": WHISPER_LANG_MAP.get(language, language),
                    "model": f"whisper-{model_name}", "alignment": "whisper-native", "char_timestamps": 0,
                }
            except ImportError as e:
                import traceback
                log_step("WHISPER", f"Vanilla Whisper ImportError: {e}")
                log_step("WHISPER", traceback.format_exc())
                result_container["error"] = "Neither WhisperX nor Whisper installed. Run: pip install whisperx"
            except Exception as e:
                import traceback
                log_step("WHISPER", f"Transcription failed: {e}")
                log_step("WHISPER", traceback.format_exc())
                result_container["error"] = f"Transcription failed: {str(e)}"

        executor_future = loop.run_in_executor(None, _run_transcribe)
        yield _send("transcribing", "Transcribing vocals with Whisper…")

        start = loop.time()
        while not executor_future.done():
            if session.get("transcribe_cancelled"):
                yield _send("cancelled", "Transcription cancelled")
                return
            elapsed = int(loop.time() - start)
            yield _send("heartbeat", f"Transcribing… {elapsed}s elapsed")
            await asyncio.sleep(5)

        await executor_future

        if "error" in result_container:
            yield _send("error", result_container["error"])
        else:
            yield _send("done", "Transcription complete", **result_container["result"])

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/transcribe/{session_id}")
def transcribe_audio(session_id: str, language: str = Form("en")):
    """Transcribe vocal audio using WhisperX with phoneme-level forced alignment.
    
    WhisperX provides ~50ms word boundaries (vs ~200ms for vanilla Whisper)
    by running wav2vec2-based forced alignment after initial transcription.
    Falls back to vanilla Whisper if WhisperX is unavailable.
    
    Returns the transcribed text with line breaks at phrase boundaries.
    """
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Find the vocal audio file
    audio_path = session.get("vocal_audio") or session.get("original_audio")
    if not audio_path or not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="No audio file found. Upload audio first.")
    
    # Map short language codes to Whisper language names
    WHISPER_LANG_MAP = {
        "en": "English", "de": "German", "fr": "French",
        "es": "Spanish", "it": "Italian", "pt": "Portuguese",
        "nl": "Dutch", "ja": "Japanese", "ko": "Korean",
        "zh": "Chinese",
    }
    
    log_step("WHISPER", f"Transcribing {os.path.basename(audio_path)} (lang={language})...")
    
    # --- Try WhisperX first (phoneme-level forced alignment) ---
    try:
        import whisperx
        import torch
        
        device = "cpu"  # MPS has limited WhisperX support
        compute_type = "int8"  # Efficient for CPU
        model_name = "medium"
        
        log_step("WHISPERX", f"Loading WhisperX model '{model_name}' (device={device})...")
        model = whisperx.load_model(model_name, device, compute_type=compute_type)
        
        # Load audio at WhisperX's expected sample rate
        log_step("WHISPERX", "Loading audio...")
        audio = whisperx.load_audio(audio_path)
        
        # Step 1: Initial transcription (same quality as vanilla Whisper)
        log_step("WHISPERX", "Running transcription...")
        result = model.transcribe(audio, batch_size=4, language=language)
        
        # Step 2: Forced alignment with wav2vec2 for precise word boundaries
        log_step("WHISPERX", "Running forced alignment (wav2vec2)...")
        align_model, align_metadata = whisperx.load_align_model(
            language_code=language, device=device
        )
        result = whisperx.align(
            result["segments"],
            align_model,
            align_metadata,
            audio,
            device,
            return_char_alignments=True,  # character-level for syllable distribution
        )
        
        # Free alignment model to save memory
        del align_model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        
        # Extract word-level and char-level timestamps
        lines = []
        all_words = []
        all_chars = []
        
        for segment in result.get("segments", []):
            text = segment.get("text", "").strip()
            if text:
                lines.append(text)
            
            # Word-level timestamps (phoneme-aligned, ~50ms accuracy)
            for w in segment.get("words", []):
                word_text = w.get("word", "").strip()
                if not word_text:
                    continue
                start = w.get("start")
                end = w.get("end")
                if start is None or end is None:
                    continue
                all_words.append({
                    "word": word_text,
                    "start": round(start, 4),
                    "end": round(end, 4),
                    "score": round(w.get("score", 0.0), 4),
                })
            
            # Character-level timestamps (for precise syllable splitting)
            for c in segment.get("chars", []):
                char_text = c.get("char", "")
                start = c.get("start")
                end = c.get("end")
                if start is None or end is None:
                    continue
                all_chars.append({
                    "char": char_text,
                    "start": round(start, 4),
                    "end": round(end, 4),
                    "score": round(c.get("score", 0.0), 4),
                })
        
        transcribed_text = "\n".join(lines)
        
        # Save to session
        session["whisper_words"] = all_words
        session["whisper_chars"] = all_chars
        session["whisper_method"] = "whisperx"
        save_session(session_id)
        
        # Debug output
        debug_dir = os.path.join(os.path.dirname(__file__), 'downloads')
        try:
            debug_path = os.path.join(debug_dir, 'whisper_words.txt')
            with open(debug_path, 'w') as f:
                f.write(f"WHISPERX WORD TIMESTAMPS ({len(all_words)} words, {len(all_chars)} chars)\n{'='*60}\n\n")
                f.write("WORDS:\n")
                for w in all_words:
                    dur = w['end'] - w['start']
                    f.write(f"  {w['start']:8.3f} - {w['end']:8.3f}  ({dur:5.3f}s)  score={w['score']:.2f}  {w['word']}\n")
                f.write(f"\nCHARACTERS ({len(all_chars)}):\n")
                for c in all_chars[:200]:  # First 200 chars
                    dur = c['end'] - c['start']
                    f.write(f"  {c['start']:8.3f} - {c['end']:8.3f}  ({dur:5.3f}s)  '{c['char']}'\n")
                if len(all_chars) > 200:
                    f.write(f"  ... and {len(all_chars) - 200} more chars\n")
            log_step("WHISPERX", f"Timestamps saved: {len(all_words)} words, {len(all_chars)} chars")
        except Exception:
            pass
        
        word_count = sum(len(line.split()) for line in lines)
        
        log_step("WHISPERX", f"Transcription complete: {len(lines)} lines, {word_count} words")
        log_step("WHISPERX", f"  Alignment: {len(all_words)} words, {len(all_chars)} char timestamps")
        if all_words:
            avg_score = sum(w['score'] for w in all_words) / len(all_words)
            log_step("WHISPERX", f"  Avg alignment score: {avg_score:.3f}")
        if lines:
            log_step("WHISPERX", f"  First line: '{lines[0][:80]}'")
            log_step("WHISPERX", f"  Last line:  '{lines[-1][:80]}'")
        
        return JSONResponse({
            "text": transcribed_text,
            "lines": len(lines),
            "words": word_count,
            "language": language,
            "language_name": WHISPER_LANG_MAP.get(language, language),
            "model": f"whisperx-{model_name}",
            "alignment": "wav2vec2",
            "char_timestamps": len(all_chars),
        })
    
    except ImportError as e:
        import traceback
        log_step("WHISPER", f"WhisperX ImportError: {e}, falling back to vanilla Whisper...")
        log_step("WHISPER", traceback.format_exc())
    except Exception as e:
        import traceback
        log_step("WHISPERX", f"WhisperX failed: {e}, falling back to vanilla Whisper")
        log_step("WHISPERX", traceback.format_exc())
    
    # --- Fallback: vanilla Whisper ---
    try:
        import whisper
        
        model_name = "medium"
        log_step("WHISPER", f"Loading Whisper model '{model_name}'...")
        model = whisper.load_model(model_name)
        
        log_step("WHISPER", "Running transcription...")
        result = model.transcribe(
            audio_path,
            language=language,
            word_timestamps=True,
        )
        
        lines = []
        all_words = []
        for segment in result.get("segments", []):
            text = segment.get("text", "").strip()
            if text:
                lines.append(text)
            for w in segment.get("words", []):
                all_words.append({
                    "word": w.get("word", "").strip(),
                    "start": round(w.get("start", 0), 4),
                    "end": round(w.get("end", 0), 4),
                })
        
        transcribed_text = "\n".join(lines)
        session["whisper_words"] = all_words
        session["whisper_chars"] = []  # No char-level from vanilla Whisper
        session["whisper_method"] = "whisper"
        save_session(session_id)
        
        # Debug output
        debug_dir = os.path.join(os.path.dirname(__file__), 'downloads')
        try:
            debug_path = os.path.join(debug_dir, 'whisper_words.txt')
            with open(debug_path, 'w') as f:
                f.write(f"WHISPER (fallback) WORD TIMESTAMPS ({len(all_words)} words)\n{'='*60}\n\n")
                for w in all_words:
                    dur = w['end'] - w['start']
                    f.write(f"  {w['start']:8.3f} - {w['end']:8.3f}  ({dur:5.3f}s)  {w['word']}\n")
            log_step("WHISPER", f"Word timestamps saved: {len(all_words)} words")
        except Exception:
            pass
        
        word_count = sum(len(line.split()) for line in lines)
        log_step("WHISPER", f"Fallback transcription complete: {len(lines)} lines, {word_count} words")
        
        return JSONResponse({
            "text": transcribed_text,
            "lines": len(lines),
            "words": word_count,
            "language": language,
            "language_name": WHISPER_LANG_MAP.get(language, language),
            "model": f"whisper-{model_name}",
            "alignment": "whisper-native",
            "char_timestamps": 0,
        })
        
    except ImportError as e:
        import traceback
        log_step("WHISPER", f"Vanilla Whisper ImportError: {e}")
        log_step("WHISPER", traceback.format_exc())
        raise HTTPException(status_code=500, detail="Neither WhisperX nor Whisper installed. Run: pip install whisperx")
    except Exception as e:
        import traceback
        log_step("WHISPER", f"Transcription failed: {e}")
        log_step("WHISPER", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


# ────────────────────────────────────────────────────────────
# Step 2b: Lyrics Input
# ────────────────────────────────────────────────────────────
@app.post("/api/lyrics/{session_id}")
async def submit_lyrics(
    session_id: str,
    lyrics: str = Form(...),
    artist: str = Form("Unknown Artist"),
    title: str = Form("Unknown Song"),
    language: str = Form("en"),
):
    """Submit lyrics and metadata for processing."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    from services.alignment_whisper import parse_lyrics
    
    parsed = parse_lyrics(lyrics)
    flat_count = sum(len(line) for line in parsed)
    
    session["lyrics"] = lyrics
    session["artist"] = artist
    session["title"] = title
    session["language"] = language
    session["parsed_lyrics"] = parsed
    session["status"] = "lyrics_submitted"
    save_session(session_id)

    # If a .txt already exists, update its headers to reflect new artist/title
    result = session.get("result")
    if result:
        import re as _re
        for key in ["corrected_txt_file", "txt_file"]:
            fname = result.get(key)
            if not fname:
                continue
            path = os.path.join(DOWNLOADS_DIR, fname)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                _orig = session.get('original_audio') or session.get('vocal_audio') or ''
                _ext = os.path.splitext(_orig)[1] or '.mp3'
                content = _re.sub(r"^#TITLE:.*$", f"#TITLE:{title}", content, count=1, flags=_re.MULTILINE)
                content = _re.sub(r"^#ARTIST:.*$", f"#ARTIST:{artist}", content, count=1, flags=_re.MULTILINE)
                content = _re.sub(r"^#MP3:.*$", f"#MP3:{artist} - {title}{_ext}", content, count=1, flags=_re.MULTILINE)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                log_step("LYRICS", f"Updated headers in {fname}")
            except Exception as e:
                log_step("LYRICS", f"Failed to update {fname}: {e}")
    
    log_step("LYRICS", f"Session {session_id}: {flat_count} syllables, {len(parsed)} lines")
    
    return {
        "status": "ok",
        "session_id": session_id,
        "syllable_count": flat_count,
        "line_count": len(parsed),
        "preview": [
            {"line": i + 1, "syllables": [s["text"] for s in line]}
            for i, line in enumerate(parsed)
        ],
    }


@app.post("/api/hyphenate")
async def hyphenate_lyrics(
    lyrics: str = Form(...),
    language: str = Form("en"),
):
    """Auto-hyphenate plain lyrics using pyphen."""
    from services.hyphenation import hyphenate_lyrics as do_hyphenate, PYPHEN_AVAILABLE
    
    result = do_hyphenate(lyrics, language)
    return {
        "status": "ok",
        "pyphen_available": PYPHEN_AVAILABLE,
        **result,
    }


# ────────────────────────────────────────────────────────────
# Test data endpoints (development mode)
# ────────────────────────────────────────────────────────────
@app.get("/api/test-lyrics")
async def get_test_lyrics():
    """Load test lyrics from frontendTest/lyrics.txt."""
    project_root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(project_root, "frontendTest", "lyrics.txt")
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Test lyrics not found")
    
    with open(path, "r") as f:
        return {"lyrics": f.read()}


@app.get("/api/test-vocal")
async def get_test_vocal():
    """Serve test vocal audio from frontendTest/test_vocal.wav."""
    project_root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(project_root, "frontendTest", "test_vocal.wav")
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Test vocal not found")
    
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/resume-last")
async def resume_last_session():
    """Create a new session cloned from the most recent one.
    
    Reuses audio files, whisper word timestamps, lyrics, and metadata
    so you can skip upload + transcription when re-generating.
    """
    if not sessions:
        raise HTTPException(status_code=404, detail="No previous sessions found")
    
    # Find the most recent session
    last = max(sessions.values(), key=lambda s: s.get("created_at", 0))
    
    # Verify audio still exists
    vocal = last.get("vocal_audio")
    original = last.get("original_audio")
    if not vocal or not os.path.exists(vocal):
        raise HTTPException(status_code=404, detail="Last session's audio files no longer exist")
    
    # Create new session reusing the same files
    session_id = str(uuid.uuid4())[:8]
    new_session = {
        "id": session_id,
        "original_audio": original,
        "vocal_audio": vocal,
        "lyrics": last.get("lyrics"),
        "artist": last.get("artist", "Unknown Artist"),
        "title": last.get("title", "Unknown Song"),
        "language": last.get("language", "en"),
        "whisper_words": last.get("whisper_words", []),
        "whisper_chars": last.get("whisper_chars", []),
        "whisper_method": last.get("whisper_method", "whisper"),
        "parsed_lyrics": last.get("parsed_lyrics"),
        "reference_content": last.get("reference_content"),
        "reference_filename": last.get("reference_filename"),
        "result": last.get("result"),  # carry over generation result
        "status": "generated" if last.get("result") else ("lyrics_submitted" if last.get("lyrics") else "vocals_extracted"),
        "created_at": time.time(),
    }
    sessions[session_id] = new_session
    save_session(session_id)

    lyrics = last.get("lyrics", "")
    syllable_count = 0
    line_count = 0
    if new_session.get("parsed_lyrics"):
        syllable_count = sum(len(line) for line in new_session["parsed_lyrics"])
        line_count = len(new_session["parsed_lyrics"])
    
    log_step("RESUME", f"New session {session_id} from {last['id']} "
             f"(vocals={os.path.basename(vocal)}, "
             f"{len(new_session.get('whisper_words', []))} whisper words, "
             f"{syllable_count} syllables)")
    
    # Build reference info if available
    reference_info = None
    ref_content = new_session.get("reference_content")
    ref_filename = new_session.get("reference_filename")
    if ref_content:
        try:
            from services.reference_comparison import parse_ultrastar_file
            parsed_ref = parse_ultrastar_file(ref_content)
            reference_info = {
                "filename": ref_filename,
                "notes_count": len(parsed_ref["notes"]),
                "bpm": parsed_ref["bpm"],
                "gap": parsed_ref["gap"],
            }
        except Exception:
            reference_info = {"filename": ref_filename, "notes_count": 0, "bpm": 0, "gap": 0}

    return {
        "status": "ok",
        "session_id": session_id,
        "from_session": last["id"],
        "filename": os.path.basename(vocal),
        "has_lyrics": bool(lyrics),
        "lyrics": lyrics,
        "artist": new_session["artist"],
        "title": new_session["title"],
        "language": new_session["language"],
        "syllable_count": syllable_count,
        "line_count": line_count,
        "whisper_words": len(new_session.get("whisper_words", [])),
        "reference": reference_info,
        "has_result": last.get("result") is not None,
        "has_vocals": vocal is not None and os.path.exists(vocal),
        "has_original": original is not None and os.path.exists(original),
    }


@app.post("/api/load-test-session")
async def load_test_session():
    """Create a session pre-loaded with test files (dev convenience)."""
    session_id = f"test-{str(uuid.uuid4())[:4]}"
    project_root = os.path.dirname(os.path.dirname(__file__))
    
    vocal_path = os.path.join(project_root, "frontendTest", "test_vocal.wav")
    lyrics_path = os.path.join(project_root, "frontendTest", "lyrics.txt")
    
    if not os.path.exists(vocal_path) or not os.path.exists(lyrics_path):
        raise HTTPException(status_code=404, detail="Test files not found")
    
    with open(lyrics_path, "r") as f:
        lyrics = f.read()
    
    from services.alignment_whisper import parse_lyrics
    parsed = parse_lyrics(lyrics)
    
    sessions[session_id] = {
        "id": session_id,
        "original_audio": vocal_path,
        "vocal_audio": vocal_path,
        "lyrics": lyrics,
        "artist": "U2",
        "title": "Beautiful Day",
        "language": "en",
        "parsed_lyrics": parsed,
        "status": "lyrics_submitted",
        "created_at": time.time(),
    }
    
    flat_count = sum(len(line) for line in parsed)
    log_step("TEST", f"Test session {session_id}: {flat_count} syllables loaded")
    
    return {
        "status": "ok",
        "session_id": session_id,
        "artist": "U2",
        "title": "Beautiful Day",
        "syllable_count": flat_count,
        "line_count": len(parsed),
    }


# ────────────────────────────────────────────────────────────
# Step 3: Generate Ultrastar Files
# ────────────────────────────────────────────────────────────
@app.post("/api/cancel/{session_id}")
async def cancel_generation(session_id: str):
    """Signal the generation pipeline to abort."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session["cancelled"] = True
    return {"status": "ok", "message": "Cancellation requested"}


@app.post("/api/generate/{session_id}")
def generate_ultrastar_files(session_id: str):
    """Run the full processing pipeline: BPM → Pitch → Alignment → Ultrastar."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if not session.get("vocal_audio"):
        raise ServiceError("No vocal audio", "Upload or extract vocals first")
    if not session.get("lyrics"):
        raise ServiceError("No lyrics", "Submit lyrics first")
    
    vocal_path = session["vocal_audio"]
    lyrics = session["lyrics"]
    artist = session.get("artist", "Unknown Artist")
    title = session.get("title", "Unknown Song")
    language = session.get("language", "en")
    
    session["status"] = "generating"
    session["cancelled"] = False
    generation_start = time.time()

    def check_cancelled():
        if session.get("cancelled"):
            session["status"] = "cancelled"
            raise ServiceError("Generation cancelled", "Cancelled by user")

    try:
        # Step 3a: BPM Detection
        log_step("GENERATE", "Step 1/4: BPM detection")
        from services.bpm_detection import detect_bpm, get_audio_duration, refine_bpm, detect_beat_phase
        
        original_path = session.get("original_audio")
        bpm = detect_bpm(vocal_path, original_audio_path=original_path)
        audio_duration = get_audio_duration(vocal_path)
        
        # Detect beat phase (where musical beats actually fall in the audio)
        beat_phase_sec = detect_beat_phase(original_path or vocal_path, bpm)
        
        # Step 3b: Pitch Detection
        log_step("GENERATE", "Step 2/4: Pitch detection")
        check_cancelled()
        from services.pitch_detection import detect_pitches
        
        pitch_data = detect_pitches(vocal_path)
        
        # Step 3c: Alignment
        log_step("GENERATE", "Step 3/4: Syllable alignment")
        check_cancelled()
        whisper_words = session.get("whisper_words", [])
        whisper_chars = session.get("whisper_chars", [])
        whisper_method = session.get("whisper_method", "whisper")
        
        # Primary: WhisperX-based alignment (phoneme-aligned ~50ms accuracy)
        # Fallback: energy-based alignment
        syllable_timings = None
        if whisper_words:
            log_step("GENERATE", f"Using {whisper_method} alignment ({len(whisper_words)} words, {len(whisper_chars)} chars)")
            try:
                from services.alignment_whisper import align_whisper
                syllable_timings = align_whisper(
                    lyrics, whisper_words, language,
                    char_timestamps=whisper_chars,
                    audio_path=vocal_path,
                )
                if syllable_timings:
                    log_step("GENERATE", f"Alignment: {len(syllable_timings)} syllables")
                else:
                    log_step("GENERATE", "Alignment returned empty, falling back to energy-based")
            except Exception as e:
                log_step("GENERATE", f"Alignment failed: {e}, falling back to energy-based")
                import traceback
                traceback.print_exc()
        
        # Onset snapping: refine syllable boundaries using spectral onsets
        if syllable_timings:
            try:
                from services.onset_snapping import snap_to_onsets
                syllable_timings = snap_to_onsets(vocal_path, syllable_timings)
                log_step("GENERATE", "Onset snapping applied")
            except Exception as e:
                log_step("GENERATE", f"Onset snapping skipped: {e}")
        
        if not syllable_timings:
            log_step("GENERATE", "Using energy-based alignment (fallback)")
            from services.alignment import align_lyrics_to_audio
            syllable_timings = align_lyrics_to_audio(vocal_path, lyrics, language)
        
        # GAP = time of first vocal note, so beat 0 = first sung syllable.
        gap_ms = 0
        if syllable_timings:
            first_start_ms = syllable_timings[0]["start"] * 1000
            gap_ms = max(0, int(round(first_start_ms)))
            log_step("GENERATE", f"GAP: {gap_ms}ms (first syllable at {first_start_ms:.0f}ms → beat 0)")
        
        # Refine BPM using syllable timestamps (can recover exact BPM)
        bpm = refine_bpm(syllable_timings, gap_ms, bpm)
        
        # Add line_index to timings if not present
        from services.alignment_whisper import parse_lyrics as parse_lyrics_fast
        parsed = parse_lyrics_fast(lyrics)
        syllable_idx = 0
        for line_idx, line in enumerate(parsed):
            for _ in line:
                if syllable_idx < len(syllable_timings):
                    syllable_timings[syllable_idx]["line_index"] = line_idx
                syllable_idx += 1
        
        # Step 3d: Generate files
        log_step("GENERATE", "Step 4/4: Generating output files")
        check_cancelled()
        from services.ultrastar import generate_ultrastar, generate_processing_summary
        from services.midi_export import generate_midi
        
        # Generate Ultrastar .txt
        _orig_path = session.get('original_audio') or ''
        _audio_ext = os.path.splitext(_orig_path)[1] or '.mp3'
        txt_content = generate_ultrastar(
            syllable_timings=syllable_timings,
            pitch_data=pitch_data,
            bpm=bpm,
            gap_ms=gap_ms,
            artist=artist,
            title=title,
            language=language,
            mp3_filename=f"{artist} - {title}{_audio_ext}",
        )
        
        # Determine pitch/alignment methods (from actual results)
        pitch_method = "PYIN"
        
        # Check what method was actually used by looking at syllable_timings
        if syllable_timings:
            methods_used = set(t.get("method", "unknown") for t in syllable_timings)
            if "whisperx" in methods_used:
                wx_count = sum(1 for t in syllable_timings if t.get("method") == "whisperx")
                char_count = sum(1 for t in syllable_timings if t.get("split_method") == "char")
                align_method = f"WhisperX ({wx_count}/{len(syllable_timings)} syllables, {char_count} char-split)"
            elif "whisper" in methods_used:
                whisper_count = sum(1 for t in syllable_timings if t.get("method") == "whisper")
                align_method = f"Whisper ({whisper_count}/{len(syllable_timings)} syllables)"
            elif "fallback_energy" in methods_used:
                align_method = "Energy-based fallback"
            elif "fallback_even" in methods_used:
                align_method = "Even distribution fallback"
            else:
                align_method = f"Mixed ({', '.join(methods_used)})"
        else:
            align_method = "Energy-based fallback"
        
        # Generate summary
        summary_content = generate_processing_summary(
            syllable_timings=syllable_timings,
            bpm=bpm,
            gap_ms=gap_ms,
            audio_duration=audio_duration,
            pitch_method=pitch_method,
            alignment_method=align_method,
        )
        
        # Save files
        timestamp = int(time.time())
        txt_filename = f"song_{timestamp}.txt"
        midi_filename = f"pitches_{timestamp}.mid"
        summary_filename = f"summary_{timestamp}.txt"
        
        txt_path = os.path.join(DOWNLOADS_DIR, txt_filename)
        midi_path = os.path.join(DOWNLOADS_DIR, midi_filename)
        summary_path = os.path.join(DOWNLOADS_DIR, summary_filename)
        
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(txt_content)
        
        generate_midi(syllable_timings, pitch_data, bpm, midi_path)
        
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(summary_content)
        
        elapsed = time.time() - generation_start
        log_step("GENERATE", f"Done in {elapsed:.1f}s: {txt_filename}")
        
        # Store result in session
        session["status"] = "generated"
        # Preserve cleanup state across regeneration (segments stored in ms — no BPM/GAP conversion needed)
        _old_result = session.get("result") or {}
        # Aggressive cleanup: remove superseded generation artifacts immediately.
        for _k in ("txt_file", "midi_file", "summary_file", "corrected_txt_file"):
            _safe_unlink_download_name(_old_result.get(_k))
        for _fn in session.get("generated_files", []):
            _safe_unlink_download_name(_fn)

        _preserved_cleanup_segments = _old_result.get("cleanup_segments", [])
        _preserved_cleaned_vocal_path = _old_result.get("cleaned_vocal_path")
        session["result"] = {
            "txt_file": txt_filename,
            "midi_file": midi_filename,
            "summary_file": summary_filename,
            "bpm": bpm,
            "gap_ms": gap_ms,
            "beat_phase_sec": beat_phase_sec,
            "syllable_count": len(syllable_timings),
            "audio_duration": audio_duration,
            "pitch_method": pitch_method,
            "alignment_method": align_method,
            "elapsed_seconds": elapsed,
            "syllable_timings": syllable_timings,
            "ultrastar_content": txt_content,
            "pitch_data": pitch_data,
            "cleanup_segments": _preserved_cleanup_segments,
            "cleaned_vocal_path": _preserved_cleaned_vocal_path,
        }
        # Track only latest generation artifacts.
        session["generated_files"] = [txt_filename, midi_filename, summary_filename]
        save_session(session_id)
        _update_txt_asset_headers(session)
        save_session(session_id)
        
        # ── Auto-compare with reference (ms-based, BPM-independent) ──
        ms_comparison = None
        ref_content = session.get("reference_content")
        if ref_content and syllable_timings:
            try:
                from services.reference_comparison import compare_timing_ms
                ms_comparison = compare_timing_ms(syllable_timings, ref_content)
                session["result"]["ms_comparison"] = ms_comparison
                save_session(session_id)
                log_step("GENERATE", f"MS comparison: {ms_comparison['matched']} matched, "
                         f"median {ms_comparison.get('median_error_sec', '?')}s, "
                         f"{ms_comparison.get('pct_within_200ms', '?')}% ≤200ms")
            except Exception as cmp_err:
                log.warning(f"MS comparison failed: {cmp_err}")
        
        return {
            "status": "ok",
            "session_id": session_id,
            "bpm": bpm,
            "gap_ms": gap_ms,
            "syllable_count": len(syllable_timings),
            "audio_duration": round(audio_duration, 1),
            "pitch_method": pitch_method,
            "alignment_method": align_method,
            "elapsed_seconds": round(elapsed, 1),
            "files": {
                "txt": f"/api/download/{session_id}/txt",
                "midi": f"/api/download/{session_id}/midi",
                "summary": f"/api/download/{session_id}/summary",
                "vocals": f"/api/preview-audio/{session_id}/vocals",
            },
            "ultrastar_preview": txt_content[:2000],
            "ms_comparison": ms_comparison,
        }
    except Exception as e:
        session["status"] = "generation_failed"
        session["error"] = str(e)
        log.error(f"Generation failed for session {session_id}: {e}")
        import traceback
        traceback.print_exc()
        raise ServiceError("Generation failed", str(e))


@app.post("/api/generate/start/{session_id}", status_code=202)
async def generate_start(session_id: str, use_cleaned: bool = False):
    """Start generation in a background thread and return immediately (202 Accepted).
    Poll /api/generate/result/{session_id} to check progress.
    If use_cleaned=true, uses the cleaned vocal file as the audio source."""
    import threading
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # Avoid double-starting if already running
    if session.get("status") == "generating":
        return {"status": "already_running"}
    if use_cleaned:
        result = session.get("result") or {}
        cleaned_path = result.get("cleaned_vocal_path")
        if not cleaned_path or not os.path.exists(cleaned_path):
            raise HTTPException(status_code=400, detail="No cleaned audio found. Generate a cleaned preview first.")
    def run_generation():
        original_vocal = session.get("vocal_audio")
        try:
            if use_cleaned:
                session["vocal_audio"] = cleaned_path
            generate_ultrastar_files(session_id)
        except Exception:
            pass
        finally:
            if use_cleaned:
                session["vocal_audio"] = original_vocal
    thread = threading.Thread(target=run_generation, daemon=True)
    thread.start()
    return {"status": "started"}


@app.get("/api/generate-stream/{session_id}")
async def generate_stream(session_id: str):
    """SSE stream that kicks off generation in a thread and sends keep-alive pings.
    Prevents Tauri/WKWebView from timing out on long CPU-bound generation."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    import asyncio, json, threading

    result_holder = {}

    def run_generation():
        try:
            generate_ultrastar_files(session_id)
            result_holder["ok"] = True
        except Exception as e:
            result_holder["error"] = str(e)

    thread = threading.Thread(target=run_generation, daemon=True)
    thread.start()

    async def event_stream():
        while thread.is_alive():
            status = session.get("status", "generating")
            yield f"data: {json.dumps({'type': 'ping', 'status': status})}\n\n"
            await asyncio.sleep(5)
        if "error" in result_holder:
            yield f"data: {json.dumps({'type': 'error', 'message': result_holder['error']})}\n\n"
        else:
            # Build result the same way get_generation_result does
            result = session.get("result", {})
            exclude_keys = {"syllable_timings", "ultrastar_content", "pitch_data"}
            response = {"type": "done", "status": "ok", "session_id": session_id}
            for k, v in result.items():
                if k not in exclude_keys:
                    response[k] = v
            response["ultrastar_preview"] = result.get("ultrastar_content", "")[:2000]
            yield f"data: {json.dumps(response)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/generate/result/{session_id}")
async def get_generation_result(session_id: str):
    """Get the result of a previous generation."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session["status"] == "generation_failed":
        return {"status": "error", "current_status": session["status"], "message": session.get("error", "Generation failed")}
    if session["status"] == "cancelled":
        return {"status": "cancelled", "current_status": session["status"]}
    if session["status"] != "generated":
        return {"status": "pending", "current_status": session["status"]}
    
    result = session.get("result", {})
    # Build response safely — exclude large fields
    exclude_keys = {"syllable_timings", "ultrastar_content", "pitch_data"}
    response = {"status": "ok", "session_id": session_id}
    for k, v in result.items():
        if k not in exclude_keys:
            response[k] = v
    response["ultrastar_preview"] = result.get("ultrastar_content", "")[:2000]
    return response


# ────────────────────────────────────────────────────────────
# Step 3: Generate empty Ultrastar file (no notes, just header)
# ────────────────────────────────────────────────────────────
@app.post("/api/generate/empty/{session_id}")
async def generate_empty(session_id: str):
    """Generate a minimal Ultrastar result with header only and no notes.
    Useful for skipping straight to the editor when note generation is not needed."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    info = session.get("song_info") or {}
    vocal = session.get("vocal_audio") or session.get("original_audio")
    original = session.get("original_audio")

    import librosa as _librosa
    audio_duration = 0.0
    bpm = 120.0
    gap_ms = 0

    if vocal and os.path.exists(vocal):
        try:
            y, sr = _librosa.load(vocal, sr=None, mono=True, duration=60)
            audio_duration_full = _librosa.get_duration(path=vocal)
            audio_duration = audio_duration_full
        except Exception:
            pass

    mp3_filename = os.path.basename(original or vocal or "audio.mp3")
    vocals_filename = os.path.basename(vocal or "")

    header = (
        f"#TITLE:{info.get('title', 'Unknown')}\n"
        f"#ARTIST:{info.get('artist', 'Unknown')}\n"
        f"#LANGUAGE:{info.get('language', '')}\n"
        f"#MP3:{mp3_filename}\n"
        f"#VOCALS:{vocals_filename}\n"
        f"#BPM:{bpm:.2f}\n"
        f"#GAP:{gap_ms}\n"
        f"#YEAR:{info.get('year', '')}\n"
    )
    ultrastar_content = header + "E\n"

    result = {
        "bpm": bpm,
        "gap_ms": gap_ms,
        "beat_phase_sec": 0.0,
        "audio_duration": audio_duration,
        "syllable_timings": [],
        "ultrastar_content": ultrastar_content,
        "notes": [],
        "pitch_data": [],
        "has_edits": False,
        "edit_count": 0,
        "cleanup_segments": [],
        "cleaned_vocal_path": None,
    }
    session["result"] = result
    session["status"] = "generated"
    save_session(session_id)

    log_step("EMPTY_GEN", f"Session {session_id}: generated empty Ultrastar file")
    return {
        "status": "ok",
        "session_id": session_id,
        "bpm": bpm,
        "gap_ms": gap_ms,
        "audio_duration": audio_duration,
        "ultrastar_preview": ultrastar_content,
    }


# ────────────────────────────────────────────────────────────
# Step 4: Editor data
# ────────────────────────────────────────────────────────────
@app.get("/api/editor-data/{session_id}")
async def get_editor_data(session_id: str):
    """Get note data for the piano roll editor."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    result = session.get("result")
    if not result:
        raise ServiceError("No generation result", "Run generation first")
    
    vocal = session.get("vocal_audio")
    original = session.get("original_audio")
    has_vocals = vocal is not None and os.path.exists(vocal)
    has_original = original is not None and os.path.exists(original)

    # Determine vocal_url — prefer vocals, fall back to original
    if has_vocals:
        vocal_url = f"/api/preview-audio/{session_id}/vocals"
    elif has_original:
        vocal_url = f"/api/preview-audio/{session_id}/original"
    else:
        vocal_url = None

    return {
        "status": "ok",
        "session_id": session_id,
        "bpm": result["bpm"],
        "gap_ms": result["gap_ms"],
        "beat_phase_ms": result.get("beat_phase_sec", 0.0) * 1000,
        "audio_duration": result["audio_duration"],
        "syllable_timings": result["syllable_timings"],
        "ultrastar_content": result["ultrastar_content"],
        "vocal_url": vocal_url,
        "has_vocals": has_vocals,
        "has_original": has_original,
        "has_edits": result.get("has_edits", False),
        "edit_count": result.get("edit_count", 0),
        "last_saved": result.get("last_saved"),
        "cleanup_segments": result.get("cleanup_segments", []),
        "cleaned_audio_available": bool(result.get("cleaned_vocal_path")),
        "has_vocal_splice": bool(session.get("original_demucs_vocal")) or (
            vocal is not None and "vocal_patched_" in os.path.basename(vocal)
        ),
        "has_original_demucs": bool(session.get("original_demucs_vocal")),
    }


# ────────────────────────────────────────────────────────────
# Step 4: Save editor state
# ────────────────────────────────────────────────────────────
@app.post("/api/save-editor/{session_id}")
async def save_editor_state(session_id: str, request: Request):
    """Save the current piano-roll editor state (notes, BPM, GAP) back to the session.

    Accepts JSON body with:
        notes: list of note objects {startBeat, duration, pitch, syllable, isRap, type}
        bpm: float
        gap_ms: int
    
    Reconstructs Ultrastar content from the notes and persists everything.
    """
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = session.get("result")
    if not result:
        raise ServiceError("No generation result", "Run generation first")

    body = await request.json()
    editor_notes = body.get("notes", [])
    editor_bpm = body.get("bpm")
    editor_gap = body.get("gap_ms")
    extra_headers = body.get("extra_headers", [])
    cleanup_segments = body.get("cleanup_segments", [])

    if editor_notes is None:
        raise ServiceError("No notes provided")
    if editor_bpm is None or editor_gap is None:
        raise ServiceError("BPM and gap_ms are required")

    normalized_cleanup_segments = []
    if isinstance(cleanup_segments, list):
        for seg in cleanup_segments:
            if not isinstance(seg, dict):
                continue
            try:
                start_ms = float(seg.get("start_ms"))
                end_ms = float(seg.get("end_ms"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(start_ms) or not math.isfinite(end_ms):
                continue
            if end_ms < start_ms:
                start_ms, end_ms = end_ms, start_ms
            if end_ms - start_ms < 50:
                end_ms = start_ms + 50
            patched = bool(seg.get("patched", False))
            normalized_cleanup_segments.append({
                "start_ms": round(start_ms, 1),
                "end_ms": round(end_ms, 1),
                "patched": patched,
            })

    # Reconstruct Ultrastar .txt content from the editor notes
    lines = []
    lines.append(f"#TITLE:{session.get('title', 'Unknown Song')}")
    lines.append(f"#ARTIST:{session.get('artist', 'Unknown Artist')}")
    lines.append(f"#BPM:{editor_bpm:.2f}")
    lines.append(f"#GAP:{int(editor_gap)}")
    lang = session.get("language", "en")
    lang_name = {"en": "English", "de": "German", "fr": "French", "es": "Spanish",
                 "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ja": "Japanese",
                 "ko": "Korean", "zh": "Chinese"}.get(lang, lang.title())
    lines.append(f"#LANGUAGE:{lang_name}")
    _mp3_path = session.get('original_audio') or 'song.mp3'
    lines.append(f"#MP3:{os.path.basename(_mp3_path)}")

    # Extra headers from the editor (e.g. YOUTUBE, COVER, etc.)
    standard_keys = {'TITLE', 'ARTIST', 'BPM', 'GAP', 'LANGUAGE', 'MP3'}
    for header in extra_headers:
        key = header.get('key', '')
        value = header.get('value', '')
        if key.upper() not in standard_keys:
            lines.append(f"#{key}:{value}")

    for note in editor_notes:
        note_type = note.get("type", "")
        if note_type == "break":
                lines.append(f"- {note['startBeat']}")
        else:
            if note.get("isGolden"):
                prefix = "*"
            elif note.get("isRap"):
                prefix = "F:"
            else:
                prefix = ":"
            lines.append(f"{prefix} {note['startBeat']} {note['duration']} {note['pitch']} {note['syllable']}")

    lines.append("E")
    ultrastar_content = "\n".join(lines)

    # Update session result
    result["bpm"] = editor_bpm
    result["gap_ms"] = int(editor_gap)
    result["ultrastar_content"] = ultrastar_content
    result["has_edits"] = True
    result["edit_count"] = result.get("edit_count", 0) + 1
    result["last_saved"] = time.time()
    # Invalidate cleaned audio if cleanup segments changed
    old_segments = result.get("cleanup_segments", [])
    if old_segments != normalized_cleanup_segments:
        _safe_unlink(result.get("cleaned_vocal_path"))
        _safe_unlink_download_name(result.get("cleaned_vocal_file"))
        result["cleaned_vocal_path"] = None
        result["cleaned_vocal_file"] = None
    result["cleanup_segments"] = normalized_cleanup_segments

    # Also write the file to downloads
    old_txt = result.get("txt_file")
    old_corrected = result.get("corrected_txt_file")
    timestamp = int(time.time())
    txt_filename = f"song_{timestamp}.txt"
    txt_path = os.path.join(DOWNLOADS_DIR, txt_filename)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(ultrastar_content)

    if old_txt and old_txt != txt_filename:
        _safe_unlink_download_name(old_txt)
    if old_corrected and old_corrected != txt_filename:
        _safe_unlink_download_name(old_corrected)

    result["txt_file"] = txt_filename
    result["corrected_txt_file"] = txt_filename  # ensure downloads always use latest saved file

    # Inject asset headers (COVER, BACKGROUND, VIDEO, VIDEOGAP) from session
    _update_txt_asset_headers(session)

    save_session(session_id)

    note_count = sum(1 for n in editor_notes if n.get("type") != "break")
    log_step("SAVE-EDITOR", f"Session {session_id}: {note_count} notes, BPM={editor_bpm:.1f}, GAP={editor_gap}ms, {len(extra_headers)} extra headers (save #{result['edit_count']})")

    return {
        "status": "ok",
        "session_id": session_id,
        "note_count": note_count,
        "edit_count": result["edit_count"],
        "last_saved": result["last_saved"],
        "txt_file": txt_filename,
    }


# ────────────────────────────────────────────────────────────
# Step 4: Splice a mic recording into the vocal track
# ────────────────────────────────────────────────────────────
@app.post("/api/splice-recording/{session_id}")
async def splice_recording(session_id: str, recording: UploadFile = File(...), start_ms: float = Form(...), end_ms: float = Form(...)):
    """Splice a mic recording into the session vocal track at the given ms range.
    
    Replaces the audio between start_ms and end_ms in the vocal file with the
    provided recording clip, then stores the patched file as the new vocal source.
    """
    import tempfile, shutil
    import numpy as np
    import librosa
    import soundfile as sf

    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    vocal_path = session.get("vocal_audio") or session.get("original_audio")
    if not vocal_path or not os.path.isfile(vocal_path):
        raise ServiceError("No vocal audio found", "Upload audio first")

    # Save uploaded recording to a temp file
    suffix = os.path.splitext(recording.filename or "rec.webm")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(recording.file, tmp)
        tmp_path = tmp.name

    try:
        # Load both files
        vocal, sr = librosa.load(vocal_path, sr=None, mono=False)
        if vocal.ndim == 1:
            vocal = np.expand_dims(vocal, axis=0)

        clip, clip_sr = librosa.load(tmp_path, sr=sr, mono=False)
        if clip.ndim == 1:
            clip = np.expand_dims(clip, axis=0)

        # Ensure same channel count
        if clip.shape[0] < vocal.shape[0]:
            clip = np.repeat(clip, vocal.shape[0], axis=0)
        elif clip.shape[0] > vocal.shape[0]:
            clip = clip[:vocal.shape[0]]

        start_sample = max(0, int(start_ms / 1000.0 * sr))
        end_sample   = min(vocal.shape[1], int(end_ms   / 1000.0 * sr))
        region_len   = end_sample - start_sample

        if region_len <= 0:
            raise ServiceError("Invalid range", "start_ms must be before end_ms")

        # Trim or pad clip to exactly fit the region
        if clip.shape[1] >= region_len:
            clip_fit = clip[:, :region_len]
        else:
            pad = np.zeros((vocal.shape[0], region_len - clip.shape[1]), dtype=np.float32)
            clip_fit = np.concatenate([clip, pad], axis=1)

        # Splice in
        patched = vocal.copy()
        patched[:, start_sample:end_sample] = clip_fit

        # Save as new vocal file
        timestamp = int(time.time())
        patched_filename = f"vocal_patched_{timestamp}.wav"
        patched_path = os.path.join(SESSIONS_DIR, patched_filename)
        sf.write(patched_path, patched.T, sr, subtype='PCM_16')

        # Preserve original demucs vocal before first splice
        if not session.get("original_demucs_vocal"):
            session["original_demucs_vocal"] = session.get("vocal_audio")
        # Update session to use patched vocal
        session["vocal_audio"] = patched_path
        session.setdefault("patched_vocal_files", []).append(patched_path)
        # Invalidate cleaned audio — it was generated from the old vocal
        result = session.get("result") or {}
        _safe_unlink(result.get("cleaned_vocal_path"))
        _safe_unlink_download_name(result.get("cleaned_vocal_file"))
        result["cleaned_vocal_path"] = None
        result["cleaned_vocal_file"] = None
        save_session(session_id)

        log_step("SPLICE", f"Session {session_id}: spliced recording into vocal @ {start_ms:.0f}–{end_ms:.0f}ms → {patched_filename}")

        return {"status": "ok", "patched_vocal_file": patched_filename}
    finally:
        os.unlink(tmp_path)


# ────────────────────────────────────────────────────────────
# Step 4: Restore a segment from original vocal
# ────────────────────────────────────────────────────────────
@app.post("/api/restore-segment/{session_id}")
async def restore_segment(session_id: str, request: Request):
    """Restore a time range in the current vocal from the original demucs vocal.

    Accepts JSON body:
        start_ms: float
        end_ms: float
    """
    import numpy as np
    import librosa
    import soundfile as sf

    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    body = await request.json()
    start_ms = float(body.get("start_ms", 0))
    end_ms = float(body.get("end_ms", 0))

    original_path = session.get("original_demucs_vocal")
    vocal_path = session.get("vocal_audio")

    if not original_path or not os.path.isfile(original_path):
        raise ServiceError(
            "No original vocal baseline to restore from",
            "This session has no saved original demucs vocal. Regenerate vocals or start a fresh session."
        )

    if not vocal_path or not os.path.isfile(vocal_path):
        raise ServiceError("No vocal audio found", "Upload audio first")

    original, sr = librosa.load(original_path, sr=None, mono=False)
    if original.ndim == 1:
        original = np.expand_dims(original, axis=0)

    vocal, _ = librosa.load(vocal_path, sr=sr, mono=False)
    if vocal.ndim == 1:
        vocal = np.expand_dims(vocal, axis=0)

    start_sample = max(0, int(start_ms / 1000.0 * sr))
    end_sample = min(vocal.shape[1], int(end_ms / 1000.0 * sr))
    orig_end = min(original.shape[1], end_sample)

    patched = vocal.copy()
    patched[:, start_sample:orig_end] = original[:, start_sample:orig_end]

    timestamp = int(time.time())
    patched_filename = f"vocal_patched_{timestamp}.wav"
    patched_path = os.path.join(SESSIONS_DIR, patched_filename)
    sf.write(patched_path, patched.T, sr, subtype='PCM_16')

    session["vocal_audio"] = patched_path
    session.setdefault("patched_vocal_files", []).append(patched_path)
    result = session.get("result") or {}
    _safe_unlink(result.get("cleaned_vocal_path"))
    _safe_unlink_download_name(result.get("cleaned_vocal_file"))
    result["cleaned_vocal_path"] = None
    result["cleaned_vocal_file"] = None
    save_session(session_id)

    log_step("RESTORE", f"Session {session_id}: restored original @ {start_ms:.0f}–{end_ms:.0f}ms → {patched_filename}")
    return {"status": "ok", "patched_vocal_file": patched_filename}


# ────────────────────────────────────────────────────────────
# Step 4: Generate cleaned audio preview
# ────────────────────────────────────────────────────────────
@app.post("/api/generate-cleaned-audio/{session_id}")
async def generate_cleaned_audio_endpoint(session_id: str, request: Request):
    """Generate cleaned audio by muting specified beat ranges.
    
    Accepts JSON body with:
        cleanup_segments: list of {start_beat, end_beat}
    
    Mutes the specified ranges in the vocal track while preserving total duration,
    then saves to session for later use in regeneration.
    """
    from services.audio_cleanup import generate_cleaned_audio, merge_overlapping_segments
    
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = session.get("result")
    if not result:
        raise ServiceError("No generation result", "Run generation first")

    body = await request.json()
    cleanup_segments = body.get("cleanup_segments", [])

    if not cleanup_segments:
        raise ServiceError("No cleanup segments provided")

    # Get audio and timing info from session
    vocal_audio_path = session.get("vocal_audio") or result.get("vocal_file")
    if not vocal_audio_path or not os.path.isfile(vocal_audio_path):
        raise ServiceError("Vocal audio not found", "No vocal track in session")

    bpm = result.get("bpm")
    gap_ms = result.get("gap_ms")

    # Generate cleaned audio
    _safe_unlink(result.get("cleaned_vocal_path"))
    _safe_unlink_download_name(result.get("cleaned_vocal_file"))
    timestamp = int(time.time())
    cleaned_filename = f"cleaned_vocals_{timestamp}.wav"
    cleaned_path = os.path.join(DOWNLOADS_DIR, cleaned_filename)

    try:
        cleanup_result = generate_cleaned_audio(
            vocal_audio_path=vocal_audio_path,
            cleanup_segments=cleanup_segments,
            output_path=cleaned_path
        )
    except Exception as e:
        raise ServiceError(f"Audio cleanup failed: {str(e)}")

    # Store cleaned audio path in result for later regeneration
    result["cleaned_vocal_file"] = cleaned_filename
    result["cleaned_vocal_path"] = cleaned_path
    result["cleanup_audio_generated_at"] = time.time()
    save_session(session_id)

    merged_count = len(merge_overlapping_segments(cleanup_segments))
    log_step("CLEANUP", f"Session {session_id}: Generated cleaned audio ({merged_count} muted ranges)")

    return {
        "status": "ok",
        "session_id": session_id,
        "cleaned_audio_file": cleaned_filename,
        "sample_rate": cleanup_result["sample_rate"],
        "num_samples": cleanup_result["num_samples"],
        "segments_muted": cleanup_result["segments_count"],
    }


# ────────────────────────────────────────────────────────────
@app.post("/api/corrections/{session_id}")
async def save_corrections(session_id: str, corrections: dict = None):
    """Save user corrections from the piano roll editor."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if not corrections:
        raise ServiceError("No corrections provided")
    
    # Save correction pair: AI output vs user correction
    correction_file = os.path.join(
        CORRECTIONS_DIR,
        f"{session_id}_{int(time.time())}.json"
    )
    
    correction_data = {
        "session_id": session_id,
        "timestamp": time.time(),
        "artist": session.get("artist", ""),
        "title": session.get("title", ""),
        "original_timings": session.get("result", {}).get("syllable_timings", []),
        "user_corrections": corrections,
    }
    
    with open(correction_file, "w") as f:
        json.dump(correction_data, f, indent=2)
    
    log_step("CORRECTIONS", f"Saved corrections: {correction_file}")
    
    return {"status": "ok", "saved": correction_file}



# ────────────────────────────────────────────────────────────
# Step 5: Export & Download
# ────────────────────────────────────────────────────────────
@app.post("/api/export/{session_id}")
async def export_with_corrections(
    session_id: str,
    corrected_content: str = Form(None),
):
    """Export final files, optionally with corrected Ultrastar content."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    result = session.get("result")
    if not result:
        raise ServiceError("No generation result", "Run generation first")
    
    if corrected_content:
        # Save corrected version
        old_corrected = result.get("corrected_txt_file")
        timestamp = int(time.time())
        corrected_filename = f"song_corrected_{timestamp}.txt"
        corrected_path = os.path.join(DOWNLOADS_DIR, corrected_filename)
        
        with open(corrected_path, "w", encoding="utf-8") as f:
            f.write(corrected_content)

        if old_corrected and old_corrected != corrected_filename:
            _safe_unlink_download_name(old_corrected)
        
        result["corrected_txt_file"] = corrected_filename
        log_step("EXPORT", f"Saved corrected file: {corrected_filename}")
    
    return {
        "status": "ok",
        "files": {
            "txt": f"/api/download/{session_id}/txt",
            "midi": f"/api/download/{session_id}/midi",
            "summary": f"/api/download/{session_id}/summary",
            "vocals": f"/api/preview-audio/{session_id}/vocals",
        }
    }


@app.patch("/api/session/{session_id}/metadata")
async def update_metadata(session_id: str, artist: str = Form(...), title: str = Form(...), language: str = Form(None)):
    """Update artist/title/language in session and rewrite the .txt file headers."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session["artist"] = artist
    session["title"] = title
    if language is not None:
        session["language"] = language
    # Clear derived headers so _update_txt_asset_headers recomputes them from new artist/title
    session.pop("vocals_header", None)
    session.pop("instrumental_header", None)
    save_session(session_id)

    # Rewrite all headers in the .txt file via the single source of truth
    result = session.get("result")
    if result:
        for key in ["corrected_txt_file", "txt_file"]:
            fname = result.get(key)
            if not fname:
                continue
            path = os.path.join(DOWNLOADS_DIR, fname)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                import re
                content = re.sub(r"^#TITLE:.*$", f"#TITLE:{title}", content, count=1, flags=re.MULTILINE)
                content = re.sub(r"^#ARTIST:.*$", f"#ARTIST:{artist}", content, count=1, flags=re.MULTILINE)
                if language:
                    content = re.sub(r"^#LANGUAGE:.*$", f"#LANGUAGE:{language}", content, count=1, flags=re.MULTILINE)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                log_step("METADATA", f"Failed to update {fname}: {e}")

    # Now rewrite all asset headers (MP3, VOCALS, INSTRUMENTAL, COVER, etc.) using the single source of truth
    _update_txt_asset_headers(session)

    log_step("METADATA", f"Session {session_id}: artist='{artist}', title='{title}', language='{language}'")
    return {"status": "ok", "artist": artist, "title": title, "language": language}


@app.get("/api/download/{session_id}/{file_type}")
async def download_file(
    session_id: str,
    file_type: str,
    include_vocals: str = "1",
    include_instrumental: str = "1",
):
    """Download a generated file."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    result = session.get("result")
    if not result:
        raise HTTPException(status_code=404, detail="No files generated yet")
    
    file_map = {
        "txt": result.get("corrected_txt_file", result.get("txt_file")),
        "midi": result.get("midi_file"),
        "summary": result.get("summary_file"),
    }
    
    filename = file_map.get(file_type)
    if not filename:
        raise HTTPException(status_code=404, detail=f"File type '{file_type}' not found")
    
    path = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    # For .txt files, normalize linebreaks to YASS-style (single number) on the fly
    if file_type == "txt":
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        import re as _re_dl
        content = _re_dl.sub(r'^(- \d+) \d+$', r'\1', content, flags=_re_dl.MULTILINE)
        if include_vocals != "1":
            content = _remove_header(content, "VOCALS")
        if include_instrumental != "1":
            content = _remove_header(content, "INSTRUMENTAL")
        # Build a user-friendly download name from artist/title
        artist = session.get("artist", "").strip()
        title = session.get("title", "").strip()
        base = f"{artist} - {title}" if artist and title else title or artist or "Untitled Song"
        from fastapi.responses import Response
        return Response(content=content, media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{base}.txt"'})

    artist = session.get("artist", "").strip()
    title = session.get("title", "").strip()
    if artist and title:
        base = f"{artist} - {title}"
    elif title:
        base = title
    elif artist:
        base = artist
    else:
        base = "Untitled Song"
    
    ext_map = {"txt": ".txt", "midi": ".mid", "summary": "_summary.txt"}
    download_name = base + ext_map.get(file_type, ".txt")
    
    return FileResponse(path, filename=download_name)


# ────────────────────────────────────────────────────────────
# Song assets helpers
# ────────────────────────────────────────────────────────────

import re as _re

def _set_header(content: str, key: str, value: str) -> str:
    """Insert or replace a #KEY:value line in Ultrastar .txt content.

    Always removes any existing occurrences first to prevent duplicates,
    then inserts after the last header line in the file.
    """
    new_line = f"#{key}:{value}"
    # Remove all existing occurrences of this key
    content = _remove_header(content, key)
    # Find the last #... line in the entire file (not just consecutive from top)
    lines = content.split("\n")
    idx = 0
    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            idx = i + 1
    lines.insert(idx, new_line)
    return "\n".join(lines)


def _remove_header(content: str, key: str) -> str:
    """Remove all #KEY:... lines from Ultrastar .txt content."""
    lines = [ln for ln in content.split("\n") if not _re.match(rf"^#{key}:[^\n]*$", ln)]
    return "\n".join(lines)


def _update_txt_asset_headers(session: dict) -> None:
    """Inject/update or remove MP3, VOCALS, INSTRUMENTAL, COVER, BACKGROUND, VIDEO, VIDEOGAP, YOUTUBE in the session's .txt file."""
    result = session.get("result")
    if not result:
        return
    # Update both txt_file and corrected_txt_file (if they exist and are different)
    files_to_update = []
    txt_file = result.get("txt_file")
    corrected_txt_file = result.get("corrected_txt_file")
    if txt_file:
        files_to_update.append(txt_file)
    if corrected_txt_file and corrected_txt_file != txt_file:
        files_to_update.append(corrected_txt_file)
    if not files_to_update:
        return
    for txt_file in files_to_update:
        path = os.path.join(DOWNLOADS_DIR, txt_file)
        if not os.path.exists(path):
            continue

        with open(path, encoding="utf-8") as f:
            content = f.read()

        artist = session.get("artist", "").strip()
        title = session.get("title", "").strip()

        if artist and title:
            base = f"{artist} - {title}"
        elif title:
            base = title
        elif artist:
            base = artist
        else:
            base = "Untitled Song"

        # --- #MP3: use Artist - Title + original extension, or remove if no original ---
        original_audio = session.get("original_audio")
        if original_audio:
            ext = os.path.splitext(original_audio)[1] or '.mp3'
            mp3_name = f"{base}{ext}"
            content = _set_header(content, "MP3", mp3_name)
            log_step("TXT-HEADERS", f"Set #MP3 to {mp3_name}")
        else:
            content = _remove_header(content, "MP3")
            log_step("TXT-HEADERS", f"Removed #MP3 (original_audio={original_audio})")

        cover_file = session.get("cover_file")
        if cover_file and os.path.exists(cover_file):
            content = _set_header(content, "COVER", f"{base} [CO].jpg")
        else:
            content = _remove_header(content, "COVER")

        bg_file = session.get("bgimage_file")
        if bg_file and os.path.exists(bg_file):
            content = _set_header(content, "BACKGROUND", f"{base} [BG].jpg")
        else:
            content = _remove_header(content, "BACKGROUND")

        video_filename = session.get("video_filename")
        if video_filename:
            content = _set_header(content, "VIDEO", video_filename)
            video_gap = session.get("video_gap")
            if video_gap is not None:
                content = _set_header(content, "VIDEOGAP", str(video_gap))
            else:
                content = _remove_header(content, "VIDEOGAP")
        else:
            content = _remove_header(content, "VIDEO")
            content = _remove_header(content, "VIDEOGAP")

        youtube_url = session.get("youtube_url")
        if youtube_url:
            content = _set_header(content, "YOUTUBE", youtube_url)
        else:
            content = _remove_header(content, "YOUTUBE")

        genre = session.get("genre", "").strip()
        if genre:
            content = _set_header(content, "GENRE", genre)
        else:
            content = _remove_header(content, "GENRE")

        year = session.get("year", "").strip()
        if year:
            content = _set_header(content, "YEAR", year)
        else:
            content = _remove_header(content, "YEAR")

        edition = session.get("edition", "").strip()
        if edition:
            content = _set_header(content, "EDITION", edition)
        else:
            content = _remove_header(content, "EDITION")

        creator = session.get("creator", "").strip()
        if creator:
            content = _set_header(content, "CREATOR", creator)
        else:
            content = _remove_header(content, "CREATOR")

        vocal_audio = session.get("vocal_audio")
        if vocal_audio:
            ext = os.path.splitext(vocal_audio)[1]
            vocals_header = f"{base} [Vocals]{ext}"
            content = _set_header(content, "VOCALS", vocals_header)
            log_step("TXT-HEADERS", f"Set #VOCALS to {vocals_header}")
        else:
            content = _remove_header(content, "VOCALS")

        instrumental_audio = session.get("instrumental_audio")
        if instrumental_audio:
            ext_i = os.path.splitext(instrumental_audio)[1]
            instrumental_header = f"{base} [Instrumental]{ext_i}"
            content = _set_header(content, "INSTRUMENTAL", instrumental_header)
            log_step("TXT-HEADERS", f"Set #INSTRUMENTAL to {instrumental_header}")
        else:
            content = _remove_header(content, "INSTRUMENTAL")

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        result["ultrastar_content"] = content


# ────────────────────────────────────────────────────────────
# Song assets: cover image, background image, video filename
# ────────────────────────────────────────────────────────────

@app.post("/api/cover/{session_id}")
async def upload_cover(session_id: str, image: UploadFile = File(...)):
    """Save a pre-cropped cover image (480×480 JPEG) for the session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    session_dir = os.path.join(UPLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    cover_path = os.path.join(session_dir, "cover.jpg")
    data = await image.read()
    with open(cover_path, "wb") as f:
        f.write(data)
    session["cover_file"] = cover_path
    save_session(session_id)
    _update_txt_asset_headers(session)
    log_step("ASSETS", f"Cover saved for session {session_id} ({len(data)} bytes)")
    return {"status": "ok"}


@app.get("/api/cover/{session_id}")
async def get_cover(session_id: str):
    """Serve the cover image for a session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    cover_path = session.get("cover_file")
    if not cover_path or not os.path.exists(cover_path):
        raise HTTPException(status_code=404, detail="No cover image")
    return FileResponse(cover_path, media_type="image/jpeg")


@app.delete("/api/cover/{session_id}")
async def delete_cover(session_id: str):
    """Remove the cover image for a session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    cover_path = session.get("cover_file")
    if cover_path and os.path.exists(cover_path):
        os.remove(cover_path)
    session.pop("cover_file", None)
    save_session(session_id)
    _update_txt_asset_headers(session)
    return {"status": "ok"}


@app.post("/api/bgimage/{session_id}")
async def upload_bgimage(session_id: str, image: UploadFile = File(...)):
    """Save a background image for the session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    session_dir = os.path.join(UPLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    bg_path = os.path.join(session_dir, "background.jpg")
    data = await image.read()
    with open(bg_path, "wb") as f:
        f.write(data)
    session["bgimage_file"] = bg_path
    save_session(session_id)
    _update_txt_asset_headers(session)
    log_step("ASSETS", f"Background image saved for session {session_id} ({len(data)} bytes)")
    return {"status": "ok"}


@app.get("/api/bgimage/{session_id}")
async def get_bgimage(session_id: str):
    """Serve the background image for a session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    bg_path = session.get("bgimage_file")
    if not bg_path or not os.path.exists(bg_path):
        raise HTTPException(status_code=404, detail="No background image")
    return FileResponse(bg_path, media_type="image/jpeg")


@app.delete("/api/bgimage/{session_id}")
async def delete_bgimage(session_id: str):
    """Remove the background image for a session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    bg_path = session.get("bgimage_file")
    if bg_path and os.path.exists(bg_path):
        os.remove(bg_path)
    session.pop("bgimage_file", None)
    save_session(session_id)
    _update_txt_asset_headers(session)
    return {"status": "ok"}


@app.get("/api/assets/{session_id}")
async def get_assets_meta(session_id: str):
    """Return stored video filename, gap, youtube url, and song metadata for the session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    vocal = session.get("vocal_audio")
    instrumental = session.get("instrumental_audio")
    artist = session.get("artist", "").strip()
    title_val = session.get("title", "").strip()
    if artist and title_val:
        base = f"{artist} - {title_val}"
    elif title_val:
        base = title_val
    elif artist:
        base = artist
    else:
        base = "Untitled Song"

    def _archive_name(audio_path, suffix):
        if not audio_path:
            return ""
        ext = os.path.splitext(audio_path)[1]
        return f"{base} [{suffix}]{ext}"

    stored_vocals = session.get("vocals_header", "")
    raw_vocals = os.path.basename(vocal) if vocal else ""
    vocals_header = stored_vocals if (stored_vocals and stored_vocals != raw_vocals) else (_archive_name(vocal, "Vocals") if vocal else stored_vocals)

    stored_instr = session.get("instrumental_header", "")
    raw_instr = os.path.basename(instrumental) if instrumental else ""
    instrumental_header = stored_instr if (stored_instr and stored_instr != raw_instr) else (_archive_name(instrumental, "Instrumental") if instrumental else stored_instr)

    return {
        "video_filename": session.get("video_filename", ""),
        "video_gap": session.get("video_gap", 0),
        "youtube_url": session.get("youtube_url", ""),
        "genre": session.get("genre", ""),
        "year": session.get("year", ""),
        "edition": session.get("edition", ""),
        "creator": session.get("creator", ""),
        "vocals_header": vocals_header,
        "instrumental_header": instrumental_header,
    }


@app.post("/api/assets/{session_id}")
async def save_assets_meta(session_id: str, request: Request):
    """Save video filename, optional video gap, youtube url, and song metadata for the session."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    body = await request.json()
    video_filename = (body.get("video_filename") or "").strip()
    video_gap = body.get("video_gap")
    youtube_url = (body.get("youtube_url") or "").strip()
    genre = (body.get("genre") or "").strip()
    year = (body.get("year") or "").strip()
    edition = (body.get("edition") or "").strip()
    creator = (body.get("creator") or "").strip()
    vocals_header = (body.get("vocals_header") or "").strip()
    instrumental_header = (body.get("instrumental_header") or "").strip()

    if video_filename:
        session["video_filename"] = video_filename
    else:
        session.pop("video_filename", None)
    if video_gap is not None:
        try:
            session["video_gap"] = float(video_gap)
        except (ValueError, TypeError):
            pass
    else:
        session.pop("video_gap", None)
    if youtube_url:
        session["youtube_url"] = youtube_url
    else:
        session.pop("youtube_url", None)
    if genre:
        session["genre"] = genre
    else:
        session.pop("genre", None)
    if year:
        session["year"] = year
    else:
        session.pop("year", None)
    if edition:
        session["edition"] = edition
    else:
        session.pop("edition", None)
    if creator:
        session["creator"] = creator
    else:
        session.pop("creator", None)
    if vocals_header:
        session["vocals_header"] = vocals_header
    else:
        session.pop("vocals_header", None)
    if instrumental_header:
        session["instrumental_header"] = instrumental_header
    else:
        session.pop("instrumental_header", None)

    save_session(session_id)
    _update_txt_asset_headers(session)
    log_step("ASSETS", f"Assets meta saved for session {session_id}: video={video_filename!r} genre={genre!r} creator={creator!r}")
    return {"status": "ok"}


@app.get("/api/download-zip/{session_id}")
async def download_zip(
    session_id: str,
    include_vocals: str = "1",
    include_instrumental: str = "1",
    include_summary: str = "1",
    include_midi: str = "1",
):
    """Bundle all generated files into a single ZIP download."""
    import zipfile
    import io
    from starlette.responses import Response

    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = session.get("result")
    if not result:
        raise HTTPException(status_code=404, detail="No files generated yet")

    # Build base name from artist/title
    artist = session.get("artist", "").strip()
    title_name = session.get("title", "").strip()
    if artist and title_name:
        base = f"{artist} - {title_name}"
    elif title_name:
        base = title_name
    elif artist:
        base = artist
    else:
        base = "Untitled Song"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Ultrastar .txt — inject asset headers before writing
        txt_file = result.get("corrected_txt_file", result.get("txt_file"))
        if txt_file:
            path = os.path.join(DOWNLOADS_DIR, txt_file)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    txt_content = f.read()

                # Normalize linebreaks to YASS-style (single number) for old sessions
                import re as _re_zip
                txt_content = _re_zip.sub(r'^(- \d+) \d+$', r'\1', txt_content, flags=_re_zip.MULTILINE)

                # Helper: insert/replace a header line
                def _set_header(content, key, value):
                    import re
                    tag = f"#{key}:"
                    new_line = f"#{key}:{value}"
                    if tag in content:
                        return re.sub(rf"#{key}:[^\n]*", new_line, content)
                    # Insert after last # header line
                    lines = content.split("\n")
                    idx = 0
                    for i, ln in enumerate(lines):
                        if ln.startswith("#"):
                            idx = i + 1
                        else:
                            break
                    lines.insert(idx, new_line)
                    return "\n".join(lines)

                cover_path = session.get("cover_file")
                if cover_path and os.path.exists(cover_path):
                    cover_archive = f"{base} [CO].jpg"
                    txt_content = _set_header(txt_content, "COVER", cover_archive)

                bg_path = session.get("bgimage_file")
                if bg_path and os.path.exists(bg_path):
                    bg_archive = f"{base} [BG].jpg"
                    txt_content = _set_header(txt_content, "BACKGROUND", bg_archive)

                video_filename = session.get("video_filename")
                if video_filename:
                    txt_content = _set_header(txt_content, "VIDEO", video_filename)
                    video_gap = session.get("video_gap")
                    if video_gap is not None:
                        txt_content = _set_header(txt_content, "VIDEOGAP", str(video_gap))

                # Strip VOCALS/INSTRUMENTAL headers from .txt copy if not included
                if include_vocals != "1":
                    txt_content = _remove_header(txt_content, "VOCALS")
                if include_instrumental != "1":
                    txt_content = _remove_header(txt_content, "INSTRUMENTAL")

                zf.writestr(f"{base}.txt", txt_content)

        # MIDI
        midi_file = result.get("midi_file")
        if midi_file and include_midi == "1":
            path = os.path.join(DOWNLOADS_DIR, midi_file)
            if os.path.exists(path):
                zf.write(path, f"{base}.mid")

        # Summary
        summary_file = result.get("summary_file")
        if summary_file and include_summary == "1":
            path = os.path.join(DOWNLOADS_DIR, summary_file)
            if os.path.exists(path):
                zf.write(path, f"{base}_summary.txt")

        # Vocals audio
        vocal_path = session.get("vocal_audio")
        if vocal_path and os.path.exists(vocal_path) and include_vocals == "1":
            ext = os.path.splitext(vocal_path)[1]
            zf.write(vocal_path, f"{base} [Vocals]{ext}")

        # Instrumental audio (no_vocals from Demucs)
        instrumental_path = session.get("instrumental_audio")
        if instrumental_path and os.path.exists(instrumental_path) and include_instrumental == "1":
            ext = os.path.splitext(instrumental_path)[1]
            zf.write(instrumental_path, f"{base} [Instrumental]{ext}")

        # Original audio
        original_path = session.get("original_audio")
        if original_path and os.path.exists(original_path):
            ext = os.path.splitext(original_path)[1]
            zf.write(original_path, f"{base}{ext}")

        # Cover image
        cover_path = session.get("cover_file")
        if cover_path and os.path.exists(cover_path):
            zf.write(cover_path, f"{base} [CO].jpg")

        # Background image
        bg_path = session.get("bgimage_file")
        if bg_path and os.path.exists(bg_path):
            zf.write(bg_path, f"{base} [BG].jpg")

    buf.seek(0)
    zip_name = f"{base}.zip"
    log_step("EXPORT", f"ZIP download: {zip_name}")

    # Content-Disposition headers are latin-1 only; use RFC 5987 filename* for
    # song titles that contain non-ASCII characters (curly quotes, accents, …).
    from urllib.parse import quote as _urlquote
    ascii_name = zip_name.encode("ascii", errors="replace").decode("ascii")
    utf8_name = _urlquote(zip_name, safe="")
    content_disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition},
    )


# ────────────────────────────────────────────────────────────
# Reference comparison (learning from verified Ultrastar files)
# ────────────────────────────────────────────────────────────
@app.post("/api/reference/upload/{session_id}")
async def upload_reference(session_id: str, reference: UploadFile = File(...)):
    """Upload a verified/original Ultrastar .txt file for comparison."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    content = (await reference.read()).decode("utf-8", errors="replace")
    session["reference_content"] = content
    session["reference_filename"] = reference.filename
    save_session(session_id)
    
    # Parse to validate
    from services.reference_comparison import parse_ultrastar_file, compare_lyrics
    parsed = parse_ultrastar_file(content)
    
    log_step("REFERENCE", f"Session {session_id}: uploaded reference {reference.filename} ({len(parsed['notes'])} notes)")
    
    # Compare lyrics if user has already entered them
    lyrics_comparison = None
    user_lyrics = session.get("lyrics")
    if user_lyrics:
        lyrics_comparison = compare_lyrics(user_lyrics, content)
    
    return {
        "status": "ok",
        "filename": reference.filename,
        "notes_count": len(parsed["notes"]),
        "bpm": parsed["bpm"],
        "gap": parsed["gap"],
        "headers": parsed["headers"],
        "lyrics_comparison": lyrics_comparison,
    }


@app.post("/api/reference/compare/{session_id}")
async def compare_reference(session_id: str):
    """Compare AI-generated output with uploaded reference file."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    ai_content = session.get("result", {}).get("ultrastar_content")
    ref_content = session.get("reference_content")
    
    if not ai_content:
        raise ServiceError("No AI output", "Run generation first")
    if not ref_content:
        raise ServiceError("No reference file", "Upload a reference Ultrastar file first")
    
    from services.reference_comparison import compare_with_reference, store_comparison, compare_lyrics
    
    comparison = compare_with_reference(ai_content, ref_content)
    
    # Also compare lyrics
    user_lyrics = session.get("lyrics")
    lyrics_comparison = None
    if user_lyrics:
        lyrics_comparison = compare_lyrics(user_lyrics, ref_content)
        comparison["lyrics_comparison"] = lyrics_comparison
    
    # Store for learning
    metadata = {
        "artist": session.get("artist", ""),
        "title": session.get("title", ""),
        "language": session.get("language", "en"),
    }
    store_comparison(session_id, comparison, metadata)
    
    session["reference_comparison"] = comparison
    
    return {
        "status": "ok",
        "comparison": comparison,
    }


@app.get("/api/reference/stats")
async def reference_stats():
    """Get learning stats and biases from stored reference comparisons."""
    from services.reference_comparison import get_reference_stats
    
    stats = get_reference_stats()
    return {"status": "ok", **stats}


@app.get("/api/reference/notes/{session_id}")
async def get_reference_notes(session_id: str):
    """Get parsed reference notes for overlay in the piano roll editor."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    ref_content = session.get("reference_content")
    if not ref_content:
        raise HTTPException(status_code=404, detail="No reference file uploaded")
    
    from services.reference_comparison import parse_ultrastar_file
    parsed = parse_ultrastar_file(ref_content)
    
    return {
        "status": "ok",
        "bpm": parsed["bpm"],
        "gap": parsed["gap"],
        "notes": parsed["notes"],
        "breaks": parsed["breaks"],
    }


# ────────────────────────────────────────────────────────────
# Startup
# ────────────────────────────────────────────────────────────


@app.post("/api/save-mic-trail/{session_id}")
async def save_mic_trail(session_id: str, trail: str = Form(...), audio: UploadFile = File(None)):
    """Save a mic pitch trail recording + optional voice audio to downloads folder.
    
    Accepts multipart form: 'trail' (JSON string) and optional 'audio' (webm file).
    Keeps only the last 5 recordings, auto-deleting the oldest.
    """
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    body = json.loads(trail)
    
    # Save to downloads folder with timestamp
    import glob as glob_mod
    timestamp = int(time.time())
    
    # Save trail JSON
    trail_filename = f"mic_trail_{session_id[:8]}_{timestamp}.json"
    trail_filepath = os.path.join(DOWNLOADS_DIR, trail_filename)
    with open(trail_filepath, "w") as f:
        json.dump(body, f, indent=2)
    
    # Save audio if provided
    audio_filename = None
    if audio and audio.filename:
        ext = audio.filename.rsplit('.', 1)[-1] if '.' in audio.filename else 'webm'
        audio_filename = f"mic_audio_{session_id[:8]}_{timestamp}.{ext}"
        audio_filepath = os.path.join(DOWNLOADS_DIR, audio_filename)
        audio_data = await audio.read()
        with open(audio_filepath, "wb") as f:
            f.write(audio_data)
        log_step("MIC", f"Saved mic audio: {audio_filename} ({len(audio_data) // 1024} KB)")
    
    # Keep only last 5 mic trail files for this session
    for prefix in ("mic_trail_", "mic_audio_"):
        pattern = os.path.join(DOWNLOADS_DIR, f"{prefix}{session_id[:8]}_*")
        existing = sorted(glob_mod.glob(pattern))
        while len(existing) > 5:
            oldest = existing.pop(0)
            os.remove(oldest)
            log_step("MIC", f"Removed old: {os.path.basename(oldest)}")
    
    sample_count = len(body.get('samples', []))
    log_step("MIC", f"Saved mic trail: {trail_filename} ({sample_count} samples)")
    
    result = {"status": "ok", "filename": trail_filename}
    if audio_filename:
        result["audioFile"] = audio_filename
    return result


if __name__ == "__main__":
    import uvicorn

    # Pre-load essentia (and its bundled libSDL-1.2.0.dylib) on the main thread
    # BEFORE uvicorn spawns worker threads.
    #
    # SDL 1.2's dylib constructor (dllinit) checks [NSThread isMainThread] and
    # calls error_dialog → [NSAlert init] when loaded on a background thread.
    # On macOS 26+, creating AppKit objects off the main thread raises an ObjC
    # exception that propagates to the C++ runtime and calls abort() (SIGABRT).
    #
    # Python caches loaded modules in sys.modules, so once the dylib is loaded
    # here on the main thread, subsequent imports by uvicorn worker threads are
    # no-ops and dllinit is never called again.
    # Minimal SDL2 preload: just dlopen the dylib on the main thread to trigger
    # SDL's C-constructor (dllinit) before uvicorn spawns background threads.
    # Avoids importing all of essentia (~13s) — essentia imports lazily on first use.
    try:
        import ctypes as _ctypes, glob as _glob, os as _os, sys as _sys
        _sdl_candidates = []
        _meipass = getattr(_sys, '_MEIPASS', None)
        if _meipass:
            _sdl_candidates += _glob.glob(_os.path.join(_meipass, 'libSDL2*.dylib'))
        try:
            import importlib.util as _ilu
            _spec = _ilu.find_spec('essentia')
            if _spec and _spec.origin:
                _sdl_candidates += _glob.glob(
                    _os.path.join(_os.path.dirname(_spec.origin), '.dylibs', 'libSDL2*.dylib'))
        except Exception:
            pass
        if _sdl_candidates:
            _ctypes.CDLL(_sdl_candidates[0])
            log_step("PRELOAD", f"SDL2 dlopen on main thread: {_os.path.basename(_sdl_candidates[0])}")
        else:
            log_step("PRELOAD", "SDL2 dylib not found — skipping preload")
    except Exception as _preload_err:
        log_step("PRELOAD", f"SDL preload skipped: {_preload_err}")

    log_step("SERVER", "Starting Ultrastar Song Generator v2.0")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
