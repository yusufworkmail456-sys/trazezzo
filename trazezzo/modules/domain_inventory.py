"""Domain & App inventory — auto-detect from nginx, systemd, ports, git repos."""

from __future__ import annotations

import subprocess
import re
import os
from pathlib import Path
from collections import defaultdict


def get_domain_inventory() -> list[dict]:
    """Parse nginx configs to build domain/app inventory.

    For each domain + path:
    - domain (server_name)
    - path (location block)
    - proxy_target (proxy_pass)
    - port (extracted from proxy_pass)
    - service_name (systemd service listening on that port)
    - service_status (active/inactive)
    - repo (auto-detected from service working directory .git)
    - ssl (cert path + days left if parseable)
    """
    domains = []

    # Parse main site configs first to get domain names
    domain_names = set()
    main_configs = []
    for config_dir in ["/etc/nginx/sites-enabled", "/etc/nginx/conf.d"]:
        p = Path(config_dir)
        if p.exists():
            for f in p.iterdir():
                if f.is_file():
                    main_configs.append(f)

    # Parse snippet configs (included location blocks)
    snippet_configs = list(Path("/etc/nginx/snippets").glob("*.conf")) if Path("/etc/nginx/snippets").exists() else []

    all_configs = main_configs + snippet_configs

    # Build a map of port → service from ss/systemctl
    port_service_map = _build_port_service_map()

    for config_file in all_configs:
        try:
            content = config_file.read_text(errors="replace")
        except Exception:
            continue

        # Find server_name in this file
        domain_match = re.search(r'server_name\s+([^;]+);', content)
        if not domain_match:
            continue

        domain_name = domain_match.group(1).strip().split()[0]

        # Find all location blocks with proxy_pass (dynamic apps)
        location_pattern = r'location\s+([^\s{]+)\s*\{([^{}]*?proxy_pass\s+([^;\s]+)\s*;[^{}]*?)\}'
        for loc_match in re.finditer(location_pattern, content, re.DOTALL):
            loc_path = loc_match.group(1).strip()
            proxy_pass = loc_match.group(3).strip().rstrip(';')

            # Extract port from proxy_pass
            port_match = re.search(r':(\d+)', proxy_pass)
            port = int(port_match.group(1)) if port_match else None

            # Lookup service for this port
            service_info = port_service_map.get(port, {}) if port else {}

            domains.append({
                "domain": domain_name,
                "path": loc_path,
                "proxy_target": proxy_pass,
                "port": port,
                "is_static": False,
                "service_name": service_info.get("service", ""),
                "service_status": service_info.get("status", ""),
                "repo": service_info.get("repo", ""),
                "ssl": _check_ssl(domain_name),
                "config_file": str(config_file),
            })

        # Find all location blocks with alias (static files)
        alias_pattern = r'location\s+([^\s{]+)\s*\{([^{}]*?alias\s+([^;\s]+)\s*;[^{}]*?)\}'
        for loc_match in re.finditer(alias_pattern, content, re.DOTALL):
            loc_path = loc_match.group(1).strip()
            alias_path = loc_match.group(3).strip().rstrip('/;')

            domains.append({
                "domain": domain_name,
                "path": loc_path,
                "proxy_target": "",
                "port": None,
                "is_static": True,
                "static_dir": alias_path,
                "service_name": "",
                "service_status": "",
                "repo": _find_git_repo(alias_path),
                "ssl": _check_ssl(domain_name),
                "config_file": str(config_file),
            })

    # Parse snippets without server_name — assume they belong to the default server block
    # (the main domain). Find the primary domain from main configs.
    primary_domain = ""
    for cfg in main_configs:
        try:
            content = cfg.read_text(errors="replace")
            dm = re.search(r'server_name\s+([^;]+);', content)
            if dm:
                primary_domain = dm.group(1).strip().split()[0]
                break
        except Exception:
            continue

    if primary_domain:
        for config_file in snippet_configs:
            try:
                content = config_file.read_text(errors="replace")
            except Exception:
                continue

            # Skip if this snippet has its own server_name
            if re.search(r'server_name\s+', content):
                continue

            # Parse location blocks with proxy_pass (dynamic apps)
            location_pattern = r'location\s+([^\s{]+)\s*\{([^{}]*?proxy_pass\s+([^;\s]+)\s*;[^{}]*?)\}'
            for loc_match in re.finditer(location_pattern, content, re.DOTALL):
                loc_path = loc_match.group(1).strip()
                proxy_pass = loc_match.group(3).strip().rstrip(';')

                port_match = re.search(r':(\d+)', proxy_pass)
                port = int(port_match.group(1)) if port_match else None

                service_info = port_service_map.get(port, {}) if port else {}

                domains.append({
                    "domain": primary_domain,
                    "path": loc_path,
                    "proxy_target": proxy_pass,
                    "port": port,
                    "is_static": False,
                    "service_name": service_info.get("service", ""),
                    "service_status": service_info.get("status", ""),
                    "repo": service_info.get("repo", ""),
                    "ssl": _check_ssl(primary_domain),
                    "config_file": str(config_file),
                })

            # Parse location blocks with alias (static files)
            alias_pattern = r'location\s+([^\s{]+)\s*\{([^{}]*?alias\s+([^;\s]+)\s*;[^{}]*?)\}'
            for loc_match in re.finditer(alias_pattern, content, re.DOTALL):
                loc_path = loc_match.group(1).strip()
                alias_path = loc_match.group(3).strip().rstrip('/;')

                domains.append({
                    "domain": primary_domain,
                    "path": loc_path,
                    "proxy_target": "",
                    "port": None,
                    "is_static": True,
                    "static_dir": alias_path,
                    "service_name": "",
                    "service_status": "",
                    "repo": _find_git_repo(alias_path),
                    "ssl": _check_ssl(primary_domain),
                    "config_file": str(config_file),
                })

    # Deduplicate
    seen = set()
    result = []
    for d in domains:
        key = (d["domain"], d["path"])
        if key not in seen:
            seen.add(key)
            result.append(d)

    return result


