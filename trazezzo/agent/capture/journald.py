"""journald capture — read systemd journal in real-time."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
from trazezzo.agent.attrib import attribute

log = logging.getLogger("trazezzo.capture.journald")

# ── Pattern matching for journal entries ───────────────────────────────
_PATTERNS = {
    # service start/stop/fail
    re.compile(r"Started (.+)\.", re.I): EventType.SERVICE_START,
    re.compile(r"Stopped (.+)\.", re.I): EventType.SERVICE_STOP,
    re.compile(r"Failed to start (.+)\.", re.I): EventType.SERVICE_FAIL,
    re.compile(r"Failed with result '.+' for (.+)\.", re.I): EventType.SERVICE_FAIL,
    # auth
    re.compile(r"Accepted (?:password|publickey) for (\S+) from (\S+)", re.I): EventType.AUTH_LOGIN,
    re.compile(r"Failed password for (?:invalid user )?(\S+) from (\S+)", re.I): EventType.AUTH_FAIL,
    re.compile(r"session opened for user (\S+)", re.I): EventType.SESSION_OPEN,
    re.compile(r"session closed for user (\S+)", re.I): EventType.SESSION_CLOSE,
    re.compile(r"(\S+) : TTY=\S+ ; PWD=.* ; USER=root ; COMMAND=(.+)", re.I): EventType.AUTH_SUDO,
    # OOM
    re.compile(r"Out of memory: Killed process (\d+)", re.I): EventType.PROCESS_OOM,
    re.compile(r"oom-killer", re.I): EventType.PROCESS_OOM,
}


def _match_event(message: str) -> tuple[EventType, dict] | None:
    """Match journal message to event type + extract payload."""
    for pattern, etype in _PATTERNS.items():
        m = pattern.search(message)
        if m:
            payload = {"raw": message}
            if etype in (EventType.AUTH_LOGIN, EventType.AUTH_FAIL):
                payload["user"] = m.group(1)
                payload["source_ip"] = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            elif etype == EventType.AUTH_SUDO:
                payload["user"] = m.group(1)
                payload["command"] = m.group(2)
            elif etype == EventType.PROCESS_OOM:
                payload["pid"] = m.group(1)
            elif etype in (EventType.SERVICE_START, EventType.SERVICE_STOP, EventType.SERVICE_FAIL):
                payload["unit"] = m.group(1)
            return etype, payload
    return None


def _parse_journalctl_line(line: str) -> dict | None:
    """Parse a journalctl --output=json line into a dict."""
    import json
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


async def capture_journald(store):
    """Continuously read journalctl --follow and emit events."""
    log.info("journald capture started (journalctl --follow)")

    proc = await asyncio.create_subprocess_exec(
        "journalctl", "--follow", "--output=json", "--since=now",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    while True:
        try:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                log.warning("journalctl stdout closed, restarting...")
                await asyncio.sleep(5)
                proc = await asyncio.create_subprocess_exec(
                    "journalctl", "--follow", "--output=json", "--since=now",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                continue

            entry = _parse_journalctl_line(line_bytes.decode("utf-8", errors="replace"))
            if not entry:
                continue

            msg = str(entry.get("MESSAGE", ""))
            if not msg:
                continue

            # Filter out non-printable / binary messages
            if not msg.isprintable() and not any(c.isprintable() for c in msg):
                continue
            # Strip ANSI escape codes
            import re as _re
            msg = _re.sub(r'\x1b\[[0-9;]*m', '', msg)
            msg = ''.join(c if c.isprintable() or c in '\n\r\t' else '' for c in msg)
            if not msg.strip():
                continue

            unit = str(entry.get("_SYSTEMD_UNIT", entry.get("SYSLOG_IDENTIFIER", "unknown")))
            ts_raw = entry.get("__REALTIME_TIMESTAMP")
            ts = datetime.fromtimestamp(int(ts_raw) / 1e6, tz=timezone.utc) if ts_raw else datetime.now(timezone.utc)

            uid_str = entry.get("_UID")
            pid_str = entry.get("_PID")
            comm = entry.get("_COMM", unit)

            uid = int(uid_str) if uid_str and uid_str.lstrip("-").isdigit() else None
            pid = int(pid_str) if pid_str and pid_str.lstrip("-").isdigit() else None

            # Try to match to a specific event type
            matched = _match_event(msg)
            if matched:
                etype, payload = matched
            else:
                etype = EventType.LOG
                payload = {"unit": unit, "raw": msg[:500]}

            actor = attribute(pid=pid, uid=uid, comm=comm, source="journald")

            # Severity from priority
            priority = int(entry.get("PRIORITY", 6))
            severity_map = {0: "critical", 1: "critical", 2: "critical", 3: "error", 4: "warning", 5: "info", 6: "info", 7: "debug"}
            severity = severity_map.get(priority, "info")

            event = ServerEvent(
                ts=ts,
                type=etype,
                source="journald",
                actor=actor,
                message=msg[:500],
                payload=payload,
                severity=severity,
            )
            store.add(event)

        except Exception as exc:
            log.error("journald capture error: %s", exc)
            await asyncio.sleep(5)
