"""Live journal log stream — tails journald and broadcasts lines to dashboard clients.

Reuses the same pub/sub bus as the events WebSocket. Consumers receive JSON:
    {"line": "2026-10-07T01:46:14+08:00 host unit[pid]: message"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess

from trazezzo.dashboard.ws import _subscribers, publish_event

log = logging.getLogger("trazezzo.logs_ws")

# Client marker so event consumers can distinguish log lines from events
LOG_CHANNEL = "logs"


def publish_log_line(line: str):
    """Broadcast a journal line to all WebSocket subscribers."""
    try:
        publish_event({"channel": LOG_CHANNEL, "line": line})
    except Exception:
        pass


async def journal_log_streamer():
    """Tail journald and broadcast every new line to connected clients.

    Runs one `journalctl --follow` process for the whole dashboard; lines are
    fanned out through the shared WebSocket bus. Started alongside the app.
    """
    while True:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "--follow", "--no-pager", "--output=short-iso",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            log.info("journal log streamer started (journalctl --follow)")
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                if text.strip():
                    publish_log_line(text)
        except asyncio.CancelledError:
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
            raise
        except Exception as exc:
            log.error("journal log streamer error: %s — restarting in 5s", exc)
            await asyncio.sleep(5)
