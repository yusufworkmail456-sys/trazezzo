"""Docker Compose manager -- project-level operations."""

from __future__ import annotations
import subprocess
import re
from pathlib import Path

SEARCH_DIRS = [
    "/opt",
    "/srv",
    "/root",
    "/home",
    "/var/lib",
]


def get_compose_projects() -> list[dict]:
    """Find docker-compose.yml files and list projects."""
    projects = []
    seen = set()

    for search_dir in SEARCH_DIRS:
        d = Path(search_dir)
        if not d.exists():
            continue
        try:
            for f in d.rglob("docker-compose.{yml,yaml}"):
                if str(f) in seen:
                    continue
                seen.add(str(f))
                project = _inspect_compose_file(str(f))
                if project:
                    projects.append(project)
        except PermissionError:
            continue

    return projects


def _inspect_compose_file(path: str) -> dict | None:
    """Get project name and service count from compose file."""
    try:
        content = Path(path).read_text()
        service_count = content.count("\n  ") if "\n  " in content else 0

        project_name = Path(path).parent.name

        m = re.search(r'name:\s*["\']?([^"\'\n]+)', content)
        if m:
            project_name = m.group(1).strip()

        return {
            "name": project_name,
            "path": str(path),
            "dir": str(Path(path).parent),
            "services": service_count,
            "status": _get_project_status(str(Path(path).parent), project_name),
        }
    except Exception:
        return None


def _get_project_status(project_dir: str, project_name: str) -> str:
    """Check if compose project is running."""
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", f"{project_dir}/docker-compose.yml", "ps", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return "running"
        return "stopped"
    except Exception:
        return "unknown"


def compose_action(project_dir: str, action: str) -> dict:
    """Run compose up/down/restart/logs for a project."""
    compose_file = None
    for ext in ("docker-compose.yml", "docker-compose.yaml"):
        p = Path(project_dir) / ext
        if p.exists():
            compose_file = str(p)
            break

    if not compose_file:
        return {"success": False, "error": "docker-compose file not found"}

    valid_actions = ["up", "down", "restart", "pull", "stop", "start"]
    if action not in valid_actions:
        return {"success": False, "error": f"Invalid action: {action}"}

    try:
        cmd = ["docker", "compose", "-f", compose_file, action]
        if action == "up":
            cmd.append("-d")

        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=120,
        )
        return {
            "success": result.returncode == 0,
            "action": action,
            "project": Path(project_dir).name,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out (120s)"}
    except FileNotFoundError:
        return {"success": False, "error": "docker compose not found"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
