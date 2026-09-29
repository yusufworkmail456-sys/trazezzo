"""Trazezzo CLI — init, up, down, status."""

from __future__ import annotations

import sys
import os
import argparse
import subprocess
import signal
import time
import logging
from pathlib import Path

from trazezzo.config import (
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    DATA_DIR,
    DB_PATH,
)

log = logging.getLogger("trazezzo.cli")

SYSTEMD_SERVICE = """\
[Unit]
Description=Trazezzo — AI-Native Server Operations Platform
After=network.target

[Service]
Type=simple
ExecStart={venv_python} -m trazezzo.daemon_entry
Restart=on-failure
RestartSec=5
Environment=TRAZEZZO_DATA_DIR={data_dir}
Environment=PYTHONUNBUFFERED=1
WorkingDirectory={project_root}
User=root

[Install]
WantedBy=multi-user.target
"""

NGINX_CONF = """\
# Trazezzo — AI-Native Server Operations
location /trazezzo/ {
    proxy_pass http://127.0.0.1:9122/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # WebSocket support
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;

    # Streaming support (SSE/chatbot)
    proxy_buffering off;
    proxy_cache off;
}
"""


def _detect_venv() -> str:
    """Find venv python path."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "python"),
        "/opt/trazezzo/.venv/bin/python",
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return sys.executable


def cmd_init(args):
    """Initialize Trazezzo: create venv, install deps, setup systemd + nginx."""
    project_root = Path(__file__).resolve().parent.parent
    venv_path = project_root / ".venv"

    print("▶ Creating venv...")
    if not venv_path.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)

    venv_python = str(venv_path / "bin" / "python")
    venv_pip = str(venv_path / "bin" / "pip")

    print("▶ Installing dependencies...")
    subprocess.run([venv_pip, "install", "-e", str(project_root)], check=True)

    # Install system packages
    print("▶ Installing system packages (auditd)...")
    subprocess.run(["apt-get", "install", "-y", "auditd", "audispd-plugins"], capture_output=True)

    # Enable auditd
    subprocess.run(["systemctl", "enable", "--now", "auditd"], capture_output=True)

    # Create data dir
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Write systemd unit
    service_path = Path("/etc/systemd/system/trazezzo.service")
    service_content = SYSTEMD_SERVICE.format(
        venv_python=venv_python,
        data_dir=DATA_DIR,
        project_root=project_root,
    )
    service_path.write_text(service_content)
    print(f"✓ Wrote {service_path}")

    # Write nginx conf
    nginx_snippet = Path("/etc/nginx/snippets/trazezzo.conf")
    nginx_snippet.parent.mkdir(parents=True, exist_ok=True)
    nginx_snippet.write_text(NGINX_CONF)
    print(f"✓ Wrote {nginx_snippet}")

    # Include in nginx default site
    nginx_site = Path("/etc/nginx/sites-enabled/default")
    if nginx_site.exists():
        content = nginx_site.read_text()
        if "trazezzo.conf" not in content:
            # Add before closing brace of server block
            content = content.replace(
                "}",
                "    include snippets/trazezzo.conf;\n}",
                1,  # only first occurrence
            )
            nginx_site.write_text(content)
            print(f"✓ Added trazezzo.conf to {nginx_site}")
        else:
            print(f"✓ trazezzo.conf already in {nginx_site}")

    subprocess.run(["nginx", "-t"], capture_output=True)
    subprocess.run(["systemctl", "reload", "nginx"], capture_output=True)

    print("\n✅ Trazezzo initialized!")
    print(f"   Venv: {venv_path}")
    print(f"   Data: {DATA_DIR}")
    print(f"   Port: {DASHBOARD_HOST}:{DASHBOARD_PORT}")
    print(f"   URL:  http://localhost:{DASHBOARD_PORT} or https://<host>/trazezzo/")
    print(f"\n   Run: trazezzo up")


def cmd_up(args):
    """Start Trazezzo agent + dashboard."""
    subprocess.run(["systemctl", "start", "trazezzo"], capture_output=True)
    time.sleep(2)
    status = subprocess.run(["systemctl", "is-active", "trazezzo"], capture_output=True, text=True)
    if status.stdout.strip() == "active":
        print("✅ Trazezzo is running")
        print(f"   Dashboard: http://localhost:{DASHBOARD_PORT}")
        print(f"   URL:       https://<your-domain>/trazezzo/")
    else:
        print("❌ Failed to start. Check: journalctl -u trazezzo -f")


def cmd_down(args):
    """Stop Trazezzo."""
    subprocess.run(["systemctl", "stop", "trazezzo"], capture_output=True)
    print("⏹ Trazezzo stopped")


def cmd_status(args):
    """Show Trazezzo status."""
    result = subprocess.run(["systemctl", "is-active", "trazezzo"], capture_output=True, text=True)
    status = result.stdout.strip()
    print(f"Service:  {status}")

    if status == "active":
        import httpx
        try:
            resp = httpx.get(f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/api/overview", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                print(f"Hostname: {data.get('hostname')}")
                print(f"Uptime:   {data.get('uptime')}")
                print(f"CPU:      {data.get('cpu', {}).get('percent')}%")
                print(f"RAM:      {data.get('memory', {}).get('percent')}%")
        except Exception as exc:
            print(f"Dashboard: unreachable ({exc})")

    if DB_PATH.exists():
        print(f"Database: {DB_PATH} ({DB_PATH.stat().st_size // 1024} KB)")


def cmd_log(args):
    """Show Trazezzo logs."""
    subprocess.run(["journalctl", "-u", "trazezzo", "-f", "--no-pager"])


def cmd_update(args):
    """Update Trazezzo: git pull, reinstall deps, restart service."""
    import importlib.metadata

    project_root = Path(__file__).resolve().parent.parent

    # Get current version
    try:
        old_version = importlib.metadata.version("trazezzo")
    except Exception:
        old_version = "unknown"

    print(f"▶ Current version: {old_version}")

    # Check git repo
    git_dir = project_root / ".git"
    if not git_dir.exists():
        print("❌ Not a git repository. Cannot auto-update.")
        print(f"   Project root: {project_root}")
        print("   Update manually: git pull && pip install -e . && systemctl restart trazezzo")
        return

    # Stop service
    print("▶ Stopping Trazezzo...")
    subprocess.run(["systemctl", "stop", "trazezzo"], capture_output=True)

    # Git pull
    print("▶ Pulling latest code...")
    result = subprocess.run(
        ["git", "pull", "origin", "main"],
        cwd=str(project_root),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"❌ git pull failed: {result.stderr.strip()}")
        print("   Fix conflicts manually, then run: trazezzo up")
        return
    print(result.stdout.strip())

    # Reinstall deps
    venv_python = _detect_venv()
    venv_pip = venv_python.replace("/python", "/pip")
    print("▶ Reinstalling dependencies...")
    result = subprocess.run(
        [venv_pip, "install", "-e", str(project_root)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"❌ pip install failed: {result.stderr.strip()}")
        print("   Fix manually, then run: trazezzo up")
        return

    # Get new version
    try:
        import importlib
        importlib.reload(importlib.metadata)
        new_version = importlib.metadata.version("trazezzo")
    except Exception:
        new_version = "unknown"

    # Restart service
    print("▶ Restarting Trazezzo...")
    subprocess.run(["systemctl", "start", "trazezzo"], capture_output=True)
    time.sleep(2)

    status = subprocess.run(["systemctl", "is-active", "trazezzo"], capture_output=True, text=True)
    if status.stdout.strip() == "active":
        print(f"\n✅ Trazezzo updated successfully!")
        print(f"   Version: {old_version} → {new_version}")
        print(f"   Service: active")
        print(f"   URL:     https://<your-domain>/trazezzo/")
    else:
        print(f"\n❌ Service failed to start. Check: journalctl -u trazezzo -f")


def cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="trazezzo", description="Trazezzo — AI-Native Server Ops")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="Initialize Trazezzo (venv, systemd, nginx)")
    subparsers.add_parser("up", help="Start Trazezzo")
    subparsers.add_parser("down", help="Stop Trazezzo")
    subparsers.add_parser("status", help="Show status")
    subparsers.add_parser("log", help="Show logs")
    subparsers.add_parser("update", help="Update Trazezzo (git pull + reinstall + restart)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {
        "init": cmd_init,
        "up": cmd_up,
        "down": cmd_down,
        "status": cmd_status,
        "log": cmd_log,
        "update": cmd_update,
    }[args.command](args)


if __name__ == "__main__":
    cli()
