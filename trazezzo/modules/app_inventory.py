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
    Group by port (dynamic apps) or by path (static apps).
    Each dynamic app gets an HTTP health probe against its local port.
    """
    from trazezzo.modules.domain_inventory import get_domain_inventory, _build_port_service_map

    raw_domains = get_domain_inventory()
    port_service_map = _build_port_service_map()
    overrides = _load_overrides()

    # ── HTTP health probe (local) ────────────────────────────────────
    def probe_health(port: int, path: str) -> dict | None:
        """GET http://127.0.0.1:<port><path> with a short timeout. None for static apps."""
        if not port:
            return None
        import time
        import urllib.request
        import urllib.error
        probe_path = path if (path or "/").startswith("/") else "/" + (path or "")
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{probe_path}",
                headers={"User-Agent": "trazezzo-health-probe/1.0"},
            )
            start = time.monotonic()
            with urllib.request.urlopen(req, timeout=3) as resp:
                latency = round((time.monotonic() - start) * 1000, 1)
                return {"status_code": resp.status, "latency_ms": latency}
        except urllib.error.HTTPError as e:
            return {"status_code": e.code, "latency_ms": None}
        except Exception:
            return {"status_code": None, "latency_ms": None}

    # Group by app_id: "port:9122" for dynamic, "static:/path/" for static
    apps_by_id: dict[str, dict] = {}

    for d in raw_domains:
        port = d.get("port")
        is_static = d.get("is_static", False)

        if is_static:
            # Strip slashes from path for app_id: "static:trazezzo-landing"
            clean_path = d['path'].strip('/')
            app_id = f"static:{clean_path}"
        elif port:
            app_id = f"port:{port}"
        else:
            # Skip entries with neither port nor static flag
            continue

        if app_id not in apps_by_id:
            override = overrides.get(app_id, {})

            if is_static:
                apps_by_id[app_id] = {
                    "app_id": app_id,
                    "port": None,
                    "domain": d["domain"],
                    "paths": [d["path"]],
                    "is_static": True,
                    "static_dir": d.get("static_dir", ""),
                    "service_name": "",
                    "service_status": "",
                    "auto_repo": d.get("repo", ""),
                    "manual_repo": override.get("repo", ""),
                    "description": override.get("description", ""),
                    "ssl": d.get("ssl", {}),
                }
            else:
                svc_info = port_service_map.get(port, {})
                apps_by_id[app_id] = {
                    "app_id": app_id,
                    "port": port,
                    "domain": d["domain"],
                    "paths": [d["path"]],
                    "is_static": False,
                    "static_dir": "",
                    "service_name": svc_info.get("service", d.get("service_name", "")),
                    "service_status": svc_info.get("status", d.get("service_status", "")),
                    "auto_repo": svc_info.get("repo", ""),
                    "manual_repo": override.get("repo", ""),
                    "description": override.get("description", ""),
                    "ssl": d.get("ssl", {}),
                    "health": None,
                }
        else:
            # Add path to existing app
            if d["path"] not in apps_by_id[app_id]["paths"]:
                apps_by_id[app_id]["paths"].append(d["path"])

    apps = list(apps_by_id.values())

    # ── Health probe each dynamic app once (cached per call) ─────────
    # Probe the service root on its local port; fall back to the external
    # nginx path when the root 404s (Streamlit-style apps serve sub-paths).
    probe_cache: dict[int, dict | None] = {}
    for app in apps:
        if app.get("is_static") or not app.get("port"):
            continue
        port = app["port"]
        if port not in probe_cache:
            probe = probe_health(port, "/")
            if probe and probe["status_code"] == 404:
                for ext_path in app.get("paths", []) or []:
                    retry = probe_health(port, ext_path)
                    if retry and retry["status_code"] is not None and retry["status_code"] != 404:
                        probe = retry
                        break
            probe_cache[port] = probe
        probe = probe_cache[port]
        if probe is None:
            app["health"] = {"status": "unknown", "status_code": None, "latency_ms": None}
        elif probe["status_code"] is None:
            # Nothing answered on the port at all
            if app.get("service_status") == "active":
                app["health"] = {"status": "degraded", "status_code": None, "latency_ms": None,
                                 "note": "service active but no HTTP response"}
            else:
                app["health"] = {"status": "down", "status_code": None, "latency_ms": None}
        elif 200 <= probe["status_code"] < 400:
            app["health"] = {"status": "up", "status_code": probe["status_code"], "latency_ms": probe["latency_ms"]}
        elif probe["status_code"] == 404:
            # Service up but exposes no GET route (webhook-only, API-only)
            app["health"] = {"status": "no_http_route", "status_code": 404, "latency_ms": None,
                             "note": "service responds but exposes no GET route"}
        else:
            app["health"] = {"status": "degraded", "status_code": probe["status_code"], "latency_ms": probe["latency_ms"]}

    # Sort: dynamic apps by port, then static apps by path
    apps.sort(key=lambda x: (x.get("is_static", False), x.get("port", 0) or 0, x.get("paths", [""])[0]))

    return apps


def get_app_context(app_id: str) -> dict:
    """Get full context for a specific app (for agent session)."""
    apps = get_app_inventory()
    for app in apps:
        if app.get("app_id") == app_id:
            repo = app.get("manual_repo") or app.get("auto_repo", "")
            return {
                "app_id": app_id,
                "port": app.get("port"),
                "domain": app["domain"],
                "paths": app["paths"],
                "is_static": app.get("is_static", False),
                "static_dir": app.get("static_dir", ""),
                "service_name": app.get("service_name", ""),
                "service_status": app.get("service_status", ""),
                "repo": repo,
                "description": app.get("description", ""),
                "ssl": app.get("ssl", {}),
            }
    return {"error": f"App {app_id} not found"}


def set_app_override(app_id: str, repo: str = None, description: str = None) -> dict:
    """Set manual override for an app (repo link and/or description)."""
    overrides = _load_overrides()
    key = app_id

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
    return {"success": True, "app_id": app_id, "overrides": overrides.get(key, {})}


def get_available_repos() -> list[str]:
    """Get list of tracked repo full_names for dropdown."""
    repos = _load_tracked_repos()
    return [r.get("full_name", "") for r in repos if r.get("full_name", "")]
