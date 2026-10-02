"""SSH terminal bridge — spawn SSH via PTY, bridge to WebSocket.

Each WebSocket connection spawns an SSH process via PTY.
Supports multiple concurrent sessions (multi-tab).
"""

from __future__ import annotations

import asyncio
import os
import pty
import signal
import struct
import logging
from typing import AsyncGenerator

from fastapi import WebSocket, WebSocketDisconnect

from trazezzo.modules.ssh_connections import build_ssh_command, mark_connected

log = logging.getLogger("trazezzo.ssh_terminal")

# ── Active sessions tracking ──────────────────────────────────────────
_active_sessions: dict[str, dict] = {}  # session_id -> {ws, pid, conn_id}


async def ssh_terminal_ws(
    websocket: WebSocket,
    conn_id: str,
):
    """WebSocket endpoint for SSH terminal session.

    Spawns SSH process via PTY, bridges stdin/stdout to WebSocket.
    Supports resize messages.
    """
    await websocket.accept()

    # Build SSH command
    cmd = build_ssh_command(conn_id)
    if not cmd:
        await websocket.send_text(f"\r\n\033[31mError: Connection '{conn_id}' not found.\033[0m\r\n")
        await websocket.close()
        return

    # Create PTY
    master_fd, slave_fd = pty.openpty()

    def set_size(rows: int = 24, cols: int = 80):
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        import fcntl
        import termios
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

    set_size()

    # Spawn SSH process
    pid = os.fork()
    if pid == 0:
        # Child: exec SSH in slave PTY
        os.setsid()
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(master_fd)
        os.close(slave_fd)
        os.execvp(cmd[0], cmd)

    os.close(slave_fd)

    session_id = f"sess-{pid}"
    _active_sessions[session_id] = {"ws": websocket, "pid": pid, "conn_id": conn_id}
    mark_connected(conn_id)

    log.info("SSH session started: conn=%s pid=%d (%d active)",
             conn_id, pid, len(_active_sessions))

    # Send connection info
    await websocket.send_text(
        f"\r\n\033[32m● Connecting to {conn_id}...\033[0m\r\n"
    )

    async def read_pty():
        """Read from PTY and send to WebSocket."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                if data:
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
                else:
                    break
            except (OSError, Exception):
                break

        try:
            await websocket.send_text(
                "\r\n\033[33m● SSH session closed.\033[0m\r\n"
            )
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass

    async def write_pty():
        """Read from WebSocket and write to PTY."""
        try:
            while True:
                msg = await websocket.receive_text()

                # Handle resize command
                if msg.startswith("\x1b]resize;"):
                    # Format: \x1b]resize;rows;cols\x07
                    try:
                        parts = msg.split(";")
                        rows = int(parts[1])
                        cols = int(parts[2].rstrip("\x07"))
                        set_size(rows, cols)
                    except Exception:
                        pass
                    continue

                # Normal input
                os.write(master_fd, msg.encode())

        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log.debug("Write PTY error: %s", exc)

    try:
        await asyncio.gather(read_pty(), write_pty())
    except Exception as exc:
        log.debug("SSH session error: %s", exc)
    finally:
        # Cleanup
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            os.close(master_fd)
        except Exception:
            pass

        _active_sessions.pop(session_id, None)
        log.info("SSH session ended: conn=%s pid=%d (%d active)",
                 conn_id, pid, len(_active_sessions))


def list_active_sessions() -> list[dict]:
    """List active SSH terminal sessions."""
    return [
        {
            "session_id": sid,
            "conn_id": s["conn_id"],
            "pid": s["pid"],
        }
        for sid, s in _active_sessions.items()
    ]
