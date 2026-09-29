"""SSH connection manager — profiles, key vault, groups.

Stores connection profiles in JSON. Supports:
- Key-based auth (reference existing ~/.ssh/ keys or upload new)
- Password-based auth (sshpass)
- ProxyJump chaining (use another connection as jump host)
- Grouping for organization
"""

from __future__ import annotations

import json
import os
import uuid
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from trazezzo.config import DATA_DIR

# ── Storage ───────────────────────────────────────────────────────────
CONNECTIONS_FILE = DATA_DIR / "ssh_connections.json"
KEY_VAULT_DIR = DATA_DIR / "ssh-keys"


def _ensure_dirs():
    KEY_VAULT_DIR.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    """Load connections data."""
    try:
        if CONNECTIONS_FILE.exists():
            return json.loads(CONNECTIONS_FILE.read_text())
    except Exception:
        pass
    return {"connections": []}


def _save(data: dict) -> None:
    """Save connections data."""
    CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONNECTIONS_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(CONNECTIONS_FILE, 0o600)


# ── Connection CRUD ──────────────────────────────────────────────────

def list_connections(group: str | None = None) -> list[dict]:
    """List all connections, optionally filtered by group."""
    data = _load()
    conns = data.get("connections", [])
    if group:
        conns = [c for c in conns if c.get("group") == group]
    # Don't return passwords
    return [_sanitize(c) for c in conns]


def get_connection(conn_id: str) -> dict | None:
    """Get a single connection by ID (includes password for internal use)."""
    data = _load()
    for c in data.get("connections", []):
        if c["id"] == conn_id:
            return c
    return None


def add_connection(
    label: str,
    host: str,
    port: int = 22,
    user: str = "root",
    auth_method: str = "key",
    key_path: str | None = None,
    password: str | None = None,
    group: str = "default",
    proxy_jump: str | None = None,
) -> dict:
    """Add a new SSH connection profile."""
    _ensure_dirs()
    data = _load()

    conn = {
        "id": f"conn-{uuid.uuid4().hex[:8]}",
        "label": label,
        "host": host,
        "port": port,
        "user": user,
        "auth_method": auth_method,  # "key" | "password"
        "key_path": key_path,
        "password": password if auth_method == "password" else None,
        "group": group,
        "proxy_jump": proxy_jump,  # connection ID of jump host
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_connected": None,
    }

    data.setdefault("connections", []).append(conn)
    _save(data)
    return _sanitize(conn)


def update_connection(conn_id: str, **kwargs) -> dict | None:
    """Update a connection profile."""
    data = _load()
    for c in data.get("connections", []):
        if c["id"] == conn_id:
            for k, v in kwargs.items():
                if k in c or k in ("label", "host", "port", "user", "auth_method",
                                   "key_path", "password", "group", "proxy_jump"):
                    c[k] = v
            _save(data)
            return _sanitize(c)
    return None


def delete_connection(conn_id: str) -> bool:
    """Delete a connection profile."""
    data = _load()
    before = len(data.get("connections", []))
    data["connections"] = [c for c in data.get("connections", []) if c["id"] != conn_id]
    if len(data["connections"]) < before:
        _save(data)
        return True
    return False


def mark_connected(conn_id: str):
    """Update last_connected timestamp."""
    update_connection(conn_id, last_connected=datetime.now(timezone.utc).isoformat())


def list_groups() -> list[dict]:
    """List all groups with connection counts."""
    data = _load()
    groups: dict[str, int] = {}
    for c in data.get("connections", []):
        g = c.get("group", "default")
        groups[g] = groups.get(g, 0) + 1
    return [{"name": g, "count": n} for g, n in sorted(groups.items())]


# ── SSH Key Vault ─────────────────────────────────────────────────────

