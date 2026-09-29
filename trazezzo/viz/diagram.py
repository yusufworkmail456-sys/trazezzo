"""System diagram — auto-discover services, ports, mounts, network → cytoscape.js graph."""

from __future__ import annotations

import json
import socket
import subprocess

import psutil

from trazezzo.modules.system import get_services


def build_system_graph() -> dict:
    """Build a cytoscape.js-compatible graph of the system topology.

    Nodes: server, systemd services, listening ports, mounts, network ifaces.
    Edges: dependencies (service→port, server→mount, server→iface, service→service).
    """
    nodes = []
    edges = []

    # ── Root: server node ─────────────────────────────────────────
    hostname = socket.gethostname()
    nodes.append({
        "data": {
            "id": "server",
            "label": hostname,
            "type": "server",
            "icon": "🖥️",
        }
    })

    # ── systemd services (top 30 by activity) ──────────────────────
    services = get_services()
    # Sort: failed first, then active
    services.sort(key=lambda s: (s["active"] != "failed", s["active"] != "active"))
    for svc in services[:30]:
        node_id = f"svc:{svc['unit']}"
        status = svc["active"]
        color = "#ef4444" if status == "failed" else "#22c55e" if status == "active" else "#6b7280"
        nodes.append({
            "data": {
                "id": node_id,
                "label": svc["unit"].replace(".service", ""),
                "type": "service",
                "status": status,
                "sub": svc["sub"],
                "description": svc["description"],
                "color": color,
                "icon": "⚙️",
            }
        })
        edges.append({
            "data": {"source": "server", "target": node_id}
        })

    # ── Listening ports ────────────────────────────────────────────
    connections = psutil.net_connections()
    listening = {}
    for conn in connections:
        if conn.status == "LISTEN" and conn.laddr:
            port = conn.laddr.port
            if port not in listening:
                listening[port] = conn.pid
    for port, pid in listening.items():
        # Resolve process name
        proc_name = "unknown"
        if pid:
            try:
                proc_name = psutil.Process(pid).name()
            except Exception:
                pass
        node_id = f"port:{port}"
        nodes.append({
            "data": {
                "id": node_id,
                "label": f":{port}",
                "type": "port",
                "port": port,
                "pid": pid,
                "process": proc_name,
                "icon": "🔌",
            }
        })
        # Link port to its process if we can find it
        if proc_name != "unknown":
            # Find matching service node
            for n in nodes:
                if n["data"].get("type") == "service" and proc_name in n["data"].get("label", ""):
                    edges.append({"data": {"source": n["data"]["id"], "target": node_id}})
                    break
            else:
                edges.append({"data": {"source": "server", "target": node_id}})

    # ── Mounts / filesystems ───────────────────────────────────────
    for part in psutil.disk_partitions():
        node_id = f"mount:{part.mountpoint}"
        try:
            usage = psutil.disk_usage(part.mountpoint)
            pct = round(usage.percent, 1)
        except Exception:
            pct = 0
        nodes.append({
            "data": {
                "id": node_id,
                "label": part.mountpoint,
                "type": "mount",
                "device": part.device,
                "fstype": part.fstype,
                "percent": pct,
                "icon": "💾",
            }
        })
        edges.append({"data": {"source": "server", "target": node_id}})

    # ── Network interfaces ─────────────────────────────────────────
    for name, addrs in psutil.net_if_addrs().items():
        addrs_list = []
        for addr in addrs:
            if addr.family == socket.AF_INET:
                addrs_list.append(addr.address)
        node_id = f"net:{name}"
        nodes.append({
            "data": {
                "id": node_id,
                "label": name,
                "type": "network",
                "addrs": addrs_list,
                "icon": "🌐",
            }
        })
        edges.append({"data": {"source": "server", "target": node_id}})

    # ── Cron jobs ─────────────────────────────────────────────────
    cron_entries = []
    for cron_path in ["/etc/crontab", "/etc/cron.d"]:
        try:
            if cron_path == "/etc/cron.d":
                import os
                for f in os.listdir(cron_path):
                    fp = os.path.join(cron_path, f)
                    if os.path.isfile(fp):
                        with open(fp) as fh:
                            for line in fh:
                                if line.strip() and not line.startswith("#"):
                                    cron_entries.append(line.strip())
            else:
                with open(cron_path) as f:
                    for line in f:
                        if line.strip() and not line.startswith("#"):
                            cron_entries.append(line.strip())
        except Exception:
            pass
    for i, entry in enumerate(cron_entries[:15]):
        node_id = f"cron:{i}"
        nodes.append({
            "data": {
                "id": node_id,
                "label": f"cron:{i}",
                "type": "cron",
                "command": entry[:80],
                "icon": "⏰",
            }
        })
        edges.append({"data": {"source": "server", "target": node_id}})

    return {
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "services": len(services),
            "listening_ports": len(listening),
            "mounts": len([n for n in nodes if n["data"].get("type") == "mount"]),
        },
    }
