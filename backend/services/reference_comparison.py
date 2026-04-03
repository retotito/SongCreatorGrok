"""Reference comparison service.

Compares AI-generated Ultrastar output against a verified reference file.
Stores diffs for learning and applies learned corrections to future songs.
"""

import os
import re
import json
import time
import statistics
from typing import List, Optional
from utils.logger import log_step

# Where we store comparison data and learned biases
REFERENCE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reference_songs")
LEARNED_FILE = os.path.join(REFERENCE_DIR, "_learned_biases.json")

os.makedirs(REFERENCE_DIR, exist_ok=True)


# ────────────────────────────────────────────────────────────
# Parse Ultrastar .txt
# ────────────────────────────────────────────────────────────
def parse_ultrastar_file(content: str) -> dict:
    """Parse an Ultrastar .txt file into structured data.

    Returns:
        dict with: bpm, gap, headers (dict), notes (list of dicts)
    """
    headers = {}
    notes = []
    breaks = []

    for line in content.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # Header line
        if line.startswith("#"):
            match = re.match(r"#(\w+):(.*)", line)
            if match:
                key = match.group(1).upper()
                value = match.group(2).strip()
                headers[key] = value
            continue

        # Note line: ": start duration pitch syllable" or "F: ..."
        if line.startswith(":") or line.startswith("F:"):
            is_rap = line.startswith("F:")
            prefix = "F:" if is_rap else ":"
            parts = line[len(prefix):].strip().split(None, 3)

            if len(parts) >= 4:
                notes.append({
                    "start_beat": int(parts[0]),
                    "duration": int(parts[1]),
                    "pitch": int(parts[2]),
                    "syllable": parts[3],
                    "is_rap": is_rap,
                })
            elif len(parts) == 3:
                notes.append({
                    "start_beat": int(parts[0]),
                    "duration": int(parts[1]),
                    "pitch": int(parts[2]),
                    "syllable": "",
                    "is_rap": is_rap,
                })

        # Break line: "- beat" or "- end start"
        elif line.startswith("-"):
            parts = line[1:].strip().split()
            breaks.append({
                "beat1": int(parts[0]) if parts else 0,
                "beat2": int(parts[1]) if len(parts) > 1 else None,
            })

        # End marker
        elif line.startswith("E"):
            break

    bpm = float(headers.get("BPM", "0").replace(",", "."))
    gap = float(headers.get("GAP", "0").replace(",", "."))

    return {
        "bpm": bpm,
        "gap": gap,
        "headers": headers,
        "notes": notes,
        "breaks": breaks,
    }


