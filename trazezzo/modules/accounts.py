"""Accounts module — user management."""

from __future__ import annotations

import subprocess
import pwd
import spwd


def get_users() -> list[dict]:
    """List system users."""
    users = []
    for entry in pwd.getpwall():
        users.append({
            "username": entry.pw_name,
            "uid": entry.pw_uid,
            "gid": entry.pw_gid,
            "home": entry.pw_dir,
            "shell": entry.pw_shell,
            "gecos": entry.pw_gecos,
            "is_system": entry.pw_uid < 1000,
        })
    return users


def get_login_users() -> list[dict]:
    """Users with login shells (non-system)."""
    all_users = get_users()
    return [u for u in all_users if not u["is_system"] and u["shell"] not in ("/usr/sbin/nologin", "/bin/false")]


def get_active_sessions() -> list[dict]:
    """List active login sessions via loginctl."""
    try:
        result = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
        sessions = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 3:
                sessions.append({
                    "session": parts[0],
                    "uid": parts[1],
                    "user": parts[2],
                    "seat": parts[3] if len(parts) > 3 else "",
                })
        return sessions
    except Exception:
        return []


def get_groups() -> list[dict]:
    """List groups."""
    import grp
    return [{"name": g.gr_name, "gid": g.gr_gid, "members": g.gr_mem} for g in grp.getgrall()]