def list_ssh_keys() -> list[dict]:
    """List available SSH keys: vault keys + ~/.ssh/ keys."""
    keys = []

    # Vault keys (uploaded)
    _ensure_dirs()
    for f in sorted(KEY_VAULT_DIR.iterdir()):
        if f.is_file() and not f.name.endswith(".pub"):
            try:
                stat = f.stat()
                fingerprint = _key_fingerprint(str(f))
                keys.append({
                    "id": f.name,
                    "name": f.name,
                    "path": str(f),
                    "source": "vault",
                    "fingerprint": fingerprint,
                    "size": stat.st_size,
                })
            except Exception:
                pass

    # ~/.ssh/ keys (existing on server)
    ssh_dir = Path.home() / ".ssh"
    if ssh_dir.exists():
        for f in sorted(ssh_dir.iterdir()):
            if f.is_file() and not f.name.endswith(".pub") and not f.name.startswith("known_hosts") and not f.name.startswith("authorized"):
                try:
                    # Verify it looks like a private key
                    content = f.read_text(errors="ignore").strip()
                    if "PRIVATE KEY" in content or "OPENSSH" in content:
                        fingerprint = _key_fingerprint(str(f))
                        keys.append({
                            "id": f.name,
                            "name": f"~/.ssh/{f.name}",
                            "path": str(f),
                            "source": "server",
                            "fingerprint": fingerprint,
                        })
                except Exception:
                    pass

    return keys


def upload_ssh_key(name: str, content: str) -> dict:
    """Upload a private key to the vault."""
    _ensure_dirs()
    # Sanitize name
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_.").strip()
    if not safe_name:
        safe_name = f"key-{uuid.uuid4().hex[:6]}"
    if not safe_name.endswith(".key"):
        safe_name += ".key"

    key_path = KEY_VAULT_DIR / safe_name
    key_path.write_text(content)
    os.chmod(key_path, 0o600)

    return {
        "id": safe_name,
        "name": safe_name,
        "path": str(key_path),
        "source": "vault",
        "fingerprint": _key_fingerprint(str(key_path)),
    }


def delete_ssh_key(key_id: str) -> bool:
    """Delete a vault key (only vault keys, not ~/.ssh/)."""
    key_path = KEY_VAULT_DIR / key_id
    if key_path.exists() and key_path.is_file():
        key_path.unlink()
        return True
    return False


def _key_fingerprint(path: str) -> str:
    """Get SSH key fingerprint."""
    try:
        result = subprocess.run(
            ["ssh-keygen", "-lf", path],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                return parts[1]
    except Exception:
        pass
    return "unknown"


# ── Build SSH command ────────────────────────────────────────────────

def build_ssh_command(conn_id: str) -> list[str] | None:
    """Build the SSH command for a connection.

    Returns command list ready for subprocess/PTY.
    Handles key auth, password auth (sshpass), and ProxyJump.
    """
    conn = get_connection(conn_id)
    if not conn:
        return None

    cmd = []

    # Password auth via sshpass
    if conn.get("auth_method") == "password" and conn.get("password"):
        cmd.extend(["sshpass", "-p", conn["password"]])

    # SSH base
    cmd.extend(["ssh", "-tt", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10"])

    # Key auth
    if conn.get("auth_method") == "key" and conn.get("key_path"):
        cmd.extend(["-i", conn["key_path"]])

    # ProxyJump
    if conn.get("proxy_jump"):
        jump_conn = get_connection(conn["proxy_jump"])
        if jump_conn:
            jump_target = f"{jump_conn['user']}@{jump_conn['host']}"
            if jump_conn.get("port") and jump_conn["port"] != 22:
                jump_target += f":{jump_conn['port']}"
            cmd.extend(["-J", jump_target])

    # Target
    port = conn.get("port", 22)
    if port != 22:
        cmd.extend(["-p", str(port)])
    cmd.append(f"{conn['user']}@{conn['host']}")

    return cmd


# ── Helpers ──────────────────────────────────────────────────────────

def _sanitize(conn: dict) -> dict:
    """Remove password from connection for API response."""
    c = dict(conn)
    if c.get("password"):
        c["password"] = "•••••"
    return c


def test_connection(conn_id: str) -> dict:
    """Test SSH connection (run 'echo ok' on remote)."""
    cmd = build_ssh_command(conn_id)
    if not cmd:
        return {"success": False, "error": "Connection not found"}

    # Append test command
    test_cmd = cmd + ["echo", "TRZEZZO_SSH_OK"]
    try:
        result = subprocess.run(
            test_cmd,
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and "TRZEZZO_SSH_OK" in result.stdout:
            mark_connected(conn_id)
            return {"success": True, "output": result.stdout.strip()}
        else:
            return {
                "success": False,
                "error": result.stderr.strip() or "Connection failed",
                "exit_code": result.returncode,
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Connection timed out (15s)"}
    except FileNotFoundError:
        return {"success": False, "error": "sshpass not installed (apt install sshpass)"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