def _build_port_service_map() -> dict[int, dict]:
    """Map listening ports to systemd services + git repos."""
    port_map = {}

    # Get listening ports + PIDs
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.split("\n"):
            # Line format: LISTEN 0 128 0.0.0.0:9122 0.0.0.0:* users:(("uvicorn",pid=12345,fd=5))
            port_match = re.search(r':(\d+)\s', line)
            if not port_match:
                continue
            port = int(port_match.group(1))
            pid_match = re.search(r'pid=(\d+)', line)
            if not pid_match:
                continue
            pid = int(pid_match.group(1))

            # Find systemd service for this PID
            service_name = _find_service_for_pid(pid)
            repo = _find_repo_for_pid(pid)

            port_map[port] = {
                "service": service_name,
                "status": _check_service_status(service_name) if service_name else "",
                "repo": repo,
            }
    except Exception:
        pass

    return port_map


def _find_service_for_pid(pid: int) -> str:
    """Find systemd service name for a given PID."""
    try:
        # Prefer /proc/PID/cgroup: walk unit segments bottom-up, skip user session units
        cgroup_path = f"/proc/{pid}/cgroup"
        if os.path.exists(cgroup_path):
            unit_name = None
            with open(cgroup_path) as f:
                for line in f:
                    if ".service" not in line and ".scope" not in line:
                        continue
                    parts = line.strip().split("/")
                    for part in reversed(parts):
                        if ".service" in part:
                            candidate = part.replace(".service", "").strip()
                        elif ".scope" in part:
                            candidate = part.replace(".scope", "").strip()
                        else:
                            continue
                        # Skip user session manager units — they hide the real service
                        if candidate.startswith(("user@", "session-", "app-", "init.scope")):
                            unit_name = None
                            continue
                        unit_name = candidate
                        break
                    if unit_name:
                        break
            if unit_name:
                return unit_name
    except Exception:
        pass
    return ""


def _find_repo_for_pid(pid: int) -> str:
    """Find git repo for a given PID by checking its working directory."""
    try:
        # Get working directory of process
        cwd_path = f"/proc/{pid}/cwd"
        if os.path.exists(cwd_path):
            cwd = os.readlink(cwd_path)
            return _find_git_repo(cwd)
    except Exception:
        pass
    return ""


def _find_git_repo(path: str) -> str:
    """Walk up from path to find .git and get remote origin URL."""
    current = Path(path)
    while current != current.parent:
        git_dir = current / ".git"
        if git_dir.exists():
            try:
                result = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=3,
                    cwd=str(current),
                )
                if result.returncode == 0:
                    url = result.stdout.strip()
                    # Extract owner/repo from URL
                    # https://github.com/owner/repo.git → owner/repo
                    match = re.search(r'github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$', url)
                    if match:
                        return match.group(1)
                    return url
            except Exception:
                pass
            break
        current = current.parent
    return ""


def _check_service_status(service: str) -> str:
    """Check if a systemd service is active."""
    if not service:
        return ""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _check_ssl(domain: str) -> dict:
    """Check SSL cert for domain."""
    cert_paths = [
        Path(f"/etc/nginx/ssl/{domain}.crt"),
        Path(f"/etc/letsencrypt/live/{domain}/fullchain.pem"),
        Path(f"/etc/ssl/certs/{domain}.crt"),
    ]
    for cert_path in cert_paths:
        if cert_path.exists():
            try:
                result = subprocess.run(
                    ["openssl", "x509", "-enddate", "-noout", "-in", str(cert_path)],
                    capture_output=True, text=True, timeout=3,
                )
                if result.returncode == 0:
                    # notAfter=Oct  3 12:00:00 2026 GMT
                    from datetime import datetime
                    date_str = result.stdout.strip().split("=")[1]
                    expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
                    days_left = (expiry - datetime.now()).days
                    return {
                        "has_ssl": True,
                        "cert_path": str(cert_path),
                        "expiry": date_str,
                        "days_left": days_left,
                        "expired": days_left < 0,
                        "expiring_soon": 0 <= days_left <= 30,
                    }
            except Exception:
                pass
    return {"has_ssl": False}
