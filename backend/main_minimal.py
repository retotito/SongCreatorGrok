from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from starlette.middleware.cors import CORSMiddleware
import uvicorn
import os
import tempfile
from pathlib import Path
from typing import Optional
import time
import traceback
import json
import logging

app = FastAPI(title="Ultrastar Song Generator API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True, 
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

@app.get("/test_lyrics")
async def get_test_lyrics():
    """Get test lyrics from frontendTest directory"""
    try:
        # Get parent directory since we're running from backend/
        parent_dir = os.path.dirname(os.getcwd())
        lyrics_path = os.path.join(parent_dir, "frontendTest", "lyrics.txt")
        
        if os.path.exists(lyrics_path):
            return FileResponse(lyrics_path, media_type="text/plain")
        else:
            raise HTTPException(status_code=404, detail="Test lyrics file not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading test lyrics: {str(e)}")

@app.get("/test_vocal")
async def get_test_vocal():
    """Get test vocal file from frontendTest directory"""
    try:
        # Get parent directory since we're running from backend/
        parent_dir = os.path.dirname(os.getcwd())
        vocal_path = os.path.join(parent_dir, "frontendTest", "test_vocal.wav")
        
        if os.path.exists(vocal_path):
            return FileResponse(vocal_path, media_type="audio/wav")
        else:
            raise HTTPException(status_code=404, detail="Test vocal file not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading test vocal: {str(e)}")

@app.get("/test_files")
async def get_test_files():
    """Get information about available test files"""
    try:
        # Get parent directory since we're running from backend/
        parent_dir = os.path.dirname(os.getcwd())
        test_dir = os.path.join(parent_dir, "frontendTest")
        
        files_info = {}
        
        # Check for lyrics file
        lyrics_path = os.path.join(test_dir, "lyrics.txt")
        if os.path.exists(lyrics_path):
            files_info["lyrics"] = {
                "available": True,
                "path": lyrics_path,
                "url": "/test_lyrics"
            }
        else:
            files_info["lyrics"] = {"available": False}
        
        # Check for vocal file
        vocal_path = os.path.join(test_dir, "test_vocal.wav")
        if os.path.exists(vocal_path):
            files_info["vocal"] = {
                "available": True,
                "path": vocal_path,
                "url": "/test_vocal"
            }
        else:
            files_info["vocal"] = {"available": False}
        
        return {
            "status": "success",
            "test_files": files_info
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking test files: {str(e)}")

@app.post("/extract_vocals")
async def extract_vocals(
    source: str = Form(...),
    audio_file: Optional[UploadFile] = File(None)
):
    """Extract vocals from audio - enhanced mock version"""
    try:
        # Simulate processing time for more realistic behavior
        import asyncio
        
        if source == "file" and audio_file:
            # Save the uploaded file temporarily
            temp_dir = tempfile.mkdtemp()
            audio_path = os.path.join(temp_dir, audio_file.filename)
            
            with open(audio_path, "wb") as f:
                content = await audio_file.read()
                f.write(content)
            
            file_size_mb = len(content) / (1024 * 1024)
            
            # Simulate processing time based on file size (2 seconds + 0.5s per MB)
            processing_time = 2 + (file_size_mb * 0.5)
            await asyncio.sleep(min(processing_time, 10))  # Cap at 10 seconds
            
            return {
                "status": "success",
                "message": f"Vocal extraction completed for {audio_file.filename}",
                "vocals_available": True,
                "file_processed": audio_file.filename,
                "file_size_mb": round(file_size_mb, 2),
                "processing_time_seconds": round(processing_time, 1),
                "temp_path": audio_path
            }
        
        elif source == "youtube":
            # Simulate YouTube processing
            await asyncio.sleep(3)
            return {
                "status": "success", 
                "message": "Vocal extraction completed from YouTube (mock)",
                "vocals_available": True,
                "source": "youtube"
            }
        
        else:
            return {
                "status": "error",
                "message": "No valid audio source provided"
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error extracting vocals: {str(e)}")

@app.post("/generate_final_files")
async def generate_final_files(
    title: str = Form(...),
    artist: str = Form(...),
    lyrics: str = Form(...),
    vocals: Optional[UploadFile] = File(None)
):
    """Generate Ultrastar files - simplified version"""
    try:
        # Create a simple mock Ultrastar file
        ultrastar_content = f"""#TITLE:{title}
#ARTIST:{artist}
#MP3:audio.mp3
#BPM:120
#GAP:0

: 0 4 0 Hello
: 5 4 0 world
: 10 4 0 this
: 15 4 0 is
: 20 4 0 a
: 25 4 0 test
E
"""
        
        # Create temporary directory for files
        temp_dir = tempfile.mkdtemp()
        
        # Create the ultrastar file
        ultrastar_path = os.path.join(temp_dir, f"{title}.txt")
        with open(ultrastar_path, 'w', encoding='utf-8') as f:
            f.write(ultrastar_content)
        
        # Create a simple MIDI file (mock)
        midi_path = os.path.join(temp_dir, f"{title}.mid")
        with open(midi_path, 'wb') as f:
            f.write(b'Mock MIDI data')
        
        return {
            "status": "success", 
            "message": "Files generated successfully",
            "temp_dir": temp_dir,
            "files": {
                "ultrastar": ultrastar_path,
                "midi": midi_path
            }
        }
    except Exception as e:
        logging.error(f"Error in generate_final_files: {str(e)}")
        logging.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error generating files: {str(e)}")

@app.get("/download/{file_type}/{filename}")
async def download_file(file_type: str, filename: str):
    """Download generated files"""
    try:
        # For now, return a simple text file
        if file_type == "ultrastar":
            content = f"Mock Ultrastar file: {filename}"
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(content)
                return FileResponse(f.name, media_type="text/plain", filename=filename)
        elif file_type == "midi":
            content = b"Mock MIDI file data"
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.mid', delete=False) as f:
                f.write(content)
                return FileResponse(f.name, media_type="audio/midi", filename=filename)
        else:
            raise HTTPException(status_code=400, detail="Invalid file type")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error downloading file: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)