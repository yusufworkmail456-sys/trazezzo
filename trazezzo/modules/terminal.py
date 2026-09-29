"""Terminal module — web shell via WebSocket (FastAPI/Starlette)."""

from __future__ import annotations

import asyncio
import os
import pty
import signal
import struct
import logging

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("trazezzo.terminal")


async def terminal_websocket(websocket: WebSocket):
    """Spawn a PTY shell and bridge to FastAPI WebSocket."""
    await websocket.accept()

    # Create pseudo-terminal
    master_fd, slave_fd = pty.openpty()

    # Set window size
    def set_size(rows=24, cols=80):
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        import fcntl
        import termios
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

    set_size()

    # Spawn shell
    os.chdir("/root")
    shell = os.environ.get("SHELL", "/bin/bash")
    pid = os.fork()
    if pid == 0:
        # Child: exec shell in slave PTY
        os.setsid()
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(master_fd)
        os.close(slave_fd)
        os.execvp(shell, [shell, "-i"])

    os.close(slave_fd)
    log.info("Local terminal started: pid=%d", pid)

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
            except Exception:
                break

        try:
            await websocket.send_text("\r\n\x1b[33m● Terminal closed.\x1b[0m\r\n")
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
                message = await websocket.receive_text()

                # Handle resize command
                if message.startswith("\x1b]resize;"):
                    try:
                        parts = message.split(";")
                        rows = int(parts[1])
                        cols = int(parts[2].rstrip("\x07"))
                        set_size(rows, cols)
                    except Exception:
                        pass
                    continue

                os.write(master_fd, message.encode())
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log.debug("Write PTY error: %s", exc)

    try:
        await asyncio.gather(read_pty(), write_pty())
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            os.close(master_fd)
        except Exception:
            pass
        log.info("Local terminal ended: pid=%d", pid)
