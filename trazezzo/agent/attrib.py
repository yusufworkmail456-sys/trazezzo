"""Attribution layer — classify each event's actor (user/agent/system)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from trazezzo.agent.schema import Actor, ActorKind

log = logging.getLogger("trazezzo.attrib")

# ── Known login sessions: uid → (username, session_info) ───────────────
_active_sessions: dict[int, dict] = {}

# Process env var set by Trazezzo agent when spawning actions
AGENT_TRACE_ENV = "TRAZEZZO_TRACE_ID"


def refresh_sessions():
    """Snapshot active login sessions from /proc + loginctl."""
    global _active_sessions
    sessions = {}
    try:
        # Read wtmp-ish via loginctl
        import subprocess

        result = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend", "--output=short"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 3:
                sessions[parts[0]] = {
                    "uid": parts[1] if parts[1].isdigit() else None,
                    "user": parts[2],
                    "session_id": parts[0],
                }
    except Exception:
        pass
    _active_sessions = sessions


def _check_agent_env(pid: int | None) -> str | None:
    """Check if process has TRAZEZZO_TRACE_ID env var set."""
    if not pid:
        return None
    try:
        env_path = Path(f"/proc/{pid}/environ")
        if env_path.exists():
            environ = env_path.read_bytes()
            for entry in environ.split(b"\x00"):
                if entry.startswith(AGENT_TRACE_ENV.encode()):
                    return entry.decode().split("=", 1)[1]
    except Exception:
        pass
    return None


def _get_uid_username(uid: int | None) -> str:
    """Resolve uid to username."""
    if uid is None:
        return "system"
    if uid == 0:
        return "root"
    try:
        import pwd

        return pwd.getpwuid(uid).pw_name
    except Exception:
        return f"uid:{uid}"


def attribute(
    pid: int | None = None,
    uid: int | None = None,
    session: str | None = None,
    comm: str | None = None,
    source: str = "",
) -> Actor:
    """Determine the actor for an event.

    Priority:
    1. Process has TRAZEZZO_TRACE_ID → agent
    2. uid maps to login session → user
    3. Otherwise → system
    """
    # Check agent trace env
    trace_id = _check_agent_env(pid)
    if trace_id:
        return Actor(
            kind=ActorKind.AGENT,
            id="trazezzo",
            session=f"trace:{trace_id}",
            uid=uid,
            pid=pid,
        )

    # Check if uid corresponds to a real login session
    if uid is not None and uid >= 1000:
        username = _get_uid_username(uid)
        return Actor(
            kind=ActorKind.USER,
            id=username,
            session=session,
            uid=uid,
            pid=pid,
        )

    # root actions: check if from login session
    if uid == 0:
        # sudo/root login → still user if session present
        if session:
            return Actor(
                kind=ActorKind.USER,
                id="root",
                session=session,
                uid=uid,
                pid=pid,
            )
        # root without session = system
        return Actor(
            kind=ActorKind.SYSTEM,
            id="root",
            uid=uid,
            pid=pid,
        )

    # Default: system
    return Actor(
        kind=ActorKind.SYSTEM,
        id=comm or "kernel",
        pid=pid,
        uid=uid,
    )
