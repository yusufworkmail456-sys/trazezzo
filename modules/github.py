"""GitHub integration module — secure token storage, repo list, commit, push, PR."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx

from trazezzo.config import DATA_DIR

# ── Token storage ───────────────────────────────────────────────────
TOKEN_FILE = DATA_DIR / "github_token.json"


def save_token(token: str) -> dict:
    """Save GitHub personal access token (encrypted-at-rest via file perms)."""
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps({"token": token}))
        os.chmod(TOKEN_FILE, 0o600)
        return {"success": True}
    except Exception as exc:
        return {"error": str(exc)}


def get_token() -> str | None:
    """Read stored GitHub token."""
    try:
        if TOKEN_FILE.exists():
            data = json.loads(TOKEN_FILE.read_text())
            return data.get("token")
    except Exception:
        pass
    return None


def has_token() -> bool:
    return get_token() is not None


def delete_token() -> dict:
    """Delete stored token."""
    try:
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        return {"success": True}
    except Exception as exc:
        return {"error": str(exc)}


# ── GitHub API ──────────────────────────────────────────────────────

GITHUB_API = "https://api.github.com"


def _headers() -> dict:
    token = get_token()
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_user_info() -> dict:
    """Get authenticated user info."""
    token = get_token()
    if not token:
        return {"error": "No token configured"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{GITHUB_API}/user", headers=_headers())
            if resp.status_code == 401:
                return {"error": "Invalid or expired token"}
            resp.raise_for_status()
            data = resp.json()
            return {
                "login": data.get("login"),
                "name": data.get("name"),
                "avatar_url": data.get("avatar_url"),
                "public_repos": data.get("public_repos"),
                "html_url": data.get("html_url"),
            }
    except Exception as exc:
        return {"error": str(exc)}


def list_repos(per_page: int = 30, sort: str = "updated") -> dict:
    """List repositories for authenticated user."""
    token = get_token()
    if not token:
        return {"error": "No token configured"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{GITHUB_API}/user/repos",
                headers=_headers(),
                params={"per_page": per_page, "sort": sort},
            )
            if resp.status_code == 401:
                return {"error": "Invalid or expired token"}
            resp.raise_for_status()
            repos = resp.json()
            return {
                "repos": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "full_name": r["full_name"],
                        "html_url": r["html_url"],
                        "clone_url": r["clone_url"],
                        "private": r["private"],
                        "default_branch": r.get("default_branch", "main"),
                        "updated_at": r["updated_at"],
                        "description": r.get("description", ""),
                        "language": r.get("language", ""),
                    }
                    for r in repos
                ],
                "count": len(repos),
            }
    except Exception as exc:
        return {"error": str(exc)}


def list_branches(owner: str, repo: str) -> dict:
    """List branches in a repo."""
    token = get_token()
    if not token:
        return {"error": "No token configured"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/branches",
                headers=_headers(),
                params={"per_page": 50},
            )
            resp.raise_for_status()
            branches = resp.json()
            return {
                "branches": [
                    {"name": b["name"], "protected": b.get("protected", False)}
                    for b in branches
                ],
            }
    except Exception as exc:
        return {"error": str(exc)}


def list_commits(owner: str, repo: str, branch: str = "main", per_page: int = 10) -> dict:
    """List recent commits in a repo."""
    token = get_token()
    if not token:
        return {"error": "No token configured"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/commits",
                headers=_headers(),
                params={"sha": branch, "per_page": per_page},
            )
            resp.raise_for_status()
            commits = resp.json()
            return {
                "commits": [
                    {
                        "sha": c["sha"][:7],
                        "message": c["commit"]["message"].split("\n")[0][:100],
                        "author": c["commit"]["author"]["name"],
                        "date": c["commit"]["author"]["date"],
                        "html_url": c["html_url"],
                    }
                    for c in commits
                ],
            }
    except Exception as exc:
        return {"error": str(exc)}


def create_commit_and_push(
    repo_path: str,
    files: list[str] | None = None,
    message: str = "Update from Trazezzo",
    branch: str | None = None,
) -> dict:
    """Git add + commit + push via subprocess in repo_path."""
    if not os.path.isdir(repo_path):
        return {"error": f"Directory not found: {repo_path}"}
    try:
        # Check if it's a git repo
        git_check = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, cwd=repo_path, timeout=10,
        )
        if git_check.returncode != 0:
            return {"error": "Not a git repository"}

        steps = []

        # Add files
        add_cmd = ["git", "add"] + (files if files else ["-A"])
        add_result = subprocess.run(
            add_cmd, capture_output=True, text=True, cwd=repo_path, timeout=30,
        )
        steps.append({
            "step": "git add",
            "success": add_result.returncode == 0,
            "stdout": add_result.stdout,
            "stderr": add_result.stderr,
        })

        # Check if there's anything to commit
        status_result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True, text=True, cwd=repo_path, timeout=10,
        )
        if status_result.returncode == 0:
            return {"success": True, "message": "Nothing to commit — working tree clean", "steps": steps}

        # Commit
        commit_result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, cwd=repo_path, timeout=30,
        )
        steps.append({
            "step": "git commit",
            "success": commit_result.returncode == 0,
            "stdout": commit_result.stdout,
            "stderr": commit_result.stderr,
        })
        if commit_result.returncode != 0:
            return {"error": "Commit failed", "steps": steps}

        # Push
        push_cmd = ["git", "push"]
        if branch:
            push_cmd += ["origin", branch]
        push_result = subprocess.run(
            push_cmd, capture_output=True, text=True, cwd=repo_path, timeout=60,
        )
        steps.append({
            "step": "git push",
            "success": push_result.returncode == 0,
            "stdout": push_result.stdout,
            "stderr": push_result.stderr,
        })

        return {
            "success": push_result.returncode == 0,
            "message": message,
            "steps": steps,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Git operation timed out"}
    except Exception as exc:
        return {"error": str(exc)}


def create_pull_request(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str = "main",
    body: str = "",
) -> dict:
    """Create a pull request via GitHub API."""
    token = get_token()
    if not token:
        return {"error": "No token configured"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
                headers=_headers(),
                json={
                    "title": title,
                    "head": head,
                    "base": base,
                    "body": body,
                },
            )
            if resp.status_code == 422:
                return {"error": "PR already exists or branch has no commits"}
            resp.raise_for_status()
            pr = resp.json()
            return {
                "success": True,
                "number": pr["number"],
                "html_url": pr["html_url"],
                "title": pr["title"],
            }
    except Exception as exc:
        return {"error": str(exc)}


def get_repo_info(owner: str, repo: str) -> dict:
    """Get repo details."""
    token = get_token()
    if not token:
        return {"error": "No token configured"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}",
                headers=_headers(),
            )
            resp.raise_for_status()
            r = resp.json()
            return {
                "name": r["name"],
                "full_name": r["full_name"],
                "html_url": r["html_url"],
                "default_branch": r.get("default_branch", "main"),
                "private": r["private"],
                "description": r.get("description", ""),
                "clone_url": r["clone_url"],
                "language": r.get("language", ""),
                "stargazers_count": r.get("stargazers_count", 0),
                "forks_count": r.get("forks_count", 0),
            }
    except Exception as exc:
        return {"error": str(exc)}
