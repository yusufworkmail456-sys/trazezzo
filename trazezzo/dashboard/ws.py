"""WebSocket handler — live event stream to dashboard clients."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("trazezzo.ws")

# ── Pub/sub bus for live events ───────────────────────────────────────
_subscribers: set[WebSocket] = set()
_recent_events: deque[dict] = deque(maxlen=100)


def publish_event(event_dict: dict):
    """Called by agent to push a live event to all WS subscribers.

    Thread-safe: works from both asyncio context and background threads.
    """
    _recent_events.append(event_dict)
    for ws in list(_subscribers):
        try:
            # Try asyncio first (same event loop — bundle mode)
            loop = asyncio.get_running_loop()

            async def _safe_send(w=ws):
                try:
                    await w.send_text(json.dumps(event_dict))
                except Exception:
                    _subscribers.discard(w)

            asyncio.ensure_future(_safe_send(), loop=loop)
        except RuntimeError:
            # No running loop (called from thread) — use thread-safe schedule
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():

                    async def _safe_send_threaded(w=ws):
                        try:
                            await w.send_text(json.dumps(event_dict))
                        except Exception:
                            _subscribers.discard(w)

                    asyncio.run_coroutine_threadsafe(_safe_send_threaded(), loop)
                else:
                    raise RuntimeError("No running event loop")
            except Exception:
                _subscribers.discard(ws)


async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for live event stream."""
    await ws.accept()
    _subscribers.add(ws)
    log.info("WS client connected (%d total)", len(_subscribers))

    # Send recent events as backlog
    for event in list(_recent_events):
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            break

    try:
        while True:
            # Keep connection alive, handle client messages if any
            data = await ws.receive_text()
            # Client can request specific filters
            if data.startswith("{"):
                msg = json.loads(data)
                # Future: filter by actor_kind, type, etc.
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.error("WS error: %s", exc)
    finally:
        _subscribers.discard(ws)
        log.info("WS client disconnected (%d total)", len(_subscribers))
