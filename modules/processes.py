"""Process manager -- htop in browser."""

from __future__ import annotations
import signal
import psutil
from datetime import datetime


def get_processes(sort_by: str = "cpu", limit: int = 100) -> list[dict]:
    """Get running processes sorted by CPU or memory usage."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent",
                                   "memory_percent", "memory_info", "cmdline",
                                   "status", "create_time", "terminal"]):
        try:
            info = p.info
            mem_mb = info["memory_info"].rss / 1e6 if info.get("memory_info") else 0
            procs.append({
                "pid": info["pid"],
                "name": info["name"] or "",
                "user": info["username"] or "",
                "cpu": round(info["cpu_percent"] or 0, 1),
                "mem": round(info["memory_percent"] or 0, 1),
                "mem_mb": round(mem_mb, 1),
                "status": info["status"] or "",
                "cmd": " ".join(info["cmdline"][:8]) if info.get("cmdline") else "",
                "started": datetime.fromtimestamp(info["create_time"]).strftime("%H:%M") if info.get("create_time") else "",
                "tty": info.get("terminal") or "",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    sort_key = {"cpu": "cpu", "mem": "mem", "pid": "pid"}.get(sort_by, "cpu")
    reverse = sort_key != "pid"
    procs.sort(key=lambda x: x.get(sort_key, 0), reverse=reverse)
    return procs[:limit]


def kill_process(pid: int, signal_type: str = "TERM") -> dict:
    """Kill a process by PID."""
    try:
        p = psutil.Process(pid)
        name = p.name()
        if signal_type == "KILL":
            p.kill()
        elif signal_type == "HUP":
            p.send_signal(signal.SIGHUP)
        else:
            p.terminate()
        return {"success": True, "pid": pid, "name": name, "signal": signal_type}
    except psutil.NoSuchProcess:
        return {"success": False, "error": f"PID {pid} not found"}
    except psutil.AccessDenied:
        return {"success": False, "error": f"Access denied for PID {pid}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