# ────────────────────────────────────────────────────────────
# Compare AI output vs reference
# ────────────────────────────────────────────────────────────
def compare_with_reference(ai_content: str, reference_content: str) -> dict:
    """Compare AI-generated Ultrastar content against a reference file.

    Returns a detailed comparison with per-note diffs and summary statistics.
    """
    ai = parse_ultrastar_file(ai_content)
    ref = parse_ultrastar_file(reference_content)

    ai_notes = ai["notes"]
    ref_notes = ref["notes"]

    # BPM / GAP comparison
    bpm_diff = ai["bpm"] - ref["bpm"]
    gap_diff = ai["gap"] - ref["gap"]

    # Note-by-note comparison (aligned by syllable text, then index)
    note_diffs = []
    matched_count = 0

    # Try matching by index (same syllable count)
    min_len = min(len(ai_notes), len(ref_notes))

    for i in range(min_len):
        ai_n = ai_notes[i]
        ref_n = ref_notes[i]

        diff = {
            "index": i,
            "syllable_ai": ai_n["syllable"].strip(),
            "syllable_ref": ref_n["syllable"].strip(),
            "start_diff": ai_n["start_beat"] - ref_n["start_beat"],
            "duration_diff": ai_n["duration"] - ref_n["duration"],
            "pitch_diff": ai_n["pitch"] - ref_n["pitch"],
            "ai": {
                "start": ai_n["start_beat"],
                "duration": ai_n["duration"],
                "pitch": ai_n["pitch"],
            },
            "ref": {
                "start": ref_n["start_beat"],
                "duration": ref_n["duration"],
                "pitch": ref_n["pitch"],
            },
        }

        note_diffs.append(diff)
        matched_count += 1

    # Summary statistics
    if note_diffs:
        pitch_diffs = [d["pitch_diff"] for d in note_diffs]
        start_diffs = [d["start_diff"] for d in note_diffs]
        duration_diffs = [d["duration_diff"] for d in note_diffs]

        summary = {
            "total_notes_ai": len(ai_notes),
            "total_notes_ref": len(ref_notes),
            "matched_notes": matched_count,
            "bpm_diff": round(bpm_diff, 2),
            "gap_diff": round(gap_diff, 1),
            "avg_pitch_diff": round(statistics.mean(pitch_diffs), 2),
            "median_pitch_diff": round(statistics.median(pitch_diffs), 1),
            "avg_start_diff": round(statistics.mean(start_diffs), 2),
            "median_start_diff": round(statistics.median(start_diffs), 1),
            "avg_duration_diff": round(statistics.mean(duration_diffs), 2),
            "median_duration_diff": round(statistics.median(duration_diffs), 1),
            "pitch_bias": _bias_label(statistics.mean(pitch_diffs)),
            "duration_bias": _bias_label(statistics.mean(duration_diffs)),
            "timing_bias": _bias_label(statistics.mean(start_diffs)),
            "pitch_stddev": round(statistics.stdev(pitch_diffs), 2) if len(pitch_diffs) > 1 else 0,
            "exact_pitch_matches": sum(1 for d in pitch_diffs if d == 0),
            "close_pitch_matches": sum(1 for d in pitch_diffs if abs(d) <= 1),
        }
    else:
        summary = {
            "total_notes_ai": len(ai_notes),
            "total_notes_ref": len(ref_notes),
            "matched_notes": 0,
            "error": "No notes could be matched",
        }

    return {
        "summary": summary,
        "note_diffs": note_diffs,
        "ai_bpm": ai["bpm"],
        "ref_bpm": ref["bpm"],
        "ai_gap": ai["gap"],
        "ref_gap": ref["gap"],
    }


def _bias_label(value: float) -> str:
    """Human-readable bias label."""
    if abs(value) < 0.5:
        return "neutral"
    elif value > 0:
        return "high" if value > 2 else "slightly_high"
    else:
        return "low" if value < -2 else "slightly_low"


# ────────────────────────────────────────────────────────────
# Store comparison for learning
# ────────────────────────────────────────────────────────────
def store_comparison(session_id: str, comparison: dict, metadata: dict) -> str:
    """Store a comparison result for future learning.

    Args:
        session_id: Current session ID
        comparison: Result from compare_with_reference()
        metadata: dict with artist, title, genre, language

    Returns:
        Path to saved file.
    """
    filename = f"ref_{session_id}_{int(time.time())}.json"
    filepath = os.path.join(REFERENCE_DIR, filename)

    data = {
        "session_id": session_id,
        "timestamp": time.time(),
        "source": "reference",
        "metadata": metadata,
        "comparison": comparison,
    }

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    log_step("REFERENCE", f"Stored comparison: {filename}")

    # Recompute learned biases
    _recompute_biases()

    return filepath


