"""Cron job manager -- view, add, delete crontab entries."""

from __future__ import annotations
import subprocess


def get_cron_jobs() -> dict:
    """List all crontab entries."""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        raw = result.stdout if result.returncode == 0 else ""
    except Exception as exc:
        return {"jobs": [], "error": str(exc)}

    jobs = []
    for i, line in enumerate(raw.split("\n")):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) >= 6:
            jobs.append({
                "index": i,
                "schedule": " ".join(parts[:5]),
                "command": parts[5],
                "raw": line,
            })
        elif len(parts) >= 1:
            jobs.append({
                "index": i,
                "schedule": "@reboot" if parts[0] == "@reboot" else "",
                "command": line,
                "raw": line,
            })

    return {"jobs": jobs, "count": len(jobs)}


def add_cron_job(schedule: str, command: str) -> dict:
    """Add a new crontab entry."""
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        current = result.stdout if result.returncode == 0 else ""
        new_line = f"{schedule} {command}"
        new_crontab = current.rstrip("\n") + "\n" + new_line + "\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, timeout=5)
        return {"success": True, "entry": new_line}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def delete_cron_job(index: int) -> dict:
    """Delete a crontab entry by index."""
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.split("\n") if result.returncode == 0 else []
        if index < 0 or index >= len(lines):
            return {"success": False, "error": "Invalid index"}
        deleted = lines.pop(index)
        new_crontab = "\n".join(lines)
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, timeout=5)
        return {"success": True, "deleted": deleted.strip()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
