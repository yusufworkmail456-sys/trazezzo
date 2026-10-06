"""sysctl kernel tuning -- read and modify kernel parameters."""

from __future__ import annotations

import subprocess
import re


# Common sysctl keys grouped by category for the UI
SYSCTL_CATEGORIES = {
    "Network": [
        "net.ipv4.tcp_tw_reuse",
        "net.ipv4.tcp_fin_timeout",
        "net.ipv4.tcp_keepalive_time",
        "net.ipv4.ip_local_port_range",
        "net.core.somaxconn",
        "net.core.netdev_max_backlog",
        "net.ipv4.tcp_max_syn_backlog",
        "net.ipv4.tcp_syncookies",
        "net.ipv4.conf.all.rp_filter",
        "net.ipv4.ip_forward",
    ],
    "Memory": [
        "vm.swappiness",
        "vm.overcommit_memory",
        "vm.dirty_ratio",
        "vm.dirty_background_ratio",
        "vm.min_free_kbytes",
    ],
    "File System": [
        "fs.file-max",
        "fs.inotify.max_user_watches",
        "fs.inotify.max_user_instances",
    ],
    "Kernel": [
        "kernel.pid_max",
        "kernel.threads-max",
        "kernel.hostname",
        "kernel.randomize_va_space",
        "kernel.sysrq",
    ],
}


def get_sysctl(keys: list[str] | None = None) -> list[dict]:
    """Get sysctl values. If keys is None, returns categorized defaults."""
    if keys is None:
        keys = []
        for group in SYSCTL_CATEGORIES.values():
            keys.extend(group)

    results = []
    for key in keys:
        try:
            result = subprocess.run(
                ["sysctl", "-n", key],
                capture_output=True, text=True, timeout=3,
            )
            value = result.stdout.strip() if result.returncode == 0 else "N/A"
            category = "Other"
            for cat, cat_keys in SYSCTL_CATEGORIES.items():
                if key in cat_keys:
                    category = cat
                    break
            results.append({
                "key": key,
                "value": value,
                "category": category,
                "writable": True,
            })
        except Exception:
            results.append({"key": key, "value": "error", "category": "Other", "writable": False})
    return results


def set_sysctl(key: str, value: str) -> dict:
    """Set a sysctl parameter temporarily (runtime only)."""
    # Validate key format to prevent injection
    if not re.match(r"^[a-zA-Z0-9._-]+$", key):
        return {"success": False, "error": "Invalid sysctl key format"}
    if not re.match(r"^[a-zA-Z0-9._ \-/]+$", value):
        return {"success": False, "error": "Invalid value format"}

    try:
        result = subprocess.run(
            ["sysctl", "-w", f"{key}={value}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return {"success": True, "key": key, "value": value, "output": result.stdout.strip()}
        else:
            return {"success": False, "error": result.stderr.strip() or result.stdout.strip()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
