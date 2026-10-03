"""Cron job manager -- view, add, delete crontab entries."""

from __future__ import annotations
import subprocess


def get_systemd_timers() -> dict:
    """List systemd timers (both enabled and active)."""
    timers = []
    try:
        result = subprocess.run(
            ["systemctl", "list-timers", "--all", "--no-pager", "--output=json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            data = json.loads(result.stdout)
            for t in data:
                timers.append({
                    "unit": t.get("unit", ""),
                    "activates": t.get("activates", ""),
                    "next_elapse": t.get("next_elapse", ""),
                    "last_trigger": t.get("last_trigger", ""),
                    "next_elapse_monotonic": "",
                })
    except Exception:
        pass

    # Fallback: parse text output
    if not timers:
        try:
            result = subprocess.run(
                ["systemctl", "list-timers", "--all", "--no-pager"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.split("\n")[1:]:  # skip header
                if line.strip() and "timer" in line.lower():
                    parts = line.split()
                    if len(parts) >= 5:
                        timers.append({
                            "unit": parts[-2] if len(parts) >= 2 else "",
                            "activates": parts[-1] if parts else "",
                            "next_elapse": " ".join(parts[:3]) if len(parts) >= 3 else "",
                            "last_trigger": "",
                            "next_elapse_monotonic": "",
                        })
        except Exception:
            pass

    return {"timers": timers, "count": len(timers)}


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
