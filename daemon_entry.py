"""Entry point for systemd service — runs agent daemon + dashboard together."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading

from trazezzo.agent.daemon import AgentDaemon
from trazezzo.config import DASHBOARD_HOST, DASHBOARD_PORT

log = logging.getLogger("trazezzo")


async def run_all():
    """Run agent daemon + dashboard concurrently in single process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )

    # Start agent daemon
    daemon = AgentDaemon()
    await daemon.start()
    log.info("Agent daemon started")

    # Start dashboard in background thread (uvicorn is sync-blocking from asyncio's POV)
    import uvicorn
    from trazezzo.dashboard.app import app

    config = uvicorn.Config(
        app,
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)

    # Run uvicorn in a thread
    def run_uvicorn():
        server.run()
    uvicorn_thread = threading.Thread(target=run_uvicorn, daemon=True)
    uvicorn_thread.start()
    log.info("Dashboard started on %s:%d", DASHBOARD_HOST, DASHBOARD_PORT)

    # Wait for shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown(stop_event, daemon, server)))

    await stop_event.wait()


async def _shutdown(stop_event, daemon, server):
    log.info("Shutting down...")
    await daemon.stop()
    server.should_exit = True
    stop_event.set()


def main():
    asyncio.run(run_all())


if __name__ == "__main__":
    main()
