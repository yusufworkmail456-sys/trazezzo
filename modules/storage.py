"""Storage module — disk, fs, mount, LVM."""

from __future__ import annotations

import subprocess
import psutil


def get_storage_overview() -> dict:
    """Disk usage + mounts + LVM info."""
    partitions = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            partitions.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "opts": part.opts,
                "total_gb": round(usage.total / 1e9, 2),
                "used_gb": round(usage.used / 1e9, 2),
                "free_gb": round(usage.free / 1e9, 2),
                "percent": usage.percent,
            })
        except Exception:
            partitions.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "opts": part.opts,
                "error": "inaccessible",
            })

    # LVM
    lvm = {"pvs": [], "vgs": [], "lvs": []}
    try:
        result = subprocess.run(["pvs", "--noheadings"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lvm["pvs"] = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        result = subprocess.run(["vgs", "--noheadings"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lvm["vgs"] = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        result = subprocess.run(["lvs", "--noheadings"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lvm["lvs"] = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    except Exception:
        pass

    # Block devices
    block_devices = []
    try:
        result = subprocess.run(["lsblk", "-b", "--json"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            import json
            block_devices = json.loads(result.stdout).get("blockdevices", [])
    except Exception:
        pass

    return {
        "partitions": partitions,
        "lvm": lvm,
        "block_devices": block_devices,
    }
