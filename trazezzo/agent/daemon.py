"""Agent daemon — orchestrates all capture sources + store + proactive loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from trazezzo.agent.store.sqlite_warm import get_store
from trazezzo.agent.capture.journald import capture_journald
from trazezzo.agent.capture.auditd import capture_auditd
from trazezzo.agent.capture.psutil_metrics import capture_psutil_metrics
from trazezzo.config import (
    CAPTURE_JOURNALD,
    CAPTURE_AUDITD,
    CAPTURE_PSUTIL,
    PROACTIVE_INTERVAL,
    PROACTIVE_MODE,
)
log = logging.getLogger("trazezzo.daemon")


class AgentDaemon:
    """Main agent event loop — manages capture coroutines + store."""

    def __init__(self):
        self.store = get_store()
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def start(self):
        """Start store + all capture sources."""
        self.store.start()
        log.info("Agent daemon starting...")

        if CAPTURE_JOURNALD:
            self._tasks.append(asyncio.create_task(capture_journald(self.store)))
        if CAPTURE_AUDITD:
            self._tasks.append(asyncio.create_task(capture_auditd(self.store)))
        if CAPTURE_PSUTIL:
            self._tasks.append(asyncio.create_task(capture_psutil_metrics(self.store)))

        # eBPF capture (optional, try import)
        try:
            from trazezzo.agent.capture.ebpf import capture_ebpf
            self._tasks.append(asyncio.create_task(capture_ebpf(self.store)))
        except Exception as exc:
            log.info("eBPF capture not available: %s", exc)

        # proactive loop
        self._tasks.append(asyncio.create_task(self._proactive_loop()))

        log.info("Agent daemon started — %d capture tasks", len(self._tasks))

    async def stop(self):
        """Graceful shutdown."""
        log.info("Agent daemon stopping...")
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.store.stop()
        log.info("Agent daemon stopped")

    async def _proactive_loop(self):
        """Proactive agent check loop — calm/aggressive mode."""
        from trazezzo.agent.proactive.scheduler import proactive_check

        log.info("Proactive agent started (mode=%s, interval=%ds)", PROACTIVE_MODE, PROACTIVE_INTERVAL)

        while not self._stop.is_set():
            try:
                await proactive_check(self.store, PROACTIVE_MODE)
            except Exception as exc:
                log.error("Proactive check error: %s", exc)
            await asyncio.sleep(PROACTIVE_INTERVAL)

    async def run(self):
        """Run until shutdown signal."""
        await self.start()

        loop = asyncio.get_running_loop()

        # Handle signals
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        await self._stop.wait()


def run_daemon():
    """Entry point for trazezzo agent daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )
    daemon = AgentDaemon()
    asyncio.run(daemon.run())
