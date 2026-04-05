#!/usr/bin/env python3
"""Convert a reference Ultrastar .txt to millisecond timings.

Also compares against AI syllable_timings (from session JSON) if available.

Usage:
    python convert_ref_to_ms.py                              # latest ref_*.json
    python convert_ref_to_ms.py <ref_*.json>                 # specific ref JSON
    python convert_ref_to_ms.py --ref-only <ultrastar.txt>   # just convert .txt
    python convert_ref_to_ms.py --session <session.json>     # from session file

Output: reference_ms_<id>.json with timing in seconds for each syllable.
"""
import json, os, re, sys, statistics, glob

# ── Helpers ──────────────────────────────────────────────────
def beat_to_sec(beat, bpm, gap_ms):
    """Convert Ultrastar beat to absolute seconds."""
    return gap_ms / 1000.0 + beat * 15.0 / bpm


def clean(w):
    return re.sub(r"[^\w']", '', w.lower().replace('\u2019', "'"))


def parse_ultrastar(content):
    """Parse Ultrastar .txt content into headers + notes."""
    headers = {}
    notes = []
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = re.match(r"#(\w+):(.*)", line)
            if m:
                headers[m.group(1).upper()] = m.group(2).strip()
            continue
        if line.startswith((':','F:','*')):
            is_rap = line.startswith('F:')
            is_golden = line.startswith('*')
            prefix = 'F:' if is_rap else ('*' if is_golden else ':')
            parts = line[len(prefix):].strip().split(None, 3)
            if len(parts) >= 3:
                notes.append({
                    "start_beat": int(parts[0]),
                    "duration": int(parts[1]),
                    "pitch": int(parts[2]),
                    "syllable": parts[3] if len(parts) > 3 else "",
                    "is_rap": is_rap,
                })
        elif line.startswith('-'):
            notes.append({"type": "break", "syllable": "~"})
        elif line.startswith('E'):
            break

    bpm = float(headers.get("BPM", "0").replace(",", "."))
    gap = float(headers.get("GAP", "0").replace(",", "."))
    return {"bpm": bpm, "gap": gap, "headers": headers, "notes": notes}


# ── Convert reference to ms ─────────────────────────────────
def ref_to_ms(parsed):
    """Convert parsed Ultrastar notes to millisecond-based entries."""
    bpm = parsed["bpm"]
    gap = parsed["gap"]
    entries = []
    for n in parsed["notes"]:
        if n.get("type") == "break":
            continue
        start_sec = beat_to_sec(n["start_beat"], bpm, gap)
        end_sec = beat_to_sec(n["start_beat"] + n["duration"], bpm, gap)
        entries.append({
            "syllable": n["syllable"],
            "start": round(start_sec, 4),
            "end": round(end_sec, 4),
            "pitch": n["pitch"],
            "is_rap": n.get("is_rap", False),
            "start_beat": n["start_beat"],
            "duration_beats": n["duration"],
        })
    return entries


