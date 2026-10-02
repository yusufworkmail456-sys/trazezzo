"""Containers module — podman/docker."""

from __future__ import annotations

import subprocess
import json
import shutil


def _get_runtime() -> str | None:
    """Detect available container runtime."""
    for rt in ("podman", "docker"):
        if shutil.which(rt):
            return rt
    return None


def get_containers() -> dict:
    """List running + all containers."""
    rt = _get_runtime()
    if not rt:
        return {"available": False, "runtime": None, "containers": []}

    try:
        result = subprocess.run(
            [rt, "ps", "-a", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        containers = json.loads(result.stdout) if result.stdout.strip() else []
        # podman returns list, docker returns list of objects
        if isinstance(containers, dict):
            containers = containers.get("Containers", [])
        return {
            "available": True,
            "runtime": rt,
            "containers": containers,
            "running": [c for c in containers if c.get("Status", "").lower().startswith("up") or c.get("State", "").lower() == "running"],
            "total": len(containers),
        }
    except Exception as exc:
        return {"available": False, "runtime": rt, "error": str(exc)}


def container_action(container_id: str, action: str) -> dict:
    """Start/stop/restart container."""
    rt = _get_runtime()
    if not rt:
        return {"error": "No container runtime"}
    allowed = {"start", "stop", "restart", "rm"}
    if action not in allowed:
        return {"error": f"Action '{action}' not allowed"}
    try:
        result = subprocess.run(
            [rt, action, container_id],
            capture_output=True, text=True, timeout=30,
        )
        return {
            "container": container_id,
            "action": action,
            "runtime": rt,
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except Exception as exc:
        return {"error": str(exc)}


def get_container_images() -> list:
    """List container images."""
    rt = _get_runtime()
    if not rt:
        return []
    try:
        result = subprocess.run(
            [rt, "images", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        return json.loads(result.stdout) if result.stdout.strip() else []
    except Exception:
        return []
