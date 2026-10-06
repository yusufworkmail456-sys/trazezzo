"""Chatbot agent module — full operation mode.

When mode='agent', the LLM can propose commands to execute on the server.
The flow:
1. User asks a question (e.g. "server lemot, kenapa?")
2. LLM receives context + system prompt with available tools
3. LLM responds with text + optional JSON action blocks
4. Server executes actions and feeds results back to LLM
5. LLM gives final answer

Action format (LLM outputs inline JSON blocks):
```action
{"tool": "exec", "command": "systemctl status nginx"}
```

Available tools:
- exec: run a shell command (with safety checks)
- read_file: read a file
- write_file: write content to a file
- list_dir: list directory contents
- git_push: git add+commit+push
- service_action: start/stop/restart a service
"""

from __future__ import annotations

import json
import re
import subprocess
import os
from pathlib import Path

from trazezzo.config import LLM_BASE_URL, LLM_MODEL, LLM_ENV_KEY_NAME, LLM_ENV_PATH
from trazezzo.llm import chat_completion, chat_completion_stream, _get_api_key
from trazezzo.config import DATA_DIR

import httpx
import logging
from typing import AsyncGenerator

log = logging.getLogger("trazezzo.agent")

# ── Safety: blocked commands ─────────────────────────────────────────
BLOCKED_PATTERNS = [
    r"\brm\s+-rf\s+/(?!tmp)",
    r"\bmkfs\b",
    r"\bdd\s+if=.*/dev/sd",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\binit\s+0\b",
    r"\b>\s*/dev/sda",
    r"\bchmod\s+-R\s+777\s+/",
]

BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS]

# Max execution time for a single command
MAX_EXEC_TIMEOUT = 60
MAX_OUTPUT = 4000  # chars per command output

# ── Cancel support ───────────────────────────────────────────────────
import threading

_cancel_flag = threading.Event()
_running_procs: list[subprocess.Popen] = []
_proc_lock = threading.Lock()


def cancel_agent():
    """Cancel the running agent loop and kill any subprocess."""
    _cancel_flag.set()
    with _proc_lock:
        for proc in _running_procs:
            try:
                proc.kill()
            except Exception:
                pass
        _running_procs.clear()


def _clear_cancel():
    _cancel_flag.clear()


def is_command_safe(cmd: str) -> tuple[bool, str]:
    """Check if a command is safe to execute."""
    for pattern in BLOCKED_RE:
        if pattern.search(cmd):
            return False, f"Blocked: command matches dangerous pattern"
    return True, ""


def execute_command(cmd: str, timeout: int = MAX_EXEC_TIMEOUT) -> dict:
    """Execute a shell command and return output."""
    safe, reason = is_command_safe(cmd)
    if not safe:
        return {"success": False, "error": reason, "command": cmd}

    try:
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        with _proc_lock:
            _running_procs.append(proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {"success": False, "error": f"Command timed out ({timeout}s)", "command": cmd}
        finally:
            with _proc_lock:
                if proc in _running_procs:
                    _running_procs.remove(proc)

        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout[-MAX_OUTPUT:] if stdout else "",
            "stderr": stderr[-MAX_OUTPUT:] if stderr else "",
            "command": cmd,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "command": cmd}


# ── Tool execution ───────────────────────────────────────────────────

def execute_tool(action: dict) -> dict:
    """Execute a single tool action."""
    tool = action.get("tool", "")
    if tool == "exec":
        cmd = action.get("command", "")
        return execute_command(cmd)
    elif tool == "read_file":
        path = action.get("path", "")
        try:
            p = Path(path)
            if not p.exists():
                return {"success": False, "error": "File not found"}
            content = p.read_text(errors="replace")[:MAX_OUTPUT]
            return {"success": True, "content": content, "path": path}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
    elif tool == "write_file":
        path = action.get("path", "")
        content = action.get("content", "")
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return {"success": True, "path": str(p.resolve()), "size": len(content)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
    elif tool == "list_dir":
        path = action.get("path", "/")
        try:
            p = Path(path)
            entries = []
            for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
                entries.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })
            return {"success": True, "entries": entries, "path": str(p.resolve())}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
    elif tool == "event_query":
        from trazezzo.agent.analytics import run_analytics_query
        return run_analytics_query(action)
    elif tool == "git_push":
        from trazezzo.modules.github import create_commit_and_push
        repo_path = action.get("repo_path", "")
        message = action.get("message", "Update from Trazezzo")
        files = action.get("files", None)
        branch = action.get("branch", None)
        return create_commit_and_push(repo_path, files, message, branch)
    elif tool == "service_action":
        unit = action.get("unit", "")
        action_type = action.get("action", "")
        if action_type not in ("start", "stop", "restart", "status", "enable", "disable"):
            return {"success": False, "error": f"Invalid service action: {action_type}"}
        return execute_command(f"systemctl {action_type} {unit}")
    elif tool == "read_repo_file":
        from trazezzo.modules.github import read_repo_file
        owner = action.get("owner", "")
        repo = action.get("repo", "")
        path = action.get("path", "")
        branch = action.get("branch", "")
        return read_repo_file(owner, repo, path, branch)
    elif tool == "read_repo_tree":
        from trazezzo.modules.github import read_repo_tree
        owner = action.get("owner", "")
        repo = action.get("repo", "")
        branch = action.get("branch", "")
        return read_repo_tree(owner, repo, branch)
    elif tool == "push_repo_file":
        from trazezzo.modules.github import push_repo_file
        owner = action.get("owner", "")
        repo = action.get("repo", "")
        path = action.get("path", "")
        content = action.get("content", "")
        message = action.get("message", "Update from Trazezzo")
        branch = action.get("branch", "main")
        sha = action.get("sha", "")
        return push_repo_file(owner, repo, path, content, message, branch, sha)
    elif tool == "create_pr":
        from trazezzo.modules.github import create_pull_request
        owner = action.get("owner", "")
        repo = action.get("repo", "")
        title = action.get("title", "PR from Trazezzo")
        head = action.get("head", "")
        base = action.get("base", "main")
        body = action.get("body", "")
        return create_pull_request(owner, repo, title, head, base, body)
    elif tool == "domain_inventory":
        from trazezzo.modules.domain_inventory import get_domain_inventory
        return {"success": True, "domains": get_domain_inventory()}
    else:
        return {"success": False, "error": f"Unknown tool: {tool}"}


