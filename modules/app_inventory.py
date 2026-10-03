"""App inventory — auto-detect apps from nginx, systemd, ports.
Manual overrides: repo link + description per app."""

from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime

from trazezzo.config import DATA_DIR

# ── Storage ─────────────────────────────────────────────────────────

APPS_FILE = DATA_DIR / "app_inventory.json"


def _load_overrides() -> dict:
    """Load manual overrides (repo links + descriptions)."""
    try:
        if APPS_FILE.exists():
            return json.loads(APPS_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_overrides(data: dict) -> None:
    """Save manual overrides."""
    APPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPS_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(APPS_FILE, 0o600)


def _load_tracked_repos() -> list[dict]:
    """Load tracked repos for dropdown."""
    repos_file = DATA_DIR / "tracked_repos.json"
    try:
        if repos_file.exists():
            return json.loads(repos_file.read_text())
    except Exception:
        pass
    return []


# ── Auto-detect ──────────────────────────────────────────────────────

def get_app_inventory() -> list[dict]:
    """Auto-detect apps from nginx + systemd + ports.
    Merge with manual overrides (repo link + description).
    Group by port (multiple paths = one app).
    """
    from trazezzo.modules.domain_inventory import get_domain_inventory, _build_port_service_map

    raw_domains = get_domain_inventory()
    port_service_map = _build_port_service_map()
    overrides = _load_overrides()

    # Group by port
    apps_by_port: dict[int, dict] = {}
    no_port_apps: list[dict] = []

    for d in raw_domains:
        port = d.get("port")
        if not port:
            no_port_apps.append(d)
            continue

        if port not in apps_by_port:
            svc_info = port_service_map.get(port, {})
            # Check for manual override
            override = overrides.get(str(port), {})

            apps_by_port[port] = {
                "port": port,
                "domain": d["domain"],
                "paths": [d["path"]],
                "service_name": svc_info.get("service", d.get("service_name", "")),
                "service_status": svc_info.get("status", d.get("service_status", "")),
                "auto_repo": svc_info.get("repo", ""),
                "manual_repo": override.get("repo", ""),
                "description": override.get("description", ""),
                "ssl": d.get("ssl", {}),
            }
        else:
            # Add path to existing app
            if d["path"] not in apps_by_port[port]["paths"]:
                apps_by_port[port]["paths"].append(d["path"])

    apps = list(apps_by_port.values())
    apps.sort(key=lambda x: x.get("port", 0))

    # Add no-port apps
    for d in no_port_apps:
        apps.append({
            "port": None,
            "domain": d["domain"],
            "paths": [d["path"]],
            "service_name": d.get("service_name", ""),
            "service_status": d.get("service_status", ""),
            "auto_repo": d.get("repo", ""),
            "manual_repo": "",
            "description": "",
            "ssl": d.get("ssl", {}),
        })

    return apps


def get_app_context(port: int) -> dict:
    """Get full context for a specific app (for agent session)."""
    apps = get_app_inventory()
    for app in apps:
        if app.get("port") == port:
            repo = app.get("manual_repo") or app.get("auto_repo", "")
            return {
                "port": port,
                "domain": app["domain"],
                "paths": app["paths"],
                "service_name": app.get("service_name", ""),
                "service_status": app.get("service_status", ""),
                "repo": repo,
                "description": app.get("description", ""),
                "ssl": app.get("ssl", {}),
            }
    return {"error": f"App on port {port} not found"}


def set_app_override(port: int, repo: str = None, description: str = None) -> dict:
    """Set manual override for an app (repo link and/or description)."""
    overrides = _load_overrides()
    key = str(port)

    if key not in overrides:
        overrides[key] = {}

    if repo is not None:
        if repo == "":
            overrides[key].pop("repo", None)
        else:
            overrides[key]["repo"] = repo

    if description is not None:
        if description == "":
            overrides[key].pop("description", None)
        else:
            overrides[key]["description"] = description

    if not overrides[key]:
        del overrides[key]

    _save_overrides(overrides)
    return {"success": True, "port": port, "overrides": overrides.get(key, {})}


def get_available_repos() -> list[str]:
    """Get list of tracked repo full_names for dropdown."""
    repos = _load_tracked_repos()
    return [r.get("full_name", "") for r in repos if r.get("full_name", "")]
