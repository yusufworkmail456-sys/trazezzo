"""Event schema — single pipeline, attribution layer filters by actor.kind."""

from __future__ import annotations

from enum import Enum
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class ActorKind(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class EventType(str, Enum):
    # system/service events
    SERVICE_START = "service.start"
    SERVICE_STOP = "service.stop"
    SERVICE_FAIL = "service.fail"
    SERVICE_RESTART = "service.restart"
    # process events
    PROCESS_EXEC = "process.exec"
    PROCESS_KILL = "process.kill"
    PROCESS_OOM = "process.oom"
    # network events
    NET_CONNECT = "net.connect"
    NET_LISTEN = "net.listen"
    # file events
    FILE_WRITE = "file.write"
    FILE_DELETE = "file.delete"
    FILE_CONFIG_CHANGE = "file.config_change"
    # auth/session events
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_FAIL = "auth.fail"
    AUTH_SUDO = "auth.sudo"
    # user/session
    SESSION_OPEN = "session.open"
    SESSION_CLOSE = "session.close"
    # metrics/anomaly
    METRIC_SPIKE = "metric.spike"
    ANOMALY = "anomaly"
    # agent
    AGENT_ACTION = "agent.action"
    AGENT_ADVISORY = "agent.advisory"
    # generic log
    LOG = "log"


class Actor(BaseModel):
    kind: ActorKind
    id: str = "system"
    session: str | None = None  # e.g. "pts/0@1.2.3.4"
    uid: int | None = None
    pid: int | None = None


class ServerEvent(BaseModel):
    """A single recorded event in the black box recorder."""

    id: int | None = None
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: EventType
    source: str  # "journald", "auditd", "psutil", "agent", etc.
    actor: Actor
    message: str = ""
    payload: dict = Field(default_factory=dict)
    severity: str = "info"  # debug/info/warning/error/critical

    def to_db_tuple(self) -> tuple:
        """Serialize for SQLite insertion."""
        import json

        return (
            self.ts.isoformat(),
            self.type.value,
            self.source,
            self.actor.kind.value,
            self.actor.id,
            self.actor.session,
            self.actor.uid,
            self.actor.pid,
            self.message,
            json.dumps(self.payload),
            self.severity,
        )

    def to_dict(self) -> dict:
        """Serialize to dict for WebSocket/SSE live streaming."""
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "type": self.type.value,
            "source": self.source,
            "actor": {
                "kind": self.actor.kind.value,
                "id": self.actor.id,
                "session": self.actor.session,
                "uid": self.actor.uid,
                "pid": self.actor.pid,
            },
            "message": self.message,
            "payload": self.payload,
            "severity": self.severity,
        }

    @classmethod
    def from_db_row(cls, row: tuple) -> "ServerEvent":
        """Deserialize from SQLite row."""
        import json

        return cls(
            id=row[0],
            ts=datetime.fromisoformat(row[1]),
            type=EventType(row[2]),
            source=row[3],
            actor=Actor(
                kind=ActorKind(row[4]),
                id=row[5],
                session=row[6],
                uid=row[7],
                pid=row[8],
            ),
            message=row[9],
            payload=json.loads(row[10]) if row[10] else {},
            severity=row[11],
        )
