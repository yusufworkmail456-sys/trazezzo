"""Timeline visualization — causal timeline from warm store events."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from trazezzo.agent.store.sqlite_warm import get_store
from trazezzo.agent.schema import EventType


def build_timeline(
    before: datetime | None = None,
    window_minutes: int = 30,
    limit: int = 500,
) -> dict:
    """Build a vis-timeline-compatible dataset from recent events.

    Shows what happened in the window before a point in time (causal analysis).
    """
    store = get_store()
    events = store.get_timeline(before=before, window_minutes=window_minutes, limit=limit)

    items = []
    groups = set()

    for ev in events:
        # Determine group by actor kind
        group = ev.actor.kind.value
        groups.add(group)

        # Color by severity
        color_map = {
            "critical": "#ef4444",
            "error": "#f87171",
            "warning": "#fbbf24",
            "info": "#3b82f6",
            "debug": "#6b7280",
        }
        color = color_map.get(ev.severity, "#3b82f6")

        items.append({
            "id": ev.id or f"{ev.ts.isoformat()}-{ev.type.value}",
            "start": ev.ts.isoformat(),
            "content": f"{ev.type.value}: {ev.message[:60]}",
            "group": group,
            "className": f"sev-{ev.severity}",
            "style": f"background-color: {color};",
            "title": f"""Type: {ev.type.value}
Source: {ev.source}
Actor: {ev.actor.kind.value}:{ev.actor.id}
Severity: {ev.severity}
Message: {ev.message}
Time: {ev.ts.isoformat()}""",
        })

    return {
        "items": items,
        "groups": [{"id": g, "content": g} for g in sorted(groups)],
        "metadata": {
            "total_events": len(items),
            "window_minutes": window_minutes,
            "start_time": (before - timedelta(minutes=window_minutes)).isoformat() if before else None,
            "end_time": before.isoformat() if before else None,
        },
    }


def build_activity_feed(
    actor_kind: str | None = None,
    limit: int = 100,
) -> dict:
    """Build activity feed (recent events, grouped by actor)."""
    store = get_store()

    if actor_kind:
        events = store.query(actor_kind=actor_kind, limit=limit)
    else:
        events = store.query(limit=limit)

    # Group counts
    counts = {"user": 0, "agent": 0, "system": 0}
    for ev in events:
        counts[ev.actor.kind.value] = counts.get(ev.actor.kind.value, 0) + 1

    items = []
    for ev in events:
        items.append({
            "id": ev.id,
            "ts": ev.ts.isoformat(),
            "type": ev.type.value,
            "source": ev.source,
            "actor_kind": ev.actor.kind.value,
            "actor_id": ev.actor.id,
            "message": ev.message,
            "severity": ev.severity,
            "payload": ev.payload,
        })

    return {
        "events": items,
        "counts": counts,
        "total": len(items),
    }
