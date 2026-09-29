"""psutil metrics — periodic CPU/mem/disk/net snapshot + anomaly detection."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import psutil

from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
from trazezzo.config import CAPTURE_METRICS_INTERVAL

log = logging.getLogger("trazezzo.capture.psutil_metrics")

# ── Anomaly thresholds ─────────────────────────────────────────────────
THRESH_CPU_PERCENT = 90.0
THRESH_MEM_PERCENT = 90.0
THRESH_DISK_PERCENT = 90.0
THRESH_LOAD_5M = 4.0


async def capture_psutil_metrics(store):
    """Periodic metrics snapshot + anomaly events."""
    log.info("psutil metrics capture started (interval=%ds)", CAPTURE_METRICS_INTERVAL)

    while True:
        try:
            ts = datetime.now(timezone.utc)

            # CPU
            cpu_percent = psutil.cpu_percent(interval=None)
            cpu_count = psutil.cpu_count()

            # Memory
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()

            # Disk
            disk_usage = {}
            for part in psutil.disk_partitions():
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disk_usage[part.mountpoint] = {
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": usage.percent,
                    }
                except Exception:
                    pass

            # Load average
            load1, load5, load15 = psutil.getloadavg()

            # Network
            net = psutil.net_io_counters()

            # ── Anomaly detection ────────────────────────────────────
            anomalies = []

            if cpu_percent > THRESH_CPU_PERCENT:
                anomalies.append(("CPU", cpu_percent, THRESH_CPU_PERCENT, "CPU usage high"))

            if mem.percent > THRESH_MEM_PERCENT:
                anomalies.append(("MEM", mem.percent, THRESH_MEM_PERCENT, "Memory usage high"))

            for mount, info in disk_usage.items():
                if info["percent"] > THRESH_DISK_PERCENT:
                    anomalies.append((f"DISK:{mount}", info["percent"], THRESH_DISK_PERCENT, f"Disk {mount} high"))

            if load5 > THRESH_LOAD_5M:
                anomalies.append(("LOAD", load5, THRESH_LOAD_5M, "Load average high"))

            for name, value, threshold, msg in anomalies:
                event = ServerEvent(
                    ts=ts,
                    type=EventType.METRIC_SPIKE,
                    source="psutil",
                    actor=Actor(kind=ActorKind.SYSTEM, id="psutil"),
                    message=msg,
                    payload={
                        "metric": name,
                        "value": round(value, 2),
                        "threshold": threshold,
                        "snapshot": {
                            "cpu_percent": cpu_percent,
                            "cpu_count": cpu_count,
                            "mem_percent": mem.percent,
                            "mem_used_gb": round(mem.used / 1e9, 2),
                            "mem_total_gb": round(mem.total / 1e9, 2),
                            "swap_percent": swap.percent,
                            "disk": disk_usage,
                            "load1": round(load1, 2),
                            "load5": round(load5, 2),
                            "load15": round(load15, 2),
                            "net_bytes_sent": net.bytes_sent,
                            "net_bytes_recv": net.bytes_recv,
                        },
                    },
                    severity="warning",
                )
                store.add(event)

        except Exception as exc:
            log.error("psutil capture error: %s", exc)

        await asyncio.sleep(CAPTURE_METRICS_INTERVAL)
