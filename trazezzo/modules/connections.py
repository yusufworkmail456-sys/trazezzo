"""Network connections -- ss/netstat equivalent."""

from __future__ import annotations
import subprocess
import re


def get_connections() -> list[dict]:
    """Get active network connections via ss."""
    try:
        result = subprocess.run(
            ["ss", "-tunp", "--no-header"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return _get_connections_netstat()

    connections = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue

        proto = parts[0]
        state = parts[1] if len(parts) > 1 else ""
        local = parts[4] if len(parts) > 4 else ""
        peer = parts[5] if len(parts) > 5 else ""
        process = parts[-1] if "users:" in (parts[-1] if parts else "") else ""

        pid = 0
        pname = ""
        if process:
            m = re.search(r'pid=(\d+).*?"([^"]*)"', process)
            if m:
                pid = int(m.group(1))
                pname = m.group(2)

        connections.append({
            "proto": proto,
            "state": state,
            "local": local,
            "peer": peer,
            "pid": pid,
            "process": pname,
        })

    return connections


def get_listening_ports() -> list[dict]:
    """Get listening ports with process info."""
    try:
        result = subprocess.run(
            ["ss", "-tlnp", "--no-header"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return _get_listening_netstat()

    ports = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue

        proto = parts[0]
        local = parts[3]
        process = parts[-1] if "users:" in (parts[-1] if parts else "") else ""

        port = local.rsplit(":", 1)[-1] if ":" in local else ""
        addr = local.rsplit(":", 1)[0] if ":" in local else local

        pid = 0
        pname = ""
        if process:
            m = re.search(r'pid=(\d+).*?"([^"]*)"', process)
            if m:
                pid = int(m.group(1))
                pname = m.group(2)

        ports.append({
            "proto": proto,
            "addr": addr,
            "port": port,
            "pid": pid,
            "process": pname,
        })

    ports.sort(key=lambda x: int(x["port"]) if x["port"].isdigit() else 99999)
    return ports


def _get_connections_netstat() -> list[dict]:
    """Fallback using netstat."""
    try:
        result = subprocess.run(
            ["netstat", "-tunp"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return []

    connections = []
    for line in result.stdout.strip().split("\n")[2:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        connections.append({
            "proto": parts[0],
            "state": parts[5] if len(parts) > 5 else "",
            "local": parts[3],
            "peer": parts[4],
            "pid": 0,
            "process": parts[6].split("/")[1] if len(parts) > 6 and "/" in parts[6] else "",
        })
    return connections


def _get_listening_netstat() -> list[dict]:
    """Fallback using netstat."""
    try:
        result = subprocess.run(
            ["netstat", "-tlnp"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return []

    ports = []
    for line in result.stdout.strip().split("\n")[2:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        local = parts[3]
        port = local.rsplit(":", 1)[-1] if ":" in local else ""
        ports.append({
            "proto": parts[0],
            "addr": local.rsplit(":", 1)[0] if ":" in local else local,
            "port": port,
            "pid": 0,
            "process": parts[6].split("/")[1] if len(parts) > 6 and "/" in parts[6] else "",
        })
    return ports
