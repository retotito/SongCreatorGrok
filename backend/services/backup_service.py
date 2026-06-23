"""
Backup service — pure functions for creating and managing session backups.

Backups are plain UltraStar .txt snapshots stored at:
  SESSIONS_DIR/backups/<session_id>/backup_<unix_ms>.txt

No FastAPI, no global state. All functions take explicit paths/content.
"""

import os
import shutil
import time

MAX_BACKUPS = 20


def get_backup_dir(sessions_dir: str, session_id: str) -> str:
    return os.path.join(sessions_dir, "backups", session_id)


def list_backups(sessions_dir: str, session_id: str) -> list:
    """Return list of backup entries, newest first.

    Each entry: {ts: int, filename: str, size_bytes: int}
    """
    backup_dir = get_backup_dir(sessions_dir, session_id)
    if not os.path.isdir(backup_dir):
        return []

    entries = []
    for fname in os.listdir(backup_dir):
        if not fname.startswith("backup_") or not fname.endswith(".txt"):
            continue
        # Filename formats: backup_<ts>_a.txt (auto), backup_<ts>_m.txt (manual),
        # or legacy backup_<ts>.txt (treated as manual)
        stem = fname[len("backup_"):-len(".txt")]  # e.g. "1234567890_a" or "1234567890"
        parts = stem.rsplit("_", 1)
        try:
            ts = int(parts[0])
        except ValueError:
            continue
        is_auto = len(parts) == 2 and parts[1] == "a"
        fpath = os.path.join(backup_dir, fname)
        try:
            size = os.path.getsize(fpath)
        except OSError:
            size = 0
        entries.append({"ts": ts, "filename": fname, "size_bytes": size, "is_auto": is_auto})

    entries.sort(key=lambda e: e["ts"], reverse=True)
    return entries


def create_backup(sessions_dir: str, session_id: str, txt_content: str, auto: bool = False) -> dict:
    """Write a new backup file and prune if over limit.

    Returns the new entry: {ts, filename, size_bytes, is_auto}
    """
    backup_dir = get_backup_dir(sessions_dir, session_id)
    os.makedirs(backup_dir, exist_ok=True)

    ts = int(time.time() * 1000)
    suffix = "a" if auto else "m"
    filename = f"backup_{ts}_{suffix}.txt"
    fpath = os.path.join(backup_dir, filename)

    with open(fpath, "w", encoding="utf-8") as f:
        f.write(txt_content)

    size = os.path.getsize(fpath)
    prune_backups(sessions_dir, session_id)

    return {"ts": ts, "filename": filename, "size_bytes": size, "is_auto": auto}


def delete_backup(sessions_dir: str, session_id: str, ts: int) -> bool:
    """Delete a single backup by timestamp. Returns True if deleted."""
    backup_dir = get_backup_dir(sessions_dir, session_id)
    # Try both new suffixed formats and legacy format
    for candidate in (f"backup_{ts}_m.txt", f"backup_{ts}_a.txt", f"backup_{ts}.txt"):
        fpath = os.path.join(backup_dir, candidate)
        if os.path.exists(fpath):
            os.remove(fpath)
            return True
    return False


def restore_backup(sessions_dir: str, session_id: str, ts: int) -> str:
    """Read and return the txt_content of a backup. Raises FileNotFoundError if missing."""
    backup_dir = get_backup_dir(sessions_dir, session_id)
    for candidate in (f"backup_{ts}_m.txt", f"backup_{ts}_a.txt", f"backup_{ts}.txt"):
        fpath = os.path.join(backup_dir, candidate)
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                return f.read()
    raise FileNotFoundError(f"Backup {ts} not found for session {session_id}")


def prune_backups(sessions_dir: str, session_id: str, max_keep: int = MAX_BACKUPS):
    """Delete oldest backups if count exceeds max_keep."""
    entries = list_backups(sessions_dir, session_id)  # newest first
    to_delete = entries[max_keep:]
    backup_dir = get_backup_dir(sessions_dir, session_id)
    for entry in to_delete:
        fpath = os.path.join(backup_dir, entry["filename"])
        try:
            os.remove(fpath)
        except OSError:
            pass


def delete_all_backups(sessions_dir: str, session_id: str):
    """Remove entire backup folder for a session. Called on session delete."""
    backup_dir = get_backup_dir(sessions_dir, session_id)
    if os.path.isdir(backup_dir):
        shutil.rmtree(backup_dir, ignore_errors=True)


def get_backup_files_for_storage(sessions_dir: str, session_id: str) -> tuple:
    """Return (file_list, total_size_bytes) for the storage manager.

    file_list: [{label, path, size}]
    """
    entries = list_backups(sessions_dir, session_id)
    backup_dir = get_backup_dir(sessions_dir, session_id)
    files = [
        {
            "label": "backup",
            "path": os.path.join(backup_dir, e["filename"]),
            "size": e["size_bytes"],
        }
        for e in entries
    ]
    total = sum(e["size_bytes"] for e in entries)
    return files, total
