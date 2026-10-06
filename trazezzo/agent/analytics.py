"""Event analytics — aggregation queries over the warm store for the AI assistant.

Exposes a small set of tools the LLM can call (via the `event_query` action) to
answer temporal questions like "what failed this morning?" or "which services
logged the most errors in the last hour?".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from trazezzo.config import DB_PATH


def _conn():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    return conn


def _parse_since(since: str | None) -> str:
    """Convert a human window ('30m', '2h', '1d') or ISO timestamp to an ISO cutoff."""
    if not since:
        return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    s = since.strip()
    # Relative windows
    import re
    m = re.fullmatch(r"(\d+)\s*([mhd])", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
        return (datetime.now(timezone.utc) - delta).isoformat()
    # ISO timestamp — validate round-trip
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except ValueError:
        return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def summary(since: str | None = None, until: str | None = None) -> dict:
    """Event counts grouped by actor and type in the window."""
    since_iso = _parse_since(since)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT actor_kind, type, severity, COUNT(*) FROM events WHERE ts >= ? GROUP BY actor_kind, type, severity",
            [since_iso],
        ).fetchall()
    finally:
        conn.close()

    by_actor: dict[str, int] = {}
    by_type: list[dict] = []
    for actor, etype, sev, count in rows:
        by_actor[actor] = by_actor.get(actor, 0) + count
        by_type.append({"type": etype, "actor": actor, "severity": sev, "count": count})
    by_type.sort(key=lambda x: -x["count"])
    return {"since": since_iso, "total": sum(by_actor.values()), "by_actor": by_actor, "by_type": by_type[:25]}


def by_type(event_type: str, since: str | None = None, limit: int = 30) -> dict:
    """Recent events of one type, newest first."""
    since_iso = _parse_since(since)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT ts, actor_kind, message, severity, payload FROM events WHERE type = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
            [event_type, since_iso, min(limit, 100)],
        ).fetchall()
    finally:
        conn.close()
    events = []
    for ts, actor, msg, sev, payload in rows:
        events.append({"ts": ts, "actor": actor, "message": msg[:200], "severity": sev})
    return {"type": event_type, "since": since_iso, "count": len(events), "events": events}


def top_sources(since: str | None = None, limit: int = 10) -> dict:
    """Most active event sources/units in the window."""
    since_iso = _parse_since(since)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT source, COUNT(*) FROM events WHERE ts >= ? GROUP BY source ORDER BY COUNT(*) DESC LIMIT ?",
            [since_iso, min(limit, 25)],
        ).fetchall()
    finally:
        conn.close()
    return {"since": since_iso, "sources": [{"source": s, "count": c} for s, c in rows]}


def errors(since: str | None = None, limit: int = 25) -> dict:
    """Error/critical events in the window — the signal among the noise."""
    since_iso = _parse_since(since)
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT ts, type, source, actor_kind, message, severity FROM events "
            "WHERE ts >= ? AND severity IN ('error', 'critical') ORDER BY ts DESC LIMIT ?",
            [since_iso, min(limit, 100)],
        ).fetchall()
        warns = conn.execute(
            "SELECT COUNT(*) FROM events WHERE ts >= ? AND severity = 'warning'",
            [since_iso],
        ).fetchone()
    finally:
        conn.close()
    events = [
        {"ts": ts, "type": t, "source": s, "actor": a, "message": m[:200], "severity": sev}
        for ts, t, s, a, m, sev in rows
    ]
    return {"since": since_iso, "errors": events, "warning_count": warns[0] if warns else 0}


def timeline(hour_slots: int = 12, event_type: str | None = None) -> dict:
    """Hourly event counts for the last N hours — shows activity shape over time."""
    hour_slots = max(1, min(hour_slots, 48))
    since = datetime.now(timezone.utc) - timedelta(hours=hour_slots)
    conn = _conn()
    try:
        if event_type:
            rows = conn.execute(
                "SELECT substr(ts, 1, 13) AS hour, COUNT(*) FROM events WHERE ts >= ? AND type = ? GROUP BY hour ORDER BY hour",
                [since.isoformat(), event_type],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT substr(ts, 1, 13) AS hour, COUNT(*) FROM events WHERE ts >= ? GROUP BY hour ORDER BY hour",
                [since.isoformat()],
            ).fetchall()
    finally:
        conn.close()
    return {"hours": hour_slots, "event_type": event_type or "all", "buckets": [{"hour": h, "count": c} for h, c in rows]}


# ── Tool registry for the LLM ────────────────────────────────────────

ANALYTICS_TOOLS = {
    "summary": summary,
    "by_type": by_type,
    "top_sources": top_sources,
    "errors": errors,
    "timeline": timeline,
}


def run_analytics_query(args: dict) -> dict:
    """Execute an analytics tool by name. Used by chat_agent via `event_query` action."""
    query = (args.get("query") or "").strip()
    if query not in ANALYTICS_TOOLS:
        return {
            "success": False,
            "error": f"Unknown query '{query}'. Available: {', '.join(sorted(ANALYTICS_TOOLS))}",
        }
    fn = ANALYTICS_TOOLS[query]
    kwargs = {}
    if "since" in args:
        kwargs["since"] = args["since"]
    if query == "by_type":
        kwargs["event_type"] = args.get("event_type", "")
        if not kwargs["event_type"]:
            return {"success": False, "error": "event_type is required (e.g. 'service.fail')"}
        if "limit" in args:
            kwargs["limit"] = args["limit"]
    if query == "timeline":
        if "hours" in args:
            kwargs["hour_slots"] = args["hours"]
        if "event_type" in args:
            kwargs["event_type"] = args["event_type"]
    if query == "top_sources":
        if "limit" in args:
            kwargs["limit"] = args["limit"]
    if query == "errors":
        if "limit" in args:
            kwargs["limit"] = args["limit"]
    try:
        result = fn(**kwargs)
        return {"success": True, "result": result}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