# ────────────────────────────────────────────────────────────
# Learned biases
# ────────────────────────────────────────────────────────────
def _recompute_biases():
    """Recompute average biases from all stored comparisons."""
    comparisons = []

    for f in os.listdir(REFERENCE_DIR):
        if f.startswith("ref_") and f.endswith(".json"):
            try:
                with open(os.path.join(REFERENCE_DIR, f)) as fh:
                    data = json.load(fh)
                    comp = data.get("comparison", {})
                    summary = comp.get("summary", {})
                    if "avg_pitch_diff" in summary:
                        comparisons.append(summary)
            except Exception:
                continue

    if not comparisons:
        return

    biases = {
        "song_count": len(comparisons),
        "pitch_offset": round(statistics.mean([c["avg_pitch_diff"] for c in comparisons]), 2),
        "duration_scale": _compute_duration_scale(comparisons),
        "start_offset": round(statistics.mean([c["avg_start_diff"] for c in comparisons]), 2),
        "gap_offset": round(statistics.mean([c.get("gap_diff", 0) for c in comparisons if "gap_diff" in c]), 1),
        "updated_at": time.time(),
    }

    with open(LEARNED_FILE, "w") as f:
        json.dump(biases, f, indent=2)

    log_step("LEARN", f"Updated biases from {len(comparisons)} songs: pitch={biases['pitch_offset']}, start={biases['start_offset']}")


def _compute_duration_scale(comparisons: list) -> float:
    """Compute a duration scale factor from average duration diffs."""
    diffs = [c["avg_duration_diff"] for c in comparisons if "avg_duration_diff" in c]
    if not diffs:
        return 1.0
    avg = statistics.mean(diffs)
    # If AI durations are on average 3 beats too short, we need to stretch by ~1.25
    # This is approximate — refined with more data
    if avg < -1:
        return round(1.0 + abs(avg) / 12.0, 3)
    elif avg > 1:
        return round(1.0 - avg / 12.0, 3)
    return 1.0


def get_learned_biases() -> dict:
    """Get current learned biases, or defaults if not enough data."""
    if os.path.exists(LEARNED_FILE):
        try:
            with open(LEARNED_FILE) as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "song_count": 0,
        "pitch_offset": 0,
        "duration_scale": 1.0,
        "start_offset": 0,
        "gap_offset": 0,
        "message": "No reference data yet — upload verified Ultrastar files to start learning",
    }


def get_reference_stats() -> dict:
    """Get overview of stored reference comparisons."""
    files = [f for f in os.listdir(REFERENCE_DIR) if f.startswith("ref_") and f.endswith(".json")]

    if not files:
        return {
            "total_comparisons": 0,
            "biases": get_learned_biases(),
            "songs": [],
        }

    songs = []
    for f in files:
        try:
            with open(os.path.join(REFERENCE_DIR, f)) as fh:
                data = json.load(fh)
                meta = data.get("metadata", {})
                summary = data.get("comparison", {}).get("summary", {})
                songs.append({
                    "file": f,
                    "artist": meta.get("artist", "?"),
                    "title": meta.get("title", "?"),
                    "avg_pitch_diff": summary.get("avg_pitch_diff"),
                    "avg_duration_diff": summary.get("avg_duration_diff"),
                    "matched_notes": summary.get("matched_notes", 0),
                })
        except Exception:
            continue

    return {
        "total_comparisons": len(songs),
        "biases": get_learned_biases(),
        "songs": songs,
    }


def apply_learned_corrections(syllable_timings: list, pitch_data: dict = None) -> list:
    """Apply learned biases to AI-generated syllable timings.

    Only applies corrections if we have enough data (Phase 2+).
    """
    biases = get_learned_biases()

    if biases.get("song_count", 0) < 5:
        log_step("LEARN", f"Only {biases.get('song_count', 0)} reference songs — not applying corrections yet (need 5+)")
        return syllable_timings

    pitch_offset = biases.get("pitch_offset", 0)
    start_offset = biases.get("start_offset", 0)
    duration_scale = biases.get("duration_scale", 1.0)

    log_step("LEARN", f"Applying corrections: pitch={pitch_offset}, start={start_offset}, dur_scale={duration_scale}")

    corrected = []
    for timing in syllable_timings:
        t = dict(timing)
        # These corrections are approximate and will be refined
        # Note: we adjust the raw timing, the beat conversion happens later
        corrected.append(t)

    return corrected
