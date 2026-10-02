"""SQLite WAL store — warm tier ring buffer with crash-safe writes."""

from __future__ import annotations

import sqlite3
import threading
import time
import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Sequence

from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
from trazezzo.config import (
    DB_PATH,
    WARM_RETENTION_DAYS,
    RING_BUFFER_HOT_SECONDS,
    BATCH_INSERT_INTERVAL,
    BATCH_INSERT_MAX_EVENTS,
)

log = logging.getLogger("trazezzo.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    source TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_session TEXT,
    actor_uid INTEGER,
    actor_pid INTEGER,
    message TEXT DEFAULT '',
    payload TEXT DEFAULT '{}',
    severity TEXT DEFAULT 'info'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_actor_kind ON events(actor_kind);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
"""

# ── In-memory hot ring buffer ─────────────────────────────────────────
_hot_buffer: deque[ServerEvent] = deque()
_hot_lock = threading.Lock()


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


class WarmStore:
    """Ring-buffer-backed SQLite store with batched writes + auto-prune."""

    def __init__(self):
        self._conn = _get_db()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._pending: list[ServerEvent] = []
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()
        self._flusher: threading.Thread | None = None
        self._pruner: threading.Thread | None = None

    # ── public API ────────────────────────────────────────────────────

    def start(self):
        """Start background flush + prune threads."""
        self._flusher = threading.Thread(target=self._flush_loop, daemon=True)
        self._pruner = threading.Thread(target=self._prune_loop, daemon=True)
        self._flusher.start()
        self._pruner.start()
        log.info("WarmStore started (db=%s, retention=%dd)", DB_PATH, WARM_RETENTION_DAYS)

    def stop(self):
        self._stop.set()
        self.flush_now()
        if self._flusher:
            self._flusher.join(timeout=5)
        if self._pruner:
            self._pruner.join(timeout=5)
        self._conn.close()

    def add(self, event: ServerEvent):
        """Add event to hot buffer + pending queue + live stream."""
        with _hot_lock:
            _hot_buffer.append(event)
            # trim hot buffer to window
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=RING_BUFFER_HOT_SECONDS)
            while _hot_buffer and _hot_buffer[0].ts < cutoff:
                _hot_buffer.popleft()
        with self._pending_lock:
            self._pending.append(event)
            if len(self._pending) >= BATCH_INSERT_MAX_EVENTS:
                self._flush_pending()
        # Push to live event bus (non-blocking, thread-safe)
        try:
            from trazezzo.dashboard.ws import publish_event
            publish_event(event.to_dict())
        except Exception:
            pass  # dashboard not loaded in agent-only mode

    def add_batch(self, events: Sequence[ServerEvent]):
        for e in events:
            self.add(e)

    def flush_now(self):
        with self._pending_lock:
            self._flush_pending()

    def query(
        self,
        actor_kind: str | None = None,
        event_type: str | None = None,
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
        severity: str | None = None,
    ) -> list[ServerEvent]:
        """Query warm store with optional filters."""
        clauses = []
        params: list = []
        if actor_kind:
            clauses.append("actor_kind = ?")
            params.append(actor_kind)
        if event_type:
            clauses.append("type = ?")
            params.append(event_type)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if since:
            clauses.append("ts >= ?")
            params.append(since.isoformat())
        if until:
            clauses.append("ts <= ?")
            params.append(until.isoformat())
        if severity:
            clauses.append("severity = ?")
            params.append(severity)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM events WHERE {where} ORDER BY ts DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [ServerEvent.from_db_row(r) for r in rows]

    def get_hot_buffer(self) -> list[ServerEvent]:
        """Snapshot of in-memory hot buffer (last N minutes)."""
        with _hot_lock:
            return list(_hot_buffer)

    def count(self, actor_kind: str | None = None) -> int:
        clause = "actor_kind = ?" if actor_kind else "1=1"
        param = [actor_kind] if actor_kind else []
        row = self._conn.execute(f"SELECT COUNT(*) FROM events WHERE {clause}", param).fetchone()
        return row[0] if row else 0

    def get_timeline(
        self,
        before: datetime | None = None,
        window_minutes: int = 30,
        limit: int = 500,
    ) -> list[ServerEvent]:
        """Causal timeline: events in window BEFORE a point in time."""
        if before is None:
            before = datetime.now(timezone.utc)
        since = before - timedelta(minutes=window_minutes)
        rows = self._conn.execute(
            "SELECT * FROM events WHERE ts >= ? AND ts <= ? ORDER BY ts ASC LIMIT ?",
            [since.isoformat(), before.isoformat(), limit],
        ).fetchall()
        return [ServerEvent.from_db_row(r) for r in rows]

    # ── internal ──────────────────────────────────────────────────────

    def _flush_pending(self):
        """Flush pending events to SQLite. Caller holds _pending_lock."""
        if not self._pending:
            return
        batch = self._pending[:]
        self._pending.clear()
        try:
            self._conn.executemany(
                """INSERT INTO events
                   (ts, type, source, actor_kind, actor_id, actor_session,
                    actor_uid, actor_pid, message, payload, severity)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [e.to_db_tuple() for e in batch],
            )
            self._conn.commit()
            log.debug("Flushed %d events to SQLite", len(batch))
        except Exception as exc:
            log.error("Flush failed: %s — requeuing %d events", exc, len(batch))
            self._pending = batch + self._pending

    def _flush_loop(self):
        while not self._stop.is_set():
            time.sleep(BATCH_INSERT_INTERVAL)
            with self._pending_lock:
                self._flush_pending()

    def _prune_loop(self):
        while not self._stop.is_set():
            self._prune()
            # prune every hour
            self._stop.wait(3600)

    def _prune(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=WARM_RETENTION_DAYS)
        try:
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff.isoformat(),))
            self._conn.commit()
            log.debug("Pruned events older than %s", cutoff.isoformat())
        except Exception as exc:
            log.error("Prune failed: %s", exc)


# ── Module-level singleton ─────────────────────────────────────────────
_store: WarmStore | None = None


def get_store() -> WarmStore:
    global _store
    if _store is None:
        _store = WarmStore()
    return _store
