"""Execution WebSocket — real-time agent execution display to dashboard clients."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("trazezzo.exec_ws")

# ── Pub/sub bus for live executions ───────────────────────────────────
_exec_subscribers: set[WebSocket] = set()
_recent_execs: deque[dict] = deque(maxlen=50)


async def exec_ws_endpoint(ws: WebSocket):
    """WebSocket endpoint for live execution stream."""
    await ws.accept()
    _exec_subscribers.add(ws)
    log.info("Exec WS client connected (%d total)", len(_exec_subscribers))

    # Send recent executions as backlog
    for ex in list(_recent_execs):
        try:
            await ws.send_text(json.dumps(ex))
        except Exception:
            break

    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.error("Exec WS error: %s", exc)
    finally:
        _exec_subscribers.discard(ws)
        log.info("Exec WS client disconnected (%d total)", len(_exec_subscribers))


def publish_execution(exec_data: dict):
    """Called by proactive agent / recommendations to push execution to all WS clients.

    exec_data: {command, source, success, output, stderr, message}
    """
    exec_data.setdefault("ts", datetime.now(timezone.utc).isoformat())
    _recent_execs.append(exec_data)

    msg = json.dumps(exec_data)
    for ws in list(_exec_subscribers):
        try:
            asyncio.create_task(ws.send_text(msg))
        except Exception:
            _exec_subscribers.discard(ws)