# ── Parse LLM response for action blocks ─────────────────────────────

ACTION_PATTERN = re.compile(r'```action\s*\n(.*?)\n```', re.DOTALL)


def parse_actions(text: str) -> list[dict]:
    """Extract action blocks from LLM response."""
    actions = []
    for match in ACTION_PATTERN.finditer(text):
        try:
            action = json.loads(match.group(1).strip())
            actions.append(action)
        except json.JSONDecodeError:
            continue
    return actions


def strip_actions(text: str) -> str:
    """Remove action blocks from text, leaving only prose."""
    return ACTION_PATTERN.sub('', text).strip()


# ── Agent system prompt ──────────────────────────────────────────────

AGENT_SYSTEM_PROMPT = """You are Trazezzo Agent, an AI server operations assistant with full capability to analyze and repair this server.

## Mode: Agent (Full Operation)

You CAN run commands on the server AND read/modify code in the connected GitHub repo. Use the tools below to diagnose and fix problems.

### Available Tools

Emit actions in ```action ... ``` blocks (JSON). The server will execute them and return the results to you.

**System Tools:**

1. **exec** — run a shell command
```action
{"tool": "exec", "command": "ps aux --sort=-%cpu | head -20"}
```

2. **read_file** — read a file on the server
```action
{"tool": "read_file", "path": "/etc/nginx/nginx.conf"}
```

3. **write_file** — write/edit a file on the server
```action
{"tool": "write_file", "path": "/path/to/file", "content": "file content here"}
```

4. **event_query** — query the event store (temporal analytics)
```action
{"tool": "event_query", "query": "summary|errors|timeline|top_sources|by_type", "minutes": 60}
```

5. **service_action** — manage a systemd service
```action
{"tool": "service_action", "action": "restart|start|stop|status", "unit": "nginx.service"}
```

6. **domain_inventory** — list domains/apps served by this server
```action
{"tool": "domain_inventory"}
```

**GitHub Tools:**

7. **read_repo_file** — read a file from the connected repo
```action
{"tool": "read_repo_file", "owner": "username", "repo": "repo-name", "path": "src/app.py", "branch": "main"}
```

8. **read_repo_tree** — list the repo file tree
```action
{"tool": "read_repo_tree", "owner": "username", "repo": "repo-name", "branch": "main"}
```

9. **push_repo_file** — push a file change to the repo (requires user approval)
```action
{"tool": "push_repo_file", "owner": "username", "repo": "repo-name", "path": "src/app.py", "content": "fixed code here", "message": "fix: timeout issue", "branch": "main"}
```

10. **create_pr** — create a pull request
```action
{"tool": "create_pr", "owner": "username", "repo": "repo-name", "title": "Fix timeout", "head": "fix-timeout", "base": "main", "body": "Fixed timeout issue in app.py"}
```

11. **git_push** — git add + commit + push in a local repo on the server
```action
{"tool": "git_push", "repo_path": "/root/project", "message": "fix: update config", "branch": "main"}
```

### Workflow

1. Diagnose: query event history (`event_query`) for temporal context, read logs (`exec`), check services (`service_action`), check domain inventory (`domain_inventory`)
2. If the issue is code-related: read the repo (`read_repo_tree` → `read_repo_file`)
3. Cross-reference: error logs → repo code → find root cause
4. Propose a fix: edit code (`push_repo_file`) with user approval
5. Deploy: restart the service (`service_action`) with user approval
6. Verify: check logs again, confirm the error is gone

### Rules

- Run commands one at a time, wait for the result, then decide the next step
- For performance issues: check CPU, memory, disk, load
- For event history: use `event_query` first (not raw log grep) — faster and pre-aggregated
- For app bugs: read logs → read repo → analyze → fix
- For deploys: push code → restart service → verify
- NEVER run dangerous commands (rm -rf /, mkfs, dd, shutdown, reboot)
- ALWAYS explain what you are doing and why
- After executing, summarize what happened
- Reply in the SAME LANGUAGE the user writes in (default: English), concise and actionable

### Output Format

- Short, structured, actionable
- Use markdown headings/bullets for readability
- State root cause before proposing fixes"""



