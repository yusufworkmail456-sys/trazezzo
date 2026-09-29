"""Networking module — interfaces, routes, firewall, bonds."""

from __future__ import annotations

import subprocess
import socket
import json


def get_interfaces() -> list[dict]:
    """List network interfaces with addresses and stats."""
    import psutil
    ifaces = []
    for name, addrs in psutil.net_if_addrs().items():
        addrs_list = []
        for addr in addrs:
            family = "ipv4" if addr.family == socket.AF_INET else \
                     "ipv6" if addr.family == socket.AF_INET6 else \
                     "mac" if addr.family == socket.AF_PACKET else "other"
            addrs_list.append({"family": family, "addr": addr.address, "netmask": addr.netmask})
        stats = psutil.net_if_stats().get(name)
        ifaces.append({
            "name": name,
            "addrs": addrs_list,
            "isup": stats.isup if stats else False,
            "speed": stats.speed if stats else 0,
            "mtu": stats.mtu if stats else 0,
            "duplex": stats.duplex if stats else "unknown",
        })
    return ifaces


def get_routes() -> list[str]:
    """Get routing table."""
    try:
        result = subprocess.run(["ip", "route", "show"], capture_output=True, text=True, timeout=5)
        return [line for line in result.stdout.strip().split("\n") if line]
    except Exception:
        return []


def get_firewall() -> dict:
    """Get firewall status (ufw/iptables)."""
    # Try ufw first
    try:
        result = subprocess.run(["ufw", "status", "verbose"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return {"type": "ufw", "status": result.stdout.strip()}
    except Exception:
        pass
    # Fallback iptables
    try:
        result = subprocess.run(["iptables", "-L", "-n"], capture_output=True, text=True, timeout=5)
        return {"type": "iptables", "status": result.stdout.strip()}
    except Exception:
        return {"type": "none", "status": "No firewall detected"}


def get_bonds() -> list[dict]:
    """List bonding interfaces if present."""
    bonds = []
    try:
        result = subprocess.run(["ip", "-d", "link", "show", "type", "bond"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    bonds.append({"raw": line})
    except Exception:
        pass
    return bonds
