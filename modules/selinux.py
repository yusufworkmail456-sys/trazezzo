"""SELinux module — status and mode management."""

from __future__ import annotations

import subprocess
import shutil
from pathlib import Path


def get_selinux_status() -> dict:
    """Get SELinux status."""
    if not shutil.which("getenforce"):
        return {"available": False, "installed": False}

    try:
        result = subprocess.run(["getenforce"], capture_output=True, text=True, timeout=5)
        mode = result.stdout.strip()

        # Get detailed status
        detailed = {}
        if shutil.which("sestatus"):
            result = subprocess.run(["sestatus"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.strip().split("\n"):
                if ":" in line:
                    parts = line.split(":", 1)
                    detailed[parts[0].strip()] = parts[1].strip()

        return {
            "available": True,
            "installed": True,
            "mode": mode,
            "detailed": detailed,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def set_selinux_mode(mode: str) -> dict:
    """Set SELinux mode (enforcing/permissive/disabled)."""
    allowed = {"enforcing", "permissive", "disabled"}
    if mode not in allowed:
        return {"error": f"Mode '{mode}' not allowed"}
    try:
        result = subprocess.run(["setenforce", mode], capture_output=True, text=True, timeout=5)
        return {
            "success": result.returncode == 0,
            "mode": mode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except Exception as exc:
        return {"error": str(exc)}