# ── Compare AI timings vs reference ms timings ──────────────
def compare_ms(ai_timings, ref_ms):
    """Compare AI syllable_timings vs reference ms entries by word content."""
    # Build clean lists
    ai_list = []
    for t in ai_timings:
        c = clean(t.get("syllable", ""))
        if c:
            ai_list.append({"syl": t["syllable"], "clean": c, "start": t["start"], "end": t["end"]})

    ref_list = []
    for r in ref_ms:
        syl = r["syllable"]
        if syl.strip() == "~":
            continue
        c = clean(syl)
        if c:
            ref_list.append({"syl": syl, "clean": c, "start": r["start"], "end": r["end"], "pitch": r["pitch"]})

    # Sequential word-content matching (with lookahead)
    a_idx = r_idx = 0
    matched = []

    while a_idx < len(ai_list) and r_idx < len(ref_list):
        ac = ai_list[a_idx]["clean"]
        rc = ref_list[r_idx]["clean"]

        if ac == rc:
            dt = ai_list[a_idx]["start"] - ref_list[r_idx]["start"]
            matched.append({
                "syllable": ai_list[a_idx]["syl"],
                "ai_start": ai_list[a_idx]["start"],
                "ref_start": ref_list[r_idx]["start"],
                "dt": round(dt, 4),
                "abs_dt": round(abs(dt), 4),
            })
            a_idx += 1
            r_idx += 1
        else:
            found = False
            for look in range(1, 8):
                if r_idx + look < len(ref_list) and ref_list[r_idx + look]["clean"] == ac:
                    r_idx += look
                    found = True
                    break
            if not found:
                for look in range(1, 8):
                    if a_idx + look < len(ai_list) and ai_list[a_idx + look]["clean"] == rc:
                        a_idx += look
                        found = True
                        break
            if not found:
                a_idx += 1
                r_idx += 1

    if not matched:
        return {"matched": 0, "message": "No syllables matched"}

    dts = [m["dt"] for m in matched]
    abs_dts = [m["abs_dt"] for m in matched]

    return {
        "matched": len(matched),
        "total_ai": len(ai_list),
        "total_ref": len(ref_list),
        "mean_error_sec": round(statistics.mean(abs_dts), 4),
        "median_error_sec": round(statistics.median(abs_dts), 4),
        "mean_drift_sec": round(statistics.mean(dts), 4),
        "max_error_sec": round(max(abs_dts), 4),
        "within_100ms": sum(1 for d in abs_dts if d <= 0.1),
        "within_200ms": sum(1 for d in abs_dts if d <= 0.2),
        "within_500ms": sum(1 for d in abs_dts if d <= 0.5),
        "within_1s": sum(1 for d in abs_dts if d <= 1.0),
        "over_1s": sum(1 for d in abs_dts if d > 1.0),
        "over_2s": sum(1 for d in abs_dts if d > 2.0),
        "pct_within_200ms": round(sum(1 for d in abs_dts if d <= 0.2) / len(abs_dts) * 100, 1),
        "pct_within_500ms": round(sum(1 for d in abs_dts if d <= 0.5) / len(abs_dts) * 100, 1),
        "details": matched,
    }


# ── Convert ref_*.json (beat-based comparison) to ms ────────
def convert_ref_json(ref_json_path):
    """Convert a ref_*.json file to ms-based comparison.
    
    These files have note_diffs with ai.start/ref.start in beats,
    plus ai_bpm/ref_bpm and ai_gap/ref_gap.
    """
    with open(ref_json_path) as f:
        data = json.load(f)

    comp = data["comparison"]
    ai_bpm = comp["ai_bpm"]
    ref_bpm = comp["ref_bpm"]
    ai_gap = comp["ai_gap"]
    ref_gap = comp["ref_gap"]

    diffs = comp["note_diffs"]
    matched = []
    abs_dts = []

    for d in diffs:
        ai = d["ai"]
        ref = d["ref"]
        syl_ai = d.get("syllable_ai", "")
        syl_ref = d.get("syllable_ref", "")

        ai_start_sec = beat_to_sec(ai["start"], ai_bpm, ai_gap)
        ai_end_sec = beat_to_sec(ai["start"] + ai["duration"], ai_bpm, ai_gap)
        ref_start_sec = beat_to_sec(ref["start"], ref_bpm, ref_gap)
        ref_end_sec = beat_to_sec(ref["start"] + ref["duration"], ref_bpm, ref_gap)

        dt = ai_start_sec - ref_start_sec
        matched.append({
            "syllable_ai": syl_ai,
            "syllable_ref": syl_ref,
            "ai_start": round(ai_start_sec, 4),
            "ai_end": round(ai_end_sec, 4),
            "ref_start": round(ref_start_sec, 4),
            "ref_end": round(ref_end_sec, 4),
            "dt": round(dt, 4),
            "abs_dt": round(abs(dt), 4),
            "pitch_diff": d.get("pitch_diff", 0),
        })
        abs_dts.append(abs(dt))

    return {
        "source": os.path.basename(ref_json_path),
        "metadata": data.get("metadata", {}),
        "ai_bpm": ai_bpm, "ref_bpm": ref_bpm,
        "ai_gap": ai_gap, "ref_gap": ref_gap,
        "matched": len(matched),
        "mean_error_sec": round(statistics.mean(abs_dts), 4) if abs_dts else 0,
        "median_error_sec": round(statistics.median(abs_dts), 4) if abs_dts else 0,
        "mean_drift_sec": round(statistics.mean([m["dt"] for m in matched]), 4) if matched else 0,
        "max_error_sec": round(max(abs_dts), 4) if abs_dts else 0,
        "within_100ms": sum(1 for d in abs_dts if d <= 0.1),
        "within_200ms": sum(1 for d in abs_dts if d <= 0.2),
        "within_500ms": sum(1 for d in abs_dts if d <= 0.5),
        "within_1s": sum(1 for d in abs_dts if d <= 1.0),
        "over_1s": sum(1 for d in abs_dts if d > 1.0),
        "over_2s": sum(1 for d in abs_dts if d > 2.0),
        "pct_within_200ms": round(sum(1 for d in abs_dts if d <= 0.2) / len(abs_dts) * 100, 1) if abs_dts else 0,
        "pct_within_500ms": round(sum(1 for d in abs_dts if d <= 0.5) / len(abs_dts) * 100, 1) if abs_dts else 0,
        "details": matched,
    }


