"""Cockpit mirror — system overview module."""

from __future__ import annotations

import os
import platform
import socket
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import psutil


def get_system_overview() -> dict:
    """Return system overview for dashboard."""
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot

    # CPU info
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    # Memory
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Disk
    disks = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": round(usage.total / 1e9, 2),
                "used_gb": round(usage.used / 1e9, 2),
                "free_gb": round(usage.free / 1e9, 2),
                "percent": usage.percent,
            })
        except Exception:
            pass

    # Network interfaces
    net_ifaces = {}
    for name, addrs in psutil.net_if_addrs().items():
        net_ifaces[name] = []
        for addr in addrs:
            if addr.family == socket.AF_INET:
                net_ifaces[name].append({"type": "ipv4", "addr": addr.address})
            elif addr.family == socket.AF_INET6:
                net_ifaces[name].append({"type": "ipv6", "addr": addr.address})
            elif addr.family == socket.AF_PACKET:
                net_ifaces[name].append({"type": "mac", "addr": addr.address})

    # OS info
    os_release = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    os_release[k] = v.strip('"')
    except Exception:
        pass

    # Kernel
    kernel = platform.release()
    hostname = socket.gethostname()

    # Load avg
    load1, load5, load15 = psutil.getloadavg()

    return {
        "hostname": hostname,
        "os": os_release.get("PRETTY_NAME", "Linux"),
        "kernel": kernel,
        "uptime": str(uptime).split(".")[0],
        "boot_time": boot.isoformat(),
        "cpu": {
            "percent": cpu_percent,
            "count": cpu_count,
            "freq_mhz": cpu_freq.current if cpu_freq else None,
        },
        "memory": {
            "percent": mem.percent,
            "used_gb": round(mem.used / 1e9, 2),
            "total_gb": round(mem.total / 1e9, 2),
        },
        "swap": {
            "percent": swap.percent,
            "used_gb": round(swap.used / 1e9, 2),
            "total_gb": round(swap.total / 1e9, 2),
        },
        "disks": disks,
        "network": net_ifaces,
        "load": {"load1": load1, "load5": load5, "load15": load15},
    }


def get_services() -> list[dict]:
    """List systemd services via systemctl."""
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=10,
        )
        services = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 4:
                services.append({
                    "unit": parts[0],
                    "load": parts[1],
                    "active": parts[2],
                    "sub": parts[3],
                    "description": " ".join(parts[4:]) if len(parts) > 4 else "",
                })
        return services
    except Exception:
        return []


def get_failed_services() -> list[str]:
    """List failed systemd units."""
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "--failed", "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
        return [line.split()[0] for line in result.stdout.strip().split("\n") if line.strip()]
    except Exception:
        return []


def service_action(unit: str, action: str) -> dict:
    """Start/stop/restart/enable/disable a service."""
    allowed = {"start", "stop", "restart", "enable", "disable", "status"}
    if action not in allowed:
        return {"error": f"Action '{action}' not allowed"}
    try:
        result = subprocess.run(
            ["systemctl", action, unit],
            capture_output=True, text=True, timeout=30,
        )
        return {
            "unit": unit,
            "action": action,
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except Exception as exc:
        return {"error": str(exc)}
