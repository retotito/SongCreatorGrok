#!/bin/bash
# Test MFA directly with the real transcript
set -e

CONDA="/Users/retokupfer/miniconda3/bin/conda"
TRANSCRIPT=$(cat backend/downloads/mfa_transcript.txt)
WORD_COUNT=$(echo "$TRANSCRIPT" | wc -w | tr -d ' ')
echo "Words: $WORD_COUNT"

# Create temp dirs
TMPDIR=$(mktemp -d)
CORPUS="$TMPDIR/corpus"
OUTPUT="$TMPDIR/output"
mkdir -p "$CORPUS" "$OUTPUT"

# Copy and convert audio
ffmpeg -i "backend/uploads/e304db7c/vocals_u2 - Beatuful Day vocals correctedmp3.mp3" \
    -ar 16000 -ac 1 "$CORPUS/song.wav" -y -loglevel error 2>/dev/null || \
ffmpeg -i "backend/uploads/004731a3/vocals_u2 - Beatuful Day vocals correctedmp3.mp3" \
    -ar 16000 -ac 1 "$CORPUS/song.wav" -y -loglevel error

echo "$TRANSCRIPT" > "$CORPUS/song.txt"

echo "Running MFA align..."
$CONDA run -n mfa mfa align \
    "$CORPUS" english_mfa english_mfa "$OUTPUT" \
    --clean --single_speaker \
    --beam 100 --retry_beam 400 2>&1

EXIT=$?
echo "Exit code: $EXIT"

# Check output
find "$OUTPUT" -name "*.TextGrid" -exec echo "TextGrid: {}" \;

# Cleanup
rm -rf "$TMPDIR"