# ── Main ─────────────────────────────────────────────────────
def main():
    base_dir = os.path.dirname(__file__)
    sessions_dir = os.path.join(base_dir, "sessions")
    downloads_dir = os.path.join(base_dir, "downloads")
    ref_songs_dir = os.path.join(base_dir, "reference_songs")

    # Find session with reference + result
    session = None
    ref_content = None

    if len(sys.argv) > 1 and sys.argv[1] == "--ref-only":
        # Just convert a reference Ultrastar file
        ref_path = sys.argv[2]
        with open(ref_path, "r") as f:
            ref_content = f.read()
        parsed = parse_ultrastar(ref_content)
        ref_ms = ref_to_ms(parsed)
        out_path = ref_path.replace(".txt", "_ms.json")
        with open(out_path, "w") as f:
            json.dump({
                "bpm": parsed["bpm"],
                "gap": parsed["gap"],
                "headers": parsed["headers"],
                "notes_ms": ref_ms,
            }, f, indent=2)
        print(f"Wrote {len(ref_ms)} notes to {out_path}")
        print(f"  BPM: {parsed['bpm']}, GAP: {parsed['gap']}ms")
        print(f"  Time range: {ref_ms[0]['start']:.3f}s - {ref_ms[-1]['end']:.3f}s")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--session":
        # Use a specific session JSON
        with open(sys.argv[2], "r") as f:
            session = json.load(f)
        _run_session_comparison(session, downloads_dir)
        return

    # Default: use ref_*.json files (beat-based comparison → ms)
    ref_path = None
    if len(sys.argv) > 1:
        ref_path = sys.argv[1]
    else:
        # Find latest ref_*.json
        files = sorted(glob.glob(os.path.join(ref_songs_dir, "ref_*.json")),
                       key=os.path.getmtime, reverse=True)
        if files:
            ref_path = files[0]

    if not ref_path or not os.path.exists(ref_path):
        print("No reference file found.")
        print("Usage:")
        print("  python convert_ref_to_ms.py                              # latest ref_*.json")
        print("  python convert_ref_to_ms.py <ref_*.json>                 # specific ref JSON")
        print("  python convert_ref_to_ms.py --ref-only <ultrastar.txt>   # just convert .txt")
        print("  python convert_ref_to_ms.py --session <session.json>     # from session file")
        return

    stats = convert_ref_json(ref_path)
    _print_stats(stats)

    # Save
    basename = os.path.splitext(os.path.basename(ref_path))[0]
    out_path = os.path.join(downloads_dir, f"comparison_ms_{basename}.json")
    with open(out_path, "w") as f:
        json.dump({
            "source": stats["source"],
            "metadata": stats["metadata"],
            "ai_bpm": stats["ai_bpm"], "ref_bpm": stats["ref_bpm"],
            "ai_gap": stats["ai_gap"], "ref_gap": stats["ref_gap"],
            "summary": {k: v for k, v in stats.items() if k not in ("details", "source", "metadata", "ai_bpm", "ref_bpm", "ai_gap", "ref_gap")},
            "note_comparisons": stats["details"],
        }, f, indent=2)
    print(f"\n  Full comparison → {out_path}")


