"""Software updates module — apt/dnf package management."""

from __future__ import annotations

import subprocess
import shutil


def detect_package_manager() -> str | None:
    """Detect available package manager."""
    for pm in ("apt", "dnf", "yum", "pacman", "zypper"):
        if shutil.which(pm):
            return pm
    return None


def get_updates() -> dict:
    """List available updates."""
    pm = detect_package_manager()
    if not pm:
        return {"available": False, "updates": []}

    updates = []
    if pm == "apt":
        try:
            # Check if apt update has been run
            result = subprocess.run(
                ["apt", "list", "--upgradable"],
                capture_output=True, text=True, timeout=15,
            )
            for line in result.stdout.strip().split("\n"):
                if "/" in line and "upgradable" in line:
                    parts = line.split("/")
                    name = parts[0]
                    updates.append({"name": name, "raw": line})
        except Exception:
            pass
    elif pm in ("dnf", "yum"):
        try:
            result = subprocess.run(
                [pm, "check-update"],
                capture_output=True, text=True, timeout=15,
            )
            for line in result.stdout.strip().split("\n"):
                if line.strip() and not line.startswith("Last metadata"):
                    parts = line.split()
                    if len(parts) >= 2:
                        updates.append({"name": parts[0], "version": parts[1]})
        except Exception:
            pass

    return {
        "available": True,
        "package_manager": pm,
        "updates": updates,
        "count": len(updates),
    }


def apply_updates() -> dict:
    """Apply all available updates."""
    pm = detect_package_manager()
    if not pm:
        return {"error": "No package manager detected"}

    try:
        if pm == "apt":
            result = subprocess.run(
                ["apt", "update", "-y"],
                capture_output=True, text=True, timeout=120,
            )
            result2 = subprocess.run(
                ["apt", "upgrade", "-y"],
                capture_output=True, text=True, timeout=300,
            )
            return {
                "success": result2.returncode == 0,
                "stdout": result2.stdout[-2000:],
                "stderr": result2.stderr[-2000:],
            }
        elif pm in ("dnf", "yum"):
            result = subprocess.run(
                [pm, "upgrade", "-y"],
                capture_output=True, text=True, timeout=300,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
    except Exception as exc:
        return {"error": str(exc)}
