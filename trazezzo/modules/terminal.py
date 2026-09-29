"""Terminal module — web shell via WebSocket."""

from __future__ import annotations

import asyncio
import os
import pty
import signal
import struct

import websockets


async def terminal_websocket(websocket):
    """Spawn a PTY shell and bridge to WebSocket."""
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

    async def read_pty():
        """Read from PTY and send to WebSocket."""
        loop = asyncio.get_event_loop()
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 1024)
                if data:
                    await websocket.send(data.decode("utf-8", errors="replace"))
                else:
                    break
            except Exception:
                break

    async def write_pty():
        """Read from WebSocket and write to PTY."""
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    os.write(master_fd, message)
                else:
                    os.write(master_fd, message.encode())
        except Exception:
            pass

    try:
        await asyncio.gather(read_pty(), write_pty())
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        os.close(master_fd)
