"""Logs module — journald browse + filter."""

from __future__ import annotations

import subprocess
from datetime import datetime


def get_journal_logs(
    unit: str | None = None,
    priority: str | None = None,
    since: str | None = None,
    lines: int = 100,
) -> list[dict]:
    """Read journald logs with optional filters."""
    cmd = ["journalctl", "--no-pager", "--output=short-iso"]
    if unit:
        cmd.extend(["--unit", unit])
    if priority:
        cmd.extend(["--priority", priority])
    if since:
        cmd.extend(["--since", since])
    else:
        cmd.extend(["--since", "1 hour ago"])
    cmd.extend(["--lines", str(lines)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        logs = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            logs.append({"line": line})
        return logs
    except Exception:
        return []


def get_journal_units() -> list[str]:
    """List units that have journal entries."""
    try:
        result = subprocess.run(
            ["journalctl", "--list-boots", "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
        boots = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                boots.append(line.strip())
        return boots
    except Exception:
        return []
