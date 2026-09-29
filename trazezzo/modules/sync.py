"""Sync trigger module — manual warm→cold archive + cron schedule."""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from trazezzo.agent.store.sqlite_warm import get_store
from trazezzo.config import DATA_DIR, WARM_RETENTION_DAYS

log = logging.getLogger("trazezzo.sync")

ARCHIVE_DIR = DATA_DIR / "archives"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def get_sync_status() -> dict:
    """Get sync/archive status."""
    archives = []
    if ARCHIVE_DIR.exists():
        for f in sorted(ARCHIVE_DIR.iterdir()):
            if f.is_file():
                archives.append({
                    "name": f.name,
                    "size_mb": round(f.stat().st_size / 1e6, 2),
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })

    store = get_store()
    total = store.count()

    return {
        "warm_store": {
            "path": str(DATA_DIR / "events.db"),
            "size_mb": round((DATA_DIR / "events.db").stat().st_size / 1e6, 2) if (DATA_DIR / "events.db").exists() else 0,
            "events": total,
            "retention_days": WARM_RETENTION_DAYS,
        },
        "archives": archives,
        "archive_count": len(archives),
        "archive_dir": str(ARCHIVE_DIR),
    }


def trigger_sync() -> dict:
    """Manually trigger archive of warm store events older than retention period."""
    store = get_store()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WARM_RETENTION_DAYS)

    # Query events to archive
    events = store.query(until=cutoff, limit=100000)
    if not events:
        return {"success": True, "message": "No events to archive", "archived": 0}

    # Export to JSON
    export_data = {
        "archive_time": now.isoformat(),
        "event_count": len(events),
        "date_range": {
            "start": min(e.ts for e in events).isoformat() if events else None,
            "end": max(e.ts for e in events).isoformat() if events else None,
        },
        "events": [
            {
                "ts": e.ts.isoformat(),
                "type": e.type.value,
                "source": e.source,
                "actor_kind": e.actor.kind.value,
                "actor_id": e.actor.id,
                "actor_session": e.actor.session,
                "message": e.message,
                "severity": e.severity,
                "payload": e.payload,
            }
            for e in events
        ],
    }

    # Write + gzip
    archive_name = f"events_{now.strftime('%Y%m%d_%H%M%S')}.json.gz"
    archive_path = ARCHIVE_DIR / archive_name
    json_str = json.dumps(export_data, indent=2, default=str)
    with gzip.open(archive_path, "wt", encoding="utf-8") as f:
        f.write(json_str)

    # Prune archived events from warm store
    store._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff.isoformat(),))
    store._conn.commit()

    log.info("Archived %d events to %s", len(events), archive_path)
    return {
        "success": True,
        "archived": len(events),
        "archive_file": str(archive_path),
        "archive_size_mb": round(archive_path.stat().st_size / 1e6, 2),
    }


def get_cron_status() -> dict:
    """Check if trazezzo sync cron is installed."""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        crontab = result.stdout if result.returncode == 0 else ""
        trazezzo_lines = [l for l in crontab.split("\n") if "trazezzo" in l.lower()]
        return {
            "has_cron": bool(trazezzo_lines),
            "cron_lines": trazezzo_lines,
        }
    except Exception:
        return {"has_cron": False, "cron_lines": []}


def setup_cron(schedule: str = "0 3 * * 0") -> dict:
    """Install weekly sync cron job (default: every Sunday 3am).

    Args:
        schedule: cron schedule expression (5 fields: min hour day month dow)
    """
    venv_python = "/root/hermes-fullset/trazezzo/.venv/bin/python"
    cmd = f"{venv_python} -c \"from trazezzo.modules.sync import trigger_sync; trigger_sync()\""

    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        crontab = result.stdout if result.returncode == 0 else ""

        # Remove existing trazezzo lines
        lines = [l for l in crontab.split("\n") if "trazezzo" not in l.lower() and l.strip()]

        # Add new entry
        new_line = f"{schedule} {cmd} # trazezzo-sync"
        lines.append(new_line)

        new_crontab = "\n".join(lines) + "\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, timeout=5)

        return {"success": True, "schedule": schedule, "command": cmd}
    except Exception as exc:
        return {"error": str(exc)}


def remove_cron() -> dict:
    """Remove trazezzo sync cron job."""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        crontab = result.stdout if result.returncode == 0 else ""
        lines = [l for l in crontab.split("\n") if "trazezzo" not in l.lower()]
        new_crontab = "\n".join(lines) + "\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, timeout=5)
        return {"success": True}
    except Exception as exc:
        return {"error": str(exc)}
