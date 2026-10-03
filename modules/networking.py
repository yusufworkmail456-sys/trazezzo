"""Networking module — interfaces, routes, firewall, bonds."""

from __future__ import annotations

import subprocess
import socket
import json
import shutil
import re


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


def get_firewall_rules() -> dict:
    """Get structured firewall rules for the editor UI."""
    rules = []
    fw_type = "none"

    # Try ufw
    try:
        result = subprocess.run(["ufw", "status", "numbered"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 or result.stdout.strip():
            fw_type = "ufw"
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("[") and "]" in line:
                    # Parse: [ 1] 22/tcp                     ALLOW IN    Anywhere
                    parts = line.split("]", 1)
                    num = parts[0].strip().strip("[").strip()
                    rest = parts[1].strip()
                    # Try to parse rule components
                    tokens = rest.split()
                    action = ""
                    proto = ""
                    port = ""
                    source = ""
                    if "ALLOW" in rest:
                        action = "allow"
                    elif "DENY" in rest:
                        action = "deny"
                    elif "REJECT" in rest:
                        action = "reject"
                    # Find port/proto
                    for token in tokens:
                        if "/" in token and not token.startswith("Anywhere"):
                            port = token.split("/")[0]
                            proto = token.split("/")[1]
                        elif token.isdigit() and not port:
                            port = token
                    if "Anywhere" not in rest:
                        # Try to find source IP
                        for token in tokens:
                            if "." in token and not "/" in token:
                                source = token
                                break
                    rules.append({
                        "num": num,
                        "action": action,
                        "port": port,
                        "proto": proto,
                        "source": source or "Anywhere",
                        "raw": rest,
                    })
            return {"type": fw_type, "rules": rules}
    except Exception:
        pass

    # Fallback: parse iptables
    try:
        result = subprocess.run(
            ["iptables", "-L", "-n", "--line-numbers"],
            capture_output=True, text=True, timeout=5,
        )
        fw_type = "iptables"
        current_chain = ""
        for line in result.stdout.split("\n"):
            if line.startswith("Chain"):
                current_chain = line.split()[1]
            elif line.strip() and line[0].isdigit():
                parts = line.split()
                if len(parts) >= 8:
                    rules.append({
                        "num": parts[0],
                        "chain": current_chain,
                        "action": parts[1].lower(),
                        "proto": parts[2],
                        "source": parts[4],
                        "dest": parts[5],
                        "port": parts[7] if len(parts) > 7 else "",
                        "raw": line.strip(),
                    })
        return {"type": fw_type, "rules": rules}
    except Exception:
        return {"type": "none", "rules": []}


def add_firewall_rule(action: str, port: str, proto: str = "tcp", source: str = "") -> dict:
    """Add a firewall rule (ufw)."""
    if not shutil.which("ufw"):
        return {"success": False, "error": "ufw not installed"}

    # Validate inputs
    if action not in ("allow", "deny", "reject"):
        return {"success": False, "error": "Invalid action"}
    if not port.isdigit():
        return {"success": False, "error": "Port must be numeric"}
    if proto not in ("tcp", "udp", "both"):
        return {"success": False, "error": "Invalid protocol"}
    if source and not re.match(r"^[0-9./]+$", source):
        return {"success": False, "error": "Invalid source IP/CIDR"}

    cmd = ["ufw", action]
    if source:
        cmd.append("from")
        cmd.append(source)
        if port:
            cmd.append("to")
            cmd.append("any")
            cmd.append("port")
            cmd.append(port)
            if proto != "both":
                cmd.append("proto")
                cmd.append(proto)
    else:
        if proto == "both":
            cmd.append(f"{port}")
        else:
            cmd.append(f"{port}/{proto}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, input="y\n")
        if result.returncode == 0:
            return {"success": True, "rule": " ".join(cmd[1:])}
        else:
            return {"success": False, "error": result.stderr.strip() or result.stdout.strip()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def delete_firewall_rule(rule_num: str) -> dict:
    """Delete a firewall rule by number (ufw)."""
    if not shutil.which("ufw"):
        return {"success": False, "error": "ufw not installed"}
    if not rule_num.isdigit():
        return {"success": False, "error": "Rule number must be numeric"}

    try:
        result = subprocess.run(
            ["ufw", "delete", rule_num],
            capture_output=True, text=True, timeout=10, input="y\n",
        )
        if result.returncode == 0:
            return {"success": True, "rule_num": rule_num}
        else:
            return {"success": False, "error": result.stderr.strip() or result.stdout.strip()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def toggle_firewall(enable: bool) -> dict:
    """Enable or disable ufw."""
    if not shutil.which("ufw"):
        return {"success": False, "error": "ufw not installed"}
    action = "enable" if enable else "disable"
    try:
        result = subprocess.run(
            ["ufw", action],
            capture_output=True, text=True, timeout=10, input="y\n",
        )
        return {"success": result.returncode == 0, "action": action, "output": result.stdout.strip()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_vpn_tunnels() -> dict:
    """Detect and list VPN/tunnel interfaces."""
    tunnels = []

    # WireGuard
    if shutil.which("wg"):
        try:
            result = subprocess.run(
                ["wg", "show", "all", "dump"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    parts = line.split("\t")
                    if len(parts) >= 8:
                        tunnels.append({
                            "type": "wireguard",
                            "interface": parts[0],
                            "private_key": "***" if parts[1] else "",
                            "public_key": parts[2] if parts[2] != "(none)" else "",
                            "listen_port": parts[4] if parts[4] != "0" else "",
                            "endpoint": parts[3] if parts[3] != "(none)" else "",
                            "rx": parts[5],
                            "tx": parts[6],
                            "status": "active" if parts[3] != "(none)" else "inactive",
                        })
        except Exception:
            pass

    # OpenVPN
    if shutil.which("openvpn"):
        try:
            result = subprocess.run(
                ["systemctl", "list-units", "--type=service", "--state=running"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.split("\n"):
                if "openvpn" in line.lower():
                    parts = line.split()
                    tunnels.append({
                        "type": "openvpn",
                        "interface": parts[0].replace(".service", ""),
                        "status": "running",
                    })
        except Exception:
            pass

    # SSH tunnels (reverse/forward)
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.split("\n"):
            if "ssh" in line and ("-L" in line or "-R" in line or "-D" in line):
                tunnels.append({
                    "type": "ssh-tunnel",
                    "interface": "ssh",
                    "raw": line.strip()[:200],
                })
    except Exception:
        pass

    return {"tunnels": tunnels, "count": len(tunnels)}


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
