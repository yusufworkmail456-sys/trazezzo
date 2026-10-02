"""SSH keys module — authorized_keys and host key management."""

from __future__ import annotations

import subprocess
from pathlib import Path
import hashlib


def get_authorized_keys() -> dict:
    """List authorized SSH keys for all login users."""
    import pwd
    keys = []
    for entry in pwd.getpwall():
        if entry.pw_uid < 1000:
            continue
        if entry.pw_shell in ("/usr/sbin/nologin", "/bin/false"):
            continue
        auth_keys_path = Path(entry.pw_dir) / ".ssh" / "authorized_keys"
        if not auth_keys_path.exists():
            continue
        try:
            with open(auth_keys_path) as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        key_type = parts[0]
                        key_data = parts[1]
                        fingerprint = hashlib.sha256(key_data.encode()).hexdigest()[:16]
                        comment = " ".join(parts[2:]) if len(parts) > 2 else ""
                        keys.append({
                            "user": entry.pw_name,
                            "line": i,
                            "key_type": key_type,
                            "fingerprint": f"SHA256:{fingerprint}",
                            "comment": comment,
                        })
        except Exception:
            pass
    return {"keys_list": keys, "count": len(keys)}


def get_host_keys() -> list[dict]:
    """List SSH host keys."""
    keys = []
    host_keys_dir = Path("/etc/ssh")
    if not host_keys_dir.exists():
        return keys
    for key_file in host_keys_dir.glob("ssh_host_*key.pub"):
        try:
            with open(key_file) as f:
                line = f.read().strip()
                parts = line.split()
                if len(parts) >= 2:
                    keys.append({
                        "file": key_file.name,
                        "key_type": parts[0],
                        "fingerprint": parts[1][:32] + "...",
                    })
        except Exception:
            pass
    return keys


def get_sshd_config_summary() -> dict:
    """Get key sshd config settings (security-relevant)."""
    config = {}
    sshd_config = Path("/etc/ssh/sshd_config")
    if not sshd_config.exists():
        return config
    try:
        with open(sshd_config) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if " " in line:
                    key, val = line.split(None, 1)
                    config[key] = val
    except Exception:
        pass
    # Check .d drop-ins
    dropin_dir = Path("/etc/ssh/sshd_config.d")
    if dropin_dir.exists():
        for f in dropin_dir.glob("*.conf"):
            try:
                with open(f) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if " " in line:
                            key, val = line.split(None, 1)
                            config[key] = val
            except Exception:
                pass
    return config