def _print_stats(stats):
    """Print timing comparison stats."""
    print(f"\n{'='*60}")
    print(f"TIMING COMPARISON (ms vs ms, BPM-independent)")
    print(f"{'='*60}")
    meta = stats.get("metadata", {})
    if meta:
        print(f"  Song:  {meta.get('artist','?')} - {meta.get('title','?')}")
    print(f"  AI  BPM={stats['ai_bpm']}, GAP={stats['ai_gap']}ms")
    print(f"  Ref BPM={stats['ref_bpm']}, GAP={stats['ref_gap']}ms")
    print(f"  Matched:      {stats['matched']} notes")
    print(f"  Mean |error|: {stats['mean_error_sec']:.3f}s")
    print(f"  Median:       {stats['median_error_sec']:.3f}s")
    print(f"  Mean drift:   {stats['mean_drift_sec']:+.3f}s")
    print(f"  Max error:    {stats['max_error_sec']:.3f}s")
    print(f"  ≤100ms: {stats['within_100ms']} | ≤200ms: {stats['within_200ms']} ({stats['pct_within_200ms']}%) | ≤500ms: {stats['within_500ms']} ({stats['pct_within_500ms']}%)")
    print(f"  ≤1s: {stats['within_1s']} | >1s: {stats['over_1s']} | >2s: {stats['over_2s']}")

    details = stats.get("details", [])
    if details:
        print(f"\n  First 5:")
        for m in details[:5]:
            syl = m.get('syllable_ai', m.get('syllable', '?'))
            print(f"    {syl:>15s}  AI={m['ai_start']:.3f}s  Ref={m['ref_start']:.3f}s  Δ={m['dt']:+.3f}s")
        if len(details) > 10:
            print(f"  Last 5:")
            for m in details[-5:]:
                syl = m.get('syllable_ai', m.get('syllable', '?'))
                print(f"    {syl:>15s}  AI={m['ai_start']:.3f}s  Ref={m['ref_start']:.3f}s  Δ={m['dt']:+.3f}s")


def _run_session_comparison(session, downloads_dir):
    """Compare AI syllable_timings from a session vs reference content."""
    ref_content = session.get("reference_content", "")
    if not ref_content:
        print("Session has no reference_content")
        return

    parsed = parse_ultrastar(ref_content)
    ref_ms = ref_to_ms(parsed)
    sid = session.get("id", "unknown")

    out_path = os.path.join(downloads_dir, f"reference_ms_{sid}.json")
    with open(out_path, "w") as f:
        json.dump({
            "session_id": sid,
            "ref_bpm": parsed["bpm"],
            "ref_gap": parsed["gap"],
            "notes_ms": ref_ms,
        }, f, indent=2)
    print(f"Reference → ms: {len(ref_ms)} notes → {out_path}")

    result = session.get("result", {})
    ai_timings = result.get("syllable_timings")
    if not ai_timings:
        print("No AI syllable_timings in session — skipping comparison.")
        return

    stats = compare_ms(ai_timings, ref_ms)
    stats["ai_bpm"] = result.get("bpm", "?")
    stats["ai_gap"] = result.get("gap_ms", "?")
    stats["ref_bpm"] = parsed["bpm"]
    stats["ref_gap"] = parsed["gap"]
    _print_stats(stats)


if __name__ == "__main__":
    main()
