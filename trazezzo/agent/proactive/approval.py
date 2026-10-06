"""Pending approval queue for aggressive mode actions.

Stores proposed commands that require user approval before execution.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from trazezzo.config import DATA_DIR

APPROVAL_FILE = DATA_DIR / "pending_approvals.json"


def _load() -> list[dict]:
    """Load pending approvals."""
    try:
        if APPROVAL_FILE.exists():
            return json.loads(APPROVAL_FILE.read_text())
    except Exception:
        pass
    return []


def _save(approvals: list[dict]) -> None:
    """Save pending approvals."""
    APPROVAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPROVAL_FILE.write_text(json.dumps(approvals, indent=2))


def store_pending_approval(cmd: str, finding: dict, store=None) -> dict:
    """Store a proposed action that needs user approval."""
    approval_id = str(uuid.uuid4())[:8]
    entry = {
        "id": approval_id,
        "cmd": cmd,
        "finding": finding,
        "status": "pending",  # pending | approved | rejected | executed | failed
        "created_at": datetime.now(timezone.utc).isoformat(),
        "executed_at": None,
        "result": None,
    }
    approvals = _load()
    # Remove old executed/rejected entries (keep max 50)
    approvals = [a for a in approvals if a["status"] in ("pending",)]
    approvals.append(entry)
    approvals = approvals[-50:]
    _save(approvals)
    return entry


def list_pending() -> list[dict]:
    """List all pending approvals."""
    return [a for a in _load() if a["status"] == "pending"]


def list_all() -> list[dict]:
    """List all approvals (pending + history)."""
    return _load()


def approve_and_execute(approval_id: str, store=None) -> dict:
    """Approve and execute a pending action."""
    approvals = _load()
    entry = None
    for a in approvals:
        if a["id"] == approval_id:
            entry = a
            break
    if not entry:
        return {"error": "Approval not found"}
    if entry["status"] != "pending":
        return {"error": f"Approval already {entry['status']}"}

    cmd = entry["cmd"]
    result = {"command": cmd, "approval_id": approval_id}
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30,
        )
        result["exit_code"] = proc.returncode
        result["stdout"] = proc.stdout[-500:]
        result["stderr"] = proc.stderr[-500:]
        result["success"] = proc.returncode == 0
    except subprocess.TimeoutExpired:
        result["error"] = "Command timed out (30s)"
        result["success"] = False
    except Exception as exc:
        result["error"] = str(exc)
        result["success"] = False

    # Update entry
    entry["status"] = "executed" if result.get("success") else "failed"
    entry["executed_at"] = datetime.now(timezone.utc).isoformat()
    entry["result"] = result
    _save(approvals)

    # Record in event store
    if store:
        from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
        store.add(ServerEvent(
            type=EventType.AGENT_ACTION,
            source="proactive",
            actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="proactive:approved"),
            message=f"Executed (approved): {cmd} → {'OK' if result.get('success') else 'FAILED'}",
            payload=result,
            severity="warning",
        ))

    # Publish to exec WebSocket
    try:
        from trazezzo.dashboard.exec_ws import publish_execution
        publish_execution({
            "command": cmd,
            "source": "proactive:approved",
            "success": result.get("success", False),
            "output": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "message": f"Executed (approved): {cmd}",
        })
    except Exception:
        pass

    return result


def reject_approval(approval_id: str, store=None) -> dict:
    """Reject a pending action."""
    approvals = _load()
    entry = None
    for a in approvals:
        if a["id"] == approval_id:
            entry = a
            break
    if not entry:
        return {"error": "Approval not found"}
    if entry["status"] != "pending":
        return {"error": f"Approval already {entry['status']}"}

    entry["status"] = "rejected"
    entry["executed_at"] = datetime.now(timezone.utc).isoformat()
    _save(approvals)

    if store:
        from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
        store.add(ServerEvent(
            type=EventType.AGENT_ADVISORY,
            source="proactive",
            actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="proactive:rejected"),
            message=f"Rejected: {entry['cmd']}",
            payload={"cmd": entry["cmd"], "id": approval_id},
            severity="info",
        ))

    return {"success": True, "status": "rejected"}
