"""Software updates module — apt/dnf package management."""

from __future__ import annotations

import subprocess
import shutil


def detect_package_manager() -> str | None:
    """Detect available package manager."""
    for pm in ("apt", "dnf", "yum", "pacman", "zypper"):
        if shutil.which(pm):
            return pm
    return None


def get_updates() -> dict:
    """List available updates."""
    pm = detect_package_manager()
    if not pm:
        return {"available": False, "updates": []}

    updates = []
    if pm == "apt":
        try:
            # Check if apt update has been run
            result = subprocess.run(
                ["apt", "list", "--upgradable"],
                capture_output=True, text=True, timeout=15,
            )
            for line in result.stdout.strip().split("\n"):
                if "/" in line and "upgradable" in line:
                    parts = line.split("/")
                    name = parts[0]
                    updates.append({"name": name, "raw": line})
        except Exception:
            pass
    elif pm in ("dnf", "yum"):
        try:
            result = subprocess.run(
                [pm, "check-update"],
                capture_output=True, text=True, timeout=15,
            )
            for line in result.stdout.strip().split("\n"):
                if line.strip() and not line.startswith("Last metadata"):
                    parts = line.split()
                    if len(parts) >= 2:
                        updates.append({"name": parts[0], "version": parts[1]})
        except Exception:
            pass

    return {
        "available": True,
        "package_manager": pm,
        "updates": updates,
        "count": len(updates),
    }


def apply_updates() -> dict:
    """Apply all available updates."""
    pm = detect_package_manager()
    if not pm:
        return {"error": "No package manager detected"}

    try:
        if pm == "apt":
            result = subprocess.run(
                ["apt", "update", "-y"],
                capture_output=True, text=True, timeout=120,
            )
            result2 = subprocess.run(
                ["apt", "upgrade", "-y"],
                capture_output=True, text=True, timeout=300,
            )
            return {
                "success": result2.returncode == 0,
                "stdout": result2.stdout[-2000:],
                "stderr": result2.stderr[-2000:],
            }
        elif pm in ("dnf", "yum"):
            result = subprocess.run(
                [pm, "upgrade", "-y"],
                capture_output=True, text=True, timeout=300,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
    except Exception as exc:
        return {"error": str(exc)}


def install_package(packages: list[str]) -> dict:
    """Install one or more packages."""
    pm = detect_package_manager()
    if not pm:
        return {"error": "No package manager detected"}

    # Validate package names (alphanumeric + hyphen + dot + plus only)
    import re
    for pkg in packages:
        if not re.match(r"^[a-zA-Z0-9.+\-]+$", pkg):
            return {"error": f"Invalid package name: {pkg}"}

    try:
        if pm == "apt":
            cmd = ["apt-get", "install", "-y", "-q"] + packages
        elif pm in ("dnf", "yum"):
            cmd = [pm, "install", "-y", "-q"] + packages
        elif pm == "pacman":
            cmd = ["pacman", "-S", "--noconfirm"] + packages
        elif pm == "zypper":
            cmd = ["zypper", "--non-interactive", "install"] + packages
        else:
            return {"error": f"Install not supported for {pm}"}

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-1000:],
            "command": " ".join(cmd),
        }
    except Exception as exc:
        return {"error": str(exc)}


def remove_package(packages: list[str], purge: bool = False) -> dict:
    """Remove one or more packages."""
    pm = detect_package_manager()
    if not pm:
        return {"error": "No package manager detected"}

    import re
    for pkg in packages:
        if not re.match(r"^[a-zA-Z0-9.+\-]+$", pkg):
            return {"error": f"Invalid package name: {pkg}"}

    try:
        if pm == "apt":
            cmd = ["apt-get"] + (["purge"] if purge else ["remove"]) + ["-y", "-q"] + packages
        elif pm in ("dnf", "yum"):
            cmd = [pm, "remove", "-y", "-q"] + packages
        elif pm == "pacman":
            cmd = ["pacman", "-R", "--noconfirm"] + packages
        elif pm == "zypper":
            cmd = ["zypper", "--non-interactive", "remove"] + packages
        else:
            return {"error": f"Remove not supported for {pm}"}

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-1000:],
            "command": " ".join(cmd),
        }
    except Exception as exc:
        return {"error": str(exc)}


def search_package(query: str) -> dict:
    """Search for packages."""
    pm = detect_package_manager()
    if not pm:
        return {"error": "No package manager detected"}

    import re
    if not re.match(r"^[a-zA-Z0-9.+\- ]+$", query):
        return {"error": "Invalid search query"}

    results = []
    try:
        if pm == "apt":
            r = subprocess.run(["apt-cache", "search", query], capture_output=True, text=True, timeout=15)
            for line in r.stdout.strip().split("\n")[:30]:
                if " - " in line:
                    parts = line.split(" - ", 1)
                    results.append({"name": parts[0], "description": parts[1]})
        elif pm in ("dnf", "yum"):
            r = subprocess.run([pm, "search", query], capture_output=True, text=True, timeout=15)
            for line in r.stdout.strip().split("\n")[:30]:
                if line.strip() and not line.startswith("=") and not line.startswith("Last"):
                    results.append({"name": line.strip(), "description": ""})
    except Exception as exc:
        return {"error": str(exc)}

    return {"results": results, "count": len(results)}
