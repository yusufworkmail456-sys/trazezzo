"""auditd capture — read audit log for execve, login, file events."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
from trazezzo.agent.attrib import attribute

log = logging.getLogger("trazezzo.capture.auditd")

AUDIT_LOG = Path("/var/log/audit/audit.log")

# ── Audit event parsing ────────────────────────────────────────────────
# type=EXECVE msg=audit(1234567890.123:456): pid=1234 uid=0 auid=1000 ...
_RE_TYPE = re.compile(r"type=([A-Z_]+)")
_RE_TIMESTAMP = re.compile(r"audit\((\d+\.\d+):(\d+)\)")
_RE_KV = re.compile(r"(\w+)=(\S+)")


def _parse_audit_line(line: str) -> dict | None:
    """Parse a single audit log line into a dict."""
    type_match = _RE_TYPE.search(line)
    ts_match = _RE_TIMESTAMP.search(line)
    if not type_match or not ts_match:
        return None

    audit_type = type_match.group(1)
    ts = datetime.fromtimestamp(float(ts_match.group(1)), tz=timezone.utc)
    seq = int(ts_match.group(2))

    # Extract all key=value pairs
    kvs = {}
    for m in _RE_KV.finditer(line):
        key = m.group(1)
        val = m.group(2).strip('"').strip("'")
        kvs[key] = val

    return {
        "type": audit_type,
        "ts": ts,
        "seq": seq,
        "fields": kvs,
        "raw": line.strip(),
    }


def _audit_type_to_event(parsed: dict) -> tuple[EventType, dict] | None:
    """Map audit type to ServerEvent type + payload."""
    atype = parsed["type"]
    fields = parsed["fields"]

    if atype == "EXECVE":
        # execve: a0=command, a1..=args
        cmd = fields.get("a0", "unknown")
        return EventType.PROCESS_EXEC, {"command": cmd, "argc": int(fields.get("argc", 0))}

    if atype == "LOGIN":
        if fields.get("res") == "success":
            return EventType.AUTH_LOGIN, {"pid": fields.get("pid"), "uid": fields.get("uid")}
        return EventType.AUTH_FAIL, {"pid": fields.get("pid"), "uid": fields.get("uid")}

    if atype == "ANOM_ABEND":
        return EventType.PROCESS_KILL, {"signal": fields.get("sig", "unknown")}

    if atype in ("ADD_USER", "USER_MGMT"):
        return EventType.AUTH_LOGIN, {"user": fields.get("id"), "action": atype}

    return None


async def capture_auditd(store):
    """Tail audit log and emit events."""
    if not AUDIT_LOG.exists():
        log.warning("audit.log not found at %s — auditd capture disabled", AUDIT_LOG)
        return

    log.info("auditd capture started (tail %s)", AUDIT_LOG)

    # Open and seek to end
    f = open(AUDIT_LOG, "r")
    f.seek(0, 2)  # end of file

    while True:
        line = f.readline()
        if line:
            parsed = _parse_audit_line(line)
            if not parsed:
                continue

            fields = parsed["fields"]
            pid = int(fields.get("pid", 0)) if fields.get("pid", "").lstrip("-").isdigit() else None
            uid = int(fields.get("uid", 0)) if fields.get("uid", "").lstrip("-").isdigit() else None
            auid = int(fields.get("auid", 0)) if fields.get("auid", "").lstrip("-").isdigit() else None

            mapped = _audit_type_to_event(parsed)
            if mapped:
                etype, payload = mapped
            else:
                etype = EventType.LOG
                payload = {"audit_type": parsed["type"], "raw": parsed["raw"][:300]}

            # Use auid (login uid) for attribution when available
            eff_uid = auid if auid and auid != 4294967295 else uid
            actor = attribute(pid=pid, uid=eff_uid, comm=payload.get("command", "auditd"), source="auditd")

            event = ServerEvent(
                ts=parsed["ts"],
                type=etype,
                source="auditd",
                actor=actor,
                message=parsed["raw"][:500],
                payload=payload,
                severity="info",
            )
            store.add(event)
        else:
            await asyncio.sleep(0.5)

    f.close()