async def run_agent(
    user_message: str,
    context_parts: list[str],
    chat_history: list[dict] | None = None,
    image_data: str | None = None,
    scope: str = "system",
) -> AsyncGenerator[str, None]:
    """Run agent loop: LLM proposes actions → execute → feed back → repeat.

    Yields JSON event strings: {"type": "text", "chunk": "..."} or
    {"type": "action", "action": {...}} or {"type": "result", "result": {...}}
    or {"type": "done", "summary": "..."}

    scope: "system" for system-wide queries, "repo:owner/repo" for repo-scoped.
    """
    from typing import AsyncGenerator

    context_str = "\n".join(context_parts)

    # Build system prompt — include repo context if scope is repo
    system_prompt = AGENT_SYSTEM_PROMPT
    if scope.startswith("repo:"):
        repo_full = scope[5:]
        system_prompt += f"\n\n## Active Repo Scope\nUser is asking about repo: {repo_full}\nUse read_repo_file and read_repo_tree tools with owner/repo from this scope to read code.\nIf you find a bug in the repo, use push_repo_file to fix it (with user approval).\nCross-reference system logs with repo code to find root causes."

    # Build initial messages
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
    ]

    # Add chat history
    if chat_history:
        messages.extend(chat_history[-10:])

    # User message with context
    if image_data:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"Context:\n{context_str}\n\nQuestion: {user_message}"},
                {"type": "image_url", "image_url": {"url": image_data}},
            ],
        })
    else:
        messages.append({
            "role": "user",
            "content": f"Context:\n{context_str}\n\nQuestion: {user_message}",
        })

    max_rounds = 8  # max action-execution rounds
    _clear_cancel()

    for round_num in range(max_rounds):
        # Check cancel
        if _cancel_flag.is_set():
            yield json.dumps({"type": "cancelled", "message": "Agent cancelled by user."})
            return

        # Get LLM response (non-streaming for agent mode — we need full text to parse actions)
        try:
            response = await chat_completion(messages, temperature=0.4, max_tokens=2000)
        except Exception as exc:
            yield json.dumps({"type": "error", "error": str(exc)})
            return

        # Parse actions from response
        actions = parse_actions(response)
        prose = strip_actions(response)

        # Stream the prose text
        if prose:
            chunk_size = 80
            for i in range(0, len(prose), chunk_size):
                yield json.dumps({"type": "text", "chunk": prose[i:i+chunk_size]})

        # If no actions, we're done
        if not actions:
            yield json.dumps({"type": "done", "summary": prose[-500:] if prose else ""})
            return

        # Add assistant response to messages
        messages.append({"role": "assistant", "content": response})

        # Execute each action and collect results
        observation_parts = []
        for action in actions:
            if _cancel_flag.is_set():
                yield json.dumps({"type": "cancelled", "message": "Agent cancelled by user."})
                return

            yield json.dumps({"type": "action", "action": action})

            result = execute_tool(action)
            yield json.dumps({"type": "result", "result": result})

            # Build observation for LLM
            tool_name = action.get("tool", "unknown")
            obs_parts = [f"Tool: {tool_name}"]
            if "command" in action:
                obs_parts.append(f"Command: {action['command']}")
            if result.get("success"):
                obs_parts.append("Result: SUCCESS")
                if result.get("stdout"):
                    obs_parts.append(f"stdout:\n{result['stdout'][-2000:]}")
                if result.get("content"):
                    obs_parts.append(f"content:\n{result['content'][-2000:]}")
            else:
                obs_parts.append("Result: FAILED")
                if result.get("error"):
                    obs_parts.append(f"error: {result['error']}")
                if result.get("stderr"):
                    obs_parts.append(f"stderr:\n{result['stderr'][-1000:]}")
            observation_parts.append("\n".join(obs_parts))

            # Publish to exec WebSocket
            try:
                from trazezzo.dashboard.exec_ws import publish_execution
                publish_execution({
                    "command": action.get("command", str(action)),
                    "source": "chatbot:agent",
                    "success": result.get("success", False),
                    "output": result.get("stdout", result.get("content", ""))[:500],
                    "stderr": result.get("stderr", result.get("error", "")),
                    "message": f"Agent: {tool_name}",
                })
            except Exception:
                pass

        # Feed results back to LLM for next round
        observation = "\n\n---\n\n".join(observation_parts)
        messages.append({
            "role": "user",
            "content": f"Execution results:\n\n{observation}\n\nContinue the analysis or give your conclusion.",
        })

    # Max rounds reached
    yield json.dumps({"type": "done", "summary": "Max execution rounds reached."})

