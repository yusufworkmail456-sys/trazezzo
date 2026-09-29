"""File editor module — browse, read, write files via web UI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# ── Allowed root directories for browsing ────────────────────────────
ALLOWED_ROOTS = [
    "/etc",
    "/root",
    "/var/lib/trazezzo",
    "/opt/trazezzo",
]

# ── Dangerous paths that should never be edited ───────────────────────
BLOCKED_PATHS = [
    "/etc/shadow",
    "/etc/gshadow",
    "/root/.hermes/.env",
    "/root/.ssh/id_rsa",
    "/root/.ssh/id_ed25519",
]

MAX_FILE_SIZE = 1024 * 1024  # 1MB


def _is_allowed(path: str) -> bool:
    """Check if path is within allowed roots."""
    real = os.path.realpath(path)
    # Check blocked
    for blocked in BLOCKED_PATHS:
        if real == os.path.realpath(blocked):
            return False
    # Check allowed roots
    for root in ALLOWED_ROOTS:
        if real.startswith(os.path.realpath(root)):
            return True
    return False


def list_dir(path: str = "/etc") -> dict:
    """List directory contents."""
    if not _is_allowed(path):
        return {"error": "Access denied"}

    try:
        p = Path(path)
        if not p.is_dir():
            return {"error": "Not a directory"}

        entries = []
        for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
            try:
                stat = entry.stat()
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "is_dir": entry.is_dir(),
                    "size": stat.st_size if entry.is_file() else 0,
                    "modified": stat.st_mtime,
                    "permissions": oct(stat.st_mode)[-3:],
                })
            except PermissionError:
                continue

        return {
            "path": str(p.resolve()),
            "parent": str(p.parent.resolve()) if str(p.parent) != str(p) else None,
            "entries": entries,
            "allowed": True,
        }
    except Exception as exc:
        return {"error": str(exc)}


def read_file(path: str) -> dict:
    """Read file content."""
    if not _is_allowed(path):
        return {"error": "Access denied"}

    try:
        p = Path(path)
        if not p.is_file():
            return {"error": "Not a file"}

        size = p.stat().st_size
        if size > MAX_FILE_SIZE:
            return {"error": f"File too large ({size} bytes, max {MAX_FILE_SIZE})"}

        # Detect binary
        content_bytes = p.read_bytes()
        if b"\x00" in content_bytes:
            return {"error": "Binary file, cannot edit"}

        content = content_bytes.decode("utf-8", errors="replace")
        return {
            "path": str(p.resolve()),
            "content": content,
            "size": size,
            "permissions": oct(p.stat().st_mode)[-3:],
            "allowed": True,
        }
    except Exception as exc:
        return {"error": str(exc)}


def write_file(path: str, content: str) -> dict:
    """Write content to file. Creates backup first."""
    if not _is_allowed(path):
        return {"error": "Access denied"}

    try:
        p = Path(path)
        if not p.parent.exists():
            return {"error": "Parent directory does not exist"}

        # Create backup
        if p.exists():
            backup = p.with_suffix(p.suffix + ".trazezzo.bak")
            shutil.copy2(str(p), str(backup))

        p.write_text(content)

        return {
            "path": str(p.resolve()),
            "size": len(content.encode("utf-8")),
            "backup": str(p.with_suffix(p.suffix + ".trazezzo.bak")) if p.exists() else None,
            "success": True,
        }
    except Exception as exc:
        return {"error": str(exc)}


def upload_file(dest_dir: str, filename: str, content: bytes) -> dict:
    """Upload/save a file to a directory. Content is raw bytes."""
    if not _is_allowed(dest_dir):
        return {"error": "Access denied"}

    # Sanitize filename — basename only, no path traversal
    filename = os.path.basename(filename)
    if not filename or filename.startswith("."):
        return {"error": "Invalid filename"}

    dest_path = os.path.join(dest_dir, filename)
    if not _is_allowed(dest_path):
        return {"error": "Access denied"}

    try:
        p = Path(dest_path)
        if not p.parent.exists():
            return {"error": "Parent directory does not exist"}

        # Check size
        if len(content) > MAX_FILE_SIZE:
            return {"error": f"File too large ({len(content)} bytes, max {MAX_FILE_SIZE})"}

        # Backup if exists
        if p.exists():
            backup = p.with_suffix(p.suffix + ".trazezzo.bak")
            shutil.copy2(str(p), str(backup))

        p.write_bytes(content)
        return {
            "path": str(p.resolve()),
            "size": len(content),
            "backup": str(p.with_suffix(p.suffix + ".trazezzo.bak")) if p.exists() else None,
            "success": True,
        }
    except Exception as exc:
        return {"error": str(exc)}


def create_dir(path: str) -> dict:
    """Create a new directory."""
    if not _is_allowed(path):
        return {"error": "Access denied"}

    try:
        p = Path(path)
        if p.exists():
            return {"error": "Directory already exists"}
        p.mkdir(parents=True, exist_ok=False)
        return {
            "path": str(p.resolve()),
            "success": True,
        }
    except Exception as exc:
        return {"error": str(exc)}


def delete_file(path: str) -> dict:
    """Delete a file (with backup)."""
    if not _is_allowed(path):
        return {"error": "Access denied"}

    try:
        p = Path(path)
        if not p.exists():
            return {"error": "File not found"}

        # Backup before delete
        backup = p.with_suffix(p.suffix + ".trazezzo.bak")
        shutil.copy2(str(p), str(backup))
        p.unlink()

        return {"success": True, "backup": str(backup)}
    except Exception as exc:
        return {"error": str(exc)}
