"""FastAPI dashboard app — Trazezzo control plane UI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trazezzo.config import DASHBOARD_HOST, DASHBOARD_PORT, PROACTIVE_MODE, AUTH_USERNAME, AUTH_PASSWORD, SESSION_SECRET, APP_VERSION
from trazezzo.agent.store.sqlite_warm import get_store
from trazezzo.dashboard.ws import websocket_endpoint

# ── Startup hooks: live journal log streamer (registered after app creation) ──
from trazezzo.dashboard.logs_ws import journal_log_streamer

log = logging.getLogger("trazezzo.dashboard")

# ── Paths ─────────────────────────────────────────────────────────────
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# ── App ────────────────────────────────────────────────────────────────
app = FastAPI(title="Trazezzo", version=APP_VERSION)
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.auto_reload = True
templates.env.globals["app_version"] = APP_VERSION


@app.on_event("startup")
async def _start_log_streamer():
    asyncio.create_task(journal_log_streamer())

# ── Session middleware ──────────────────────────────────────────────
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import secrets
_session_secret = SESSION_SECRET if SESSION_SECRET != "trazezzo-session-secret-change-me" else secrets.token_hex(32)


def check_auth(request: Request) -> bool:
    """Check if user is authenticated via session."""
    return request.session.get("authenticated") is True


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect to login if not authenticated."""
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Allow: login page, login API, static files, health check
        public_paths = ["/login", "/api/auth/login", "/api/auth/logout", "/static"]
        if any(path.startswith(p) for p in public_paths) or path == "/":
            return await call_next(request)

        if not check_auth(request):
            if path.startswith("/api/") or path.startswith("/ws/"):
                return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
            return RedirectResponse(url="/trazezzo/login", status_code=302)

        return await call_next(request)


# Order matters: SessionMiddleware FIRST (outer), AuthMiddleware SECOND (inner)
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=_session_secret)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Auth ──────────────────────────────────────────────────────────────

def check_auth(request: Request) -> bool:
    """Check if user is authenticated via session."""
    return request.session.get("authenticated") is True


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    if check_auth(request):
        return RedirectResponse(url="/trazezzo/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"request": request})


@app.post("/api/auth/login")
async def api_login(request: Request):
    """Login endpoint — validate credentials, set session."""
    import hmac
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")

    user_ok = hmac.compare_digest(username, AUTH_USERNAME)
    pass_ok = hmac.compare_digest(password, AUTH_PASSWORD)

    if not (user_ok and pass_ok):
        return JSONResponse(status_code=401, content={"detail": "Invalid username or password"})

    request.session["authenticated"] = True
    request.session["username"] = username
    return {"success": True}


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    """Logout endpoint — clear session."""
    request.session.clear()
    return {"success": True}


# ── Pages ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main dashboard — overview page."""
    from trazezzo.modules.system import get_system_overview, get_failed_services

    overview = get_system_overview()
    failed = get_failed_services()
    store = get_store()
    counts = {
        "user": store.count("user"),
        "agent": store.count("agent"),
        "system": store.count("system"),
        "total": store.count(),
    }
    import trazezzo.config as _cfg
    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "overview": overview,
        "failed_services": failed,
        "counts": counts,
        "proactive_mode": _cfg.PROACTIVE_MODE,
    })


@app.get("/services", response_class=HTMLResponse)
async def services_page(request: Request):
    """Services management page."""
    from trazezzo.modules.system import get_services, get_failed_services

    return templates.TemplateResponse(request, "services.html", {
        "request": request,
        "services": get_services(),
        "failed": get_failed_services(),
    })


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """Logs viewer page."""
    from trazezzo.modules.logs import get_journal_logs

    logs = get_journal_logs(lines=200)
    return templates.TemplateResponse(request, "logs.html", {
        "request": request,
        "logs": logs,
    })


@app.get("/networking", response_class=HTMLResponse)
async def networking_page(request: Request):
    """Networking page."""
    from trazezzo.modules.networking import get_interfaces, get_routes, get_firewall, get_bonds

    return templates.TemplateResponse(request, "networking.html", {
        "request": request,
        "interfaces": get_interfaces(),
        "routes": get_routes(),
        "firewall": get_firewall(),
        "bonds": get_bonds(),
    })


@app.get("/storage", response_class=HTMLResponse)
async def storage_page(request: Request):
    """Storage page."""
    from trazezzo.modules.storage import get_storage_overview

    return templates.TemplateResponse(request, "storage.html", {
        "request": request,
        "storage": get_storage_overview(),
    })


@app.get("/containers", response_class=HTMLResponse)
async def containers_page(request: Request):
    """Containers + Compose page."""
    from trazezzo.modules.containers import get_containers
    from trazezzo.modules.compose import get_compose_projects

    return templates.TemplateResponse(request, "containers.html", {
        "request": request,
        "active": "containers",
        "containers": get_containers(),
        "compose_projects": get_compose_projects(),
    })


@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    """Access page (users, sessions, SSH keys)."""
    from trazezzo.modules.accounts import get_login_users, get_active_sessions, get_groups
    from trazezzo.modules.ssh_keys import get_authorized_keys, get_host_keys, get_sshd_config_summary

    return templates.TemplateResponse(request, "accounts.html", {
        "request": request,
        "active": "accounts",
        "users": get_login_users(),
        "sessions": get_active_sessions(),
        "groups": get_groups(),
        "ssh_keys": get_authorized_keys(),
        "host_keys": get_host_keys(),
        "sshd_config": get_sshd_config_summary(),
    })


@app.get("/events", response_class=HTMLResponse)
async def events_page(request: Request):
    """Events page -- live feed + timeline + graph (unified)."""
    from trazezzo.viz.timeline import build_activity_feed

    feed = build_activity_feed(limit=200)
    store = get_store()
    feed["counts"] = {
        "user": store.count("user"),
        "agent": store.count("agent"),
        "system": store.count("system"),
    }
    return templates.TemplateResponse(request, "events.html", {
        "request": request,
        "active": "events",
        "feed": feed,
    })


@app.get("/chatbot", response_class=HTMLResponse)
async def chatbot_page(request: Request):
    """AI chatbot page."""
    from trazezzo.modules.system import get_system_overview

    overview = get_system_overview()
    return templates.TemplateResponse(request, "chatbot.html", {
        "request": request,
        "overview": overview,
    })


@app.get("/terminal", response_class=HTMLResponse)
async def terminal_page(request: Request):
    """Web terminal page."""
    return templates.TemplateResponse(request, "terminal.html", {
        "request": request,
    })


@app.get("/updates", response_class=HTMLResponse)
async def updates_page(request: Request):
    """Software updates page."""
    from trazezzo.modules.updates import get_updates

    return templates.TemplateResponse(request, "updates.html", {
        "request": request,
        "updates": get_updates(),
    })


@app.get("/security", response_class=HTMLResponse)
async def security_page(request: Request):
    """Security page (SELinux, SSH keys, sshd config)."""
    from trazezzo.modules.selinux import get_selinux_status
    from trazezzo.modules.ssh_keys import get_authorized_keys, get_host_keys, get_sshd_config_summary
    from trazezzo.modules.credentials import scan_credentials

    return templates.TemplateResponse(request, "security.html", {
        "request": request,
        "active": "security",
        "selinux": get_selinux_status(),
        "ssh_keys": get_authorized_keys(),
        "host_keys": get_host_keys(),
        "sshd_config": get_sshd_config_summary(),
    })




# ── API endpoints ─────────────────────────────────────────────────────

@app.get("/api/overview")
async def api_overview():
    from trazezzo.modules.system import get_system_overview
    return get_system_overview()


@app.get("/api/services")
async def api_services():
    from trazezzo.modules.system import get_services
    return {"services": get_services()}


@app.post("/api/services/{unit}/{action}")
async def api_service_action(unit: str, action: str):
    from trazezzo.modules.system import service_action
    result = service_action(unit, action)
    # Record agent action
    store = get_store()
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    store.add(ServerEvent(
        type=EventType.AGENT_ACTION,
        source="dashboard",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:web"),
        message=f"Service action: {action} {unit}",
        payload=result,
        severity="warning" if action in ("stop", "restart") else "info",
    ))
    # Publish to exec WebSocket
    try:
        publish_execution({
            "command": f"systemctl {action} {unit}",
            "source": "dashboard:service",
            "success": result.get("success", False),
            "output": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "message": f"Service action: {action} {unit}",
        })
    except Exception:
        pass
    return result


@app.get("/api/activity")
async def api_activity(actor_kind: str | None = None, limit: int = 100):
    from trazezzo.viz.timeline import build_activity_feed
    return build_activity_feed(actor_kind=actor_kind, limit=limit)


@app.get("/api/timeline")
async def api_timeline(minutes: int = 30):
    from trazezzo.viz.timeline import build_timeline
    return build_timeline(window_minutes=minutes)


@app.get("/api/diagram")
async def api_diagram():
    from trazezzo.viz.diagram import build_system_graph
    return build_system_graph()


@app.get("/api/logs")
async def api_logs(unit: str | None = None, lines: int = 100):
    from trazezzo.modules.logs import get_journal_logs
    return {"logs": get_journal_logs(unit=unit, lines=lines)}


@app.get("/api/storage")
async def api_storage():
    from trazezzo.modules.storage import get_storage_overview
    return get_storage_overview()


@app.get("/api/containers")
async def api_containers():
    from trazezzo.modules.containers import get_containers
    return get_containers()


@app.get("/api/networking")
async def api_networking():
    from trazezzo.modules.networking import get_interfaces, get_routes, get_firewall
    return {
        "interfaces": get_interfaces(),
        "routes": get_routes(),
        "firewall": get_firewall(),
    }


@app.get("/api/accounts")
async def api_accounts():
    from trazezzo.modules.accounts import get_login_users, get_active_sessions
    return {
        "users": get_login_users(),
        "sessions": get_active_sessions(),
    }


@app.get("/api/updates")
async def api_updates():
    from trazezzo.modules.updates import get_updates
    return get_updates()


@app.post("/api/updates/apply")
async def api_apply_updates():
    from trazezzo.modules.updates import apply_updates
    return apply_updates()


@app.get("/api/security")
async def api_security():
    from trazezzo.modules.selinux import get_selinux_status
    from trazezzo.modules.ssh_keys import get_authorized_keys, get_host_keys, get_sshd_config_summary
    from trazezzo.modules.credentials import scan_credentials
    return {
        "selinux": get_selinux_status(),
        "ssh_keys": get_authorized_keys(),
        "server_keys": get_host_keys(),
        "sshd_config": get_sshd_config_summary(),
        "credentials": scan_credentials(),
    }


# ── AI Chatbot API ────────────────────────────────────────────────────

@app.post("/api/chat")
async def api_chat(request: Request):
    """AI chatbot endpoint — talk to your server. Supports text + image."""
    body = await request.json()
    user_message = body.get("message", "")
    mode = body.get("mode", "reasoning")  # reasoning | agent
    image_data = body.get("image", None)  # base64 image (data URI)
    scope = body.get("scope", "system")  # system | repo:owner/repo

    from trazezzo.modules.system import get_system_overview
    from trazezzo.llm import chat_completion, chat_completion_stream

    overview = get_system_overview()
    store = get_store()

    # Build context
    context_parts = [
        f"Hostname: {overview['hostname']}",
        f"OS: {overview['os']}",
        f"Kernel: {overview['kernel']}",
        f"Uptime: {overview['uptime']}",
        f"CPU: {overview['cpu']['percent']}% ({overview['cpu']['count']} cores)",
        f"RAM: {overview['memory']['percent']}% ({overview['memory']['used_gb']}/{overview['memory']['total_gb']} GB)",
    ]

    # Recent events
    recent_events = store.query(limit=10)
    if recent_events:
        context_parts.append("\nRecent events:")
        for ev in recent_events:
            context_parts.append(f"  [{ev.ts.strftime('%H:%M:%S')}] [{ev.actor.kind.value}] {ev.type.value}: {ev.message[:80]}")

    # ── Repo scope: add repo context if scope is repo:owner/repo ──────
    repo_context = ""
    if scope.startswith("repo:"):
        repo_full = scope[5:]  # owner/repo
        parts = repo_full.split("/")
        if len(parts) == 2:
            owner, repo_name = parts
            from trazezzo.modules.github import read_repo_tree, has_token
            if has_token():
                tree = read_repo_tree(owner, repo_name)
                if tree.get("success") and tree.get("files"):
                    repo_context = f"\n\nRepo: {repo_full} (branch: {tree.get('branch', 'main')})\nFiles:\n" + "\n".join(tree["files"][:50])
                    context_parts.append(repo_context)
            else:
                context_parts.append(f"\nRepo: {repo_full} — GitHub token not configured. Cannot read repo files.")

    # ── Agent mode: full operation ────────────────────────────────────
    if mode == "agent":
        from trazezzo.agent.chat_agent import run_agent
        # Pass chat history from client
        chat_history = body.get("history", [])

        async def agent_stream():
            try:
                async for event_json in run_agent(user_message, context_parts, chat_history, image_data, scope=scope):
                    yield f"data: {event_json}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        return StreamingResponse(agent_stream(), media_type="text/event-stream")

    # ── Reasoning mode (default) ──────────────────────────────────────

    system_prompt = (
        "You are Trazezzo, an AI server operations assistant.\n\n"
        "## About Trazezzo\n"
        "Trazezzo is an AI-native server operations platform (inspired by Cockpit OS). "
        "Trazezzo runs on this server and has the following components:\n\n"
        "### Architecture\n"
        "- **Agent daemon**: asyncio, 4 capture sources (journald, auditd, psutil, eBPF via bpftrace)\n"
        "- **Warm store**: SQLite WAL, 7-day retention, ring buffer, batched writes\n"
        "- **Dashboard**: FastAPI + Jinja2, port 9122, 3-column layout (nav | content | chat panel)\n"
        "- **LLM**: OpenAI-compatible API (configurable in trazezzo/config.py)\n"
        "- **Attribution**: every event is classified by actor (user/agent/system)\n\n"
        "### Key Features\n"
        "1. **System Overview**: realtime CPU/RAM/disk/load, failed services, proactive mode indicator\n"
        "2. **Services**: list/start/stop/restart systemd services\n"
        "3. **Logs**: journald viewer with filter, realtime stream\n"
        "4. **Events**: black box recorder with Live Feed (WebSocket) + Causal Timeline\n"
        "5. **Causal Timeline**: 'what happened before a crash/down' — vis-timeline, window reconstruction\n"
        "6. **Networking**: interfaces, routes, firewall, bonds\n"
        "7. **Storage**: disk usage, mounts, LVM, block devices, local vs network filesystems\n"
        "8. **Containers**: podman/docker list + action\n"
        "9. **Accounts**: user management, active sessions, groups\n"
        "10. **Updates**: apt/dnf package updates\n"
        "11. **Security**: SELinux, SSH keys, kdump, tuned profiles, credential scanner (plaintext secret detection)\n"
        "12. **Diagnostic**: sosreport generation\n"
        "13. **File Editor**: browse/read/edit config files with auto-backup\n"
        "14. **Event Export**: download captured events as .json.gz for any time range\n"
        "15. **AI Chatbot** (you): talk to the server, streaming responses, image paste support\n"
        "16. **Auto Recommendations**: realtime checks for failed services, CPU/mem/disk/load, brute-force, OOM — with action buttons\n"
        "17. **Execution Panel**: realtime agent command execution display, slides in next to chat\n"
        "18. **Proactive Agent**: 2 modes — Calm (advisory only) / Aggressive (proposes auto-remediation with user approval: restart service, kill OOM, clean disk, renice CPU)\n\n"
        "19. **Agent Chat Mode**: Toggle Q&A ↔ Agent in the chat panel. Agent mode can execute commands, read/write files, git push, manage services directly from chat\n\n"
        "20. **GitHub Integration**: secure token storage, list repos, view commits, push changes, create PRs — page /trazezzo/github\n\n"
        "### Capture Sources\n"
        "- **journald**: journalctl --follow --output=json\n"
        "- **auditd**: tail /var/log/audit/audit.log (execve, login, file events)\n"
        "- **psutil**: CPU/mem/disk/net metrics + anomaly detection (threshold-based)\n"
        "- **eBPF**: bpftrace (execve, connect, kill syscalls)\n\n"
        "### Event Attribution\n"
        "Every event is classified:\n"
        "- `user`: events from login sessions (UID >= 1000 or sudo)\n"
        "- `agent`: events from Trazezzo itself (proactive action, recommendation execute, dashboard action)\n"
        "- `system`: events from kernel/systemd/daemons\n\n"
        "### URL\n"
        "- Local: http://127.0.0.1:9122\n"
        "- Public: served via nginx at /trazezzo/ (see docs)\n"
        "- Nginx reverse proxy: /trazezzo/ → :9122\n"
        "- systemd unit: trazezzo.service\n\n"
        "### How to Use\n"
        "- Toggle chat mode (Q&A / Agent) in the chat panel header — Q&A for questions, Agent for executing commands\n"
        "- Toggle proactive mode (Calm/Aggressive) in the chat panel header\n"
        "- Aggressive mode: agent proposes remediation, user approves via chat before execution\n"
        "- Click a recommendation action button to execute the fix\n"
        "- Execution panel auto-opens when the agent runs a command\n"
        "- Chat history persists via localStorage (🗑 button to clear)\n"
        "- Paste images (Ctrl+V) for visual analysis\n"
        "- GitHub integration at /trazezzo/github — store PAT, list repos, push, create PRs\n"
        "## Answer Instructions\n"
        "Reply in the SAME LANGUAGE the user writes in (default: English). Keep answers short and actionable. "
        "If the user asks about server state, use the provided data context. "
        "If the user requests an action, explain the commands to run. "
        "If the user asks about Trazezzo, use the knowledge above.\n\n"
        "## Output Format\n"
        "Your answer is rendered as markdown in the UI. Use this format:\n"
        "- Structure answers with small headings (###), lists, or tables — no long paragraphs\n"
        "- For data: use short bullet lists or tables, not prose\n"
        "- For commands: use inline code `command` or code blocks\n"
        "- Be concise: straight to the answer, no opening/closing filler\n"
        "- Do not overuse emoji; always include numbers and units\n"
    )

    # Build user message — text context + optional image
    user_content_parts = [f"Context:\n{chr(10).join(context_parts)}\n\nQuestion: {user_message}"]

    messages = [{"role": "system", "content": system_prompt}]

    # If image provided, send as multimodal message (OpenAI vision format)
    if image_data:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"Context:\n{chr(10).join(context_parts)}\n\nQuestion: {user_message}"},
                {"type": "image_url", "image_url": {"url": image_data}},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_content_parts[0]})

    # Vision-capable model fallback when image is provided
    VISION_MODEL = "bp/skylark-vision-250515"
    use_vision = bool(image_data)

    async def stream():
        try:
            # Try vision model first if image provided
            model_to_use = VISION_MODEL if use_vision else None
            async for chunk in chat_completion_stream(
                messages, temperature=0.5, max_tokens=1500,
                model=model_to_use,
            ):
                if "chunk" in chunk or "error" in chunk:
                    pass
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            err_msg = str(exc)
            # If vision model fails, fall back to default model with a note
            if use_vision and ("503" in err_msg or "404" in err_msg or "400" in err_msg):
                # Add image note to messages
                messages.append({
                    "role": "system",
                    "content": "Note: the user attached an image, but the current model does not support vision input. Tell the user the image cannot be viewed and ask them to describe it in text."
                })
                try:
                    async for chunk in chat_completion_stream(
                        messages, temperature=0.5, max_tokens=1500,
                    ):
                        yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as exc2:
                    yield f"data: {json.dumps({'error': str(exc2)})}\n\n"
            else:
                yield f"data: {json.dumps({'error': err_msg})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Terminal WebSocket (kept for backward compat, not used in UI) ─────

@app.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket):
    """Web terminal WebSocket."""
    from trazezzo.modules.terminal import terminal_websocket
    await terminal_websocket(ws)


# ── Events WebSocket ──────────────────────────────────────────────────

@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """Live event stream WebSocket."""
    await websocket_endpoint(ws)


# ── Execution WebSocket — real-time agent execution display ────────────

from trazezzo.dashboard.exec_ws import exec_ws_endpoint, publish_execution

@app.websocket("/ws/exec")
async def ws_exec(ws: WebSocket):
    """Live agent execution stream WebSocket."""
    await exec_ws_endpoint(ws)


# ── SSH Terminal WebSocket ───────────────────────────────────────────

@app.websocket("/ws/ssh/{conn_id}")
async def ws_ssh_terminal(ws: WebSocket, conn_id: str):
    """SSH terminal WebSocket — bridge to remote SSH session."""
    from trazezzo.modules.ssh_terminal import ssh_terminal_ws
    await ssh_terminal_ws(ws, conn_id)


# ── File Editor ────────────────────────────────────────────────────────

@app.get("/editor", response_class=HTMLResponse)
async def editor_page(request: Request):
    """File editor page."""
    return templates.TemplateResponse(request, "editor.html", {
        "request": request,
    })


@app.get("/api/editor/ls")
async def api_editor_ls(path: str = "/etc"):
    from trazezzo.modules.file_editor import list_dir
    return list_dir(path)


@app.get("/api/editor/read")
async def api_editor_read(path: str):
    from trazezzo.modules.file_editor import read_file
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    result = read_file(path)
    # Record agent read
    store = get_store()
    store.add(ServerEvent(
        type=EventType.AGENT_ACTION,
        source="editor",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:editor"),
        message=f"Read file: {path}",
        payload={"path": path},
        severity="info",
    ))
    return result


@app.post("/api/editor/write")
async def api_editor_write(request: Request):
    from trazezzo.modules.file_editor import write_file
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    body = await request.json()
    path = body.get("path", "")
    content = body.get("content", "")
    result = write_file(path, content)
    # Record agent write
    store = get_store()
    store.add(ServerEvent(
        type=EventType.FILE_CONFIG_CHANGE,
        source="editor",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:editor"),
        message=f"Edited file: {path}",
        payload={"path": path, "size": len(content)},
        severity="warning",
    ))
    return result


@app.post("/api/editor/delete")
async def api_editor_delete(request: Request):
    from trazezzo.modules.file_editor import delete_file
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    body = await request.json()
    path = body.get("path", "")
    result = delete_file(path)
    store = get_store()
    store.add(ServerEvent(
        type=EventType.FILE_DELETE,
        source="editor",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:editor"),
        message=f"Deleted file: {path}",
        payload={"path": path},
        severity="warning",
    ))
    return result


@app.post("/api/editor/upload")
async def api_editor_upload(request: Request):
    """Upload file from local laptop to server."""
    from trazezzo.modules.file_editor import upload_file
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind

    ct = request.headers.get("content-type", "")
    if "multipart/form-data" not in ct:
        # Try JSON fallback (base64)
        body = await request.json()
        import base64
        dest_dir = body.get("dir", "/root")
        filename = body.get("filename", "")
        content_b64 = body.get("content", "")
        try:
            content = base64.b64decode(content_b64)
        except Exception:
            return {"error": "Invalid base64 content"}
        result = upload_file(dest_dir, filename, content)
    else:
        form = await request.form()
        upload_file_obj = form.get("file")
        dest_dir = str(form.get("dir", "/root") or "/root")
        rel_path = str(form.get("rel_path", "") or "")
        if not upload_file_obj or not hasattr(upload_file_obj, "read"):
            return {"error": "No file provided"}
        content = await upload_file_obj.read()
        upload_filename = getattr(upload_file_obj, "filename", None) or "unnamed"

        # Handle folder upload: rel_path may contain subdirectories
        if rel_path and "/" in rel_path:
            subdir = os.path.dirname(rel_path)
            if subdir:
                dest_dir = os.path.join(dest_dir, subdir)
                os.makedirs(dest_dir, exist_ok=True)
            filename = os.path.basename(rel_path) or upload_filename
        else:
            filename = upload_filename

        result = upload_file(dest_dir, filename, content)

    store = get_store()
    store.add(ServerEvent(
        type=EventType.FILE_CONFIG_CHANGE,
        source="editor",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:editor"),
        message=f"Uploaded file: {result.get('path', 'failed')}",
        payload=result,
        severity="warning",
    ))
    return result


@app.post("/api/editor/mkdir")
async def api_editor_mkdir(request: Request):
    """Create a new directory."""
    from trazezzo.modules.file_editor import create_dir
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    body = await request.json()
    path = body.get("path", "")
    result = create_dir(path)
    store = get_store()
    store.add(ServerEvent(
        type=EventType.AGENT_ACTION,
        source="editor",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:editor"),
        message=f"Created directory: {path}",
        payload={"path": path, "result": result},
        severity="info",
    ))
    return result


# ── Event Export (replaces legacy Sync & Archive) ─────────────────────

@app.get("/export", response_class=HTMLResponse)
async def export_page(request: Request):
    """Event export page — pick a date range and download a gzipped JSON archive."""
    from datetime import datetime, timezone
    from trazezzo.config import WARM_RETENTION_DAYS
    store = get_store()
    return templates.TemplateResponse(request, "export.html", {
        "request": request,
        "active": "export",
        "total_events": store.count(),
        "retention_days": WARM_RETENTION_DAYS,
        "today": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })


@app.get("/api/export/events")
async def api_export_events(since: str | None = None, until: str | None = None):
    """Export events in a date range as a gzipped JSON download."""
    import gzip
    from datetime import datetime, timedelta, timezone
    from fastapi.responses import Response

    store = get_store()
    now = datetime.now(timezone.utc)

    def _parse(ts: str | None, default: datetime) -> datetime:
        if not ts:
            return default
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return default

    since_dt = _parse(since, now - timedelta(days=1))
    until_dt = _parse(until, now)
    # Cap range at 30 days to keep exports bounded
    if (until_dt - since_dt).days > 30:
        since_dt = until_dt - timedelta(days=30)

    events = store.query(since=since_dt, until=until_dt, limit=200000)
    export_data = {
        "exported_at": now.isoformat(),
        "range": {"since": since_dt.isoformat(), "until": until_dt.isoformat()},
        "event_count": len(events),
        "events": [
            {
                "ts": e.ts.isoformat(),
                "type": e.type.value,
                "source": e.source,
                "actor_kind": e.actor.kind.value,
                "actor_id": e.actor.id,
                "actor_session": e.actor.session,
                "message": e.message,
                "severity": e.severity,
                "payload": e.payload,
            }
            for e in events
        ],
    }

    json_bytes = json.dumps(export_data, default=str).encode("utf-8")
    gz = gzip.compress(json_bytes)
    filename = f"trazezzo-events_{since_dt.strftime('%Y%m%d')}_{until_dt.strftime('%Y%m%d')}.json.gz"

    # Record the export action
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    store.add(ServerEvent(
        type=EventType.AGENT_ACTION,
        source="export",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:export"),
        message=f"Events exported: {len(events)} events ({since_dt.date()} to {until_dt.date()})",
        payload={"since": since_dt.isoformat(), "until": until_dt.isoformat(), "count": len(events)},
        severity="info",
    ))

    return Response(
        content=gz,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Causal Timeline ──────────────────────────────────────────────────

@app.get("/api/causal-timeline")
async def api_causal_timeline(minutes: int = 30, before: str | None = None):
    """Causal timeline: events in window before a point in time."""
    from trazezzo.viz.timeline import build_timeline
    from datetime import datetime, timezone
    before_dt = None
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except Exception:
            pass
    return build_timeline(before=before_dt, window_minutes=minutes)


# ── Proactive Mode Toggle ─────────────────────────────────────────────

@app.post("/api/chat/cancel")
async def api_cancel_agent():
    """Cancel the running agent loop and kill subprocesses."""
    from trazezzo.agent.chat_agent import cancel_agent
    cancel_agent()
    return {"success": True, "message": "Agent cancelled."}


@app.get("/api/proactive/status")
async def api_proactive_status():
    """Get proactive agent status."""
    import trazezzo.config as cfg
    return {
        "mode": cfg.PROACTIVE_MODE,
        "interval": cfg.PROACTIVE_INTERVAL,
    }


@app.post("/api/proactive/mode")
async def api_proactive_mode(request: Request):
    """Toggle proactive mode (calm/aggressive)."""
    import trazezzo.config as cfg
    body = await request.json()
    new_mode = body.get("mode", "calm")
    if new_mode not in ("calm", "aggressive"):
        return {"error": "Invalid mode"}
    cfg.PROACTIVE_MODE = new_mode
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    store = get_store()
    store.add(ServerEvent(
        type=EventType.AGENT_ACTION,
        source="dashboard",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:proactive"),
        message=f"Proactive mode changed to: {new_mode}",
        payload={"mode": new_mode},
        severity="warning" if new_mode == "aggressive" else "info",
    ))
    return {"success": True, "mode": new_mode}


# ── Pending Approvals (aggressive mode) ──────────────────────────────

@app.get("/api/approvals")
async def api_approvals():
    """List all pending approvals."""
    from trazezzo.agent.proactive.approval import list_all
    return {"approvals": list_all()}


@app.post("/api/approvals/{approval_id}/approve")
async def api_approve(approval_id: str):
    """Approve and execute a pending action."""
    from trazezzo.agent.proactive.approval import approve_and_execute
    from trazezzo.agent.store.sqlite_warm import get_store
    store = get_store()
    return approve_and_execute(approval_id, store=store)


@app.post("/api/approvals/{approval_id}/reject")
async def api_reject(approval_id: str):
    """Reject a pending action."""
    from trazezzo.agent.proactive.approval import reject_approval
    from trazezzo.agent.store.sqlite_warm import get_store
    store = get_store()
    return reject_approval(approval_id, store=store)


# ── Auto Recommendations ──────────────────────────────────────────────

@app.get("/api/recommendations")
async def api_recommendations():
    """Generate auto recommendations based on current server state (Cockpit-style)."""
    import psutil
    from trazezzo.modules.system import get_failed_services
    from trazezzo.agent.store.sqlite_warm import get_store
    from trazezzo.agent.schema import EventType
    from datetime import datetime, timezone, timedelta

    recs = []

    # ── Failed services ──────────────────────────────────────────
    failed = get_failed_services()
    for unit in failed[:5]:
        recs.append({
            "severity": "error",
            "message": f"Service '{unit}' failed",
            "action_type": "service.restart",
            "action_args": {"unit": unit},
            "action_label": "Restart",
        })

    # ── CPU ───────────────────────────────────────────────────────
    cpu = psutil.cpu_percent(interval=0.5)
    if cpu > 90:
        recs.append({
            "severity": "error",
            "message": f"CPU usage critical: {cpu}%",
            "action_type": None,
        })
    elif cpu > 75:
        recs.append({
            "severity": "warning",
            "message": f"CPU usage high: {cpu}%",
            "action_type": None,
        })

    # ── Memory ────────────────────────────────────────────────────
    mem = psutil.virtual_memory()
    if mem.percent > 90:
        recs.append({
            "severity": "error",
            "message": f"Memory critical: {mem.percent}% ({round(mem.used/1e9,1)}/{round(mem.total/1e9,1)} GB)",
            "action_type": None,
        })
    elif mem.percent > 75:
        recs.append({
            "severity": "warning",
            "message": f"Memory high: {mem.percent}% ({round(mem.used/1e9,1)}/{round(mem.total/1e9,1)} GB)",
            "action_type": None,
        })

    # ── Disk ──────────────────────────────────────────────────────
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            if usage.percent > 90:
                recs.append({
                    "severity": "error",
                    "message": f"Disk {part.mountpoint} full: {usage.percent}%",
                    "action_type": "disk.cleanup_journal",
                    "action_args": {},
                    "action_label": "Clean",
                })
            elif usage.percent > 80:
                recs.append({
                    "severity": "warning",
                    "message": f"Disk {part.mountpoint} high: {usage.percent}%",
                    "action_type": None,
                })
        except Exception:
            pass

    # ── Load average ──────────────────────────────────────────────
    load1, load5, load15 = psutil.getloadavg()
    cpu_count = psutil.cpu_count() or 1
    if load5 > cpu_count * 2:
        recs.append({
            "severity": "error",
            "message": f"Load average critical: {round(load5,2)} ({cpu_count} cores)",
            "action_type": None,
        })
    elif load5 > cpu_count:
        recs.append({
            "severity": "warning",
            "message": f"Load average high: {round(load5,2)} ({cpu_count} cores)",
            "action_type": None,
        })

    # ── Recent auth failures (brute force) ────────────────────────
    store = get_store()
    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    auth_fails = store.query(event_type="auth.fail", since=since, limit=100)
    if len(auth_fails) > 10:
        ips = set()
        for ev in auth_fails:
            ip = ev.payload.get("source_ip")
            if ip:
                ips.add(ip)
        recs.append({
            "severity": "warning",
            "message": f"Possible brute-force: {len(auth_fails)} failed logins from {len(ips)} IPs",
            "action_type": None,
        })

    # ── Recent OOM kills ──────────────────────────────────────────
    oom_events = store.query(event_type="process.oom", since=since, limit=10)
    if oom_events:
        recs.append({
            "severity": "error",
            "message": f"OOM kill detected: {len(oom_events)} process(es) killed in last 30min",
            "action_type": None,
        })

    # ── Positive: all good ────────────────────────────────────────
    if not recs:
        recs.append({
            "severity": "success",
            "message": "All systems nominal. No issues detected.",
            "action_type": None,
        })

    return {"recommendations": recs, "count": len(recs)}


@app.post("/api/recommendations/execute")
async def api_execute_recommendation(request: Request):
    """Execute a recommendation action."""
    from trazezzo.agent.proactive.scheduler import AGGRESSIVE_ALLOWLIST
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    body = await request.json()
    action_type = body.get("action_type", "")
    action_args = body.get("action_args", {})

    if action_type not in AGGRESSIVE_ALLOWLIST:
        return {"success": False, "error": f"Action '{action_type}' not in allowlist"}

    import subprocess
    cmd_template = AGGRESSIVE_ALLOWLIST[action_type]
    try:
        cmd = cmd_template.format(**action_args)
    except KeyError:
        cmd = cmd_template

    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        success = proc.returncode == 0
        output = proc.stdout[-300:] if proc.stdout else ""
        stderr = proc.stderr[-300:] if proc.stderr else ""
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    # Record agent action
    store = get_store()
    store.add(ServerEvent(
        type=EventType.AGENT_ACTION,
        source="recommendation",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:rec"),
        message=f"Executed recommendation: {cmd}",
        payload={"command": cmd, "success": success, "output": output},
        severity="warning",
    ))

    # Publish to exec WebSocket
    try:
        publish_execution({
            "command": cmd,
            "source": "recommendation",
            "success": success,
            "output": output,
            "stderr": stderr,
            "message": f"Executed recommendation: {cmd}",
        })
    except Exception:
        pass

    return {"success": success, "output": output, "command": cmd}


# ── GitHub Integration ────────────────────────────────────────────────

@app.get("/github", response_class=HTMLResponse)
async def github_page(request: Request):
    """GitHub integration page."""
    from trazezzo.modules.github import has_token, get_user_info
    user = get_user_info() if has_token() else None
    return templates.TemplateResponse(request, "github.html", {
        "request": request,
        "has_token": has_token(),
        "user": user,
    })


@app.post("/api/github/token")
async def api_github_save_token(request: Request):
    """Save GitHub personal access token."""
    from trazezzo.modules.github import save_token
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    body = await request.json()
    token = body.get("token", "")
    if not token.strip():
        return {"error": "Token cannot be empty"}
    result = save_token(token.strip())
    if result.get("success"):
        store = get_store()
        store.add(ServerEvent(
            type=EventType.AGENT_ACTION,
            source="github",
            actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:github"),
            message="GitHub token saved",
            payload={"action": "token_save"},
            severity="warning",
        ))
    return result


@app.delete("/api/github/token")
async def api_github_delete_token():
    """Delete GitHub token."""
    from trazezzo.modules.github import delete_token
    return delete_token()


@app.get("/api/github/user")
async def api_github_user():
    """Get authenticated GitHub user info."""
    from trazezzo.modules.github import get_user_info
    return get_user_info()


@app.get("/api/github/repos")
async def api_github_repos():
    """List GitHub repositories."""
    from trazezzo.modules.github import list_repos
    return list_repos()


@app.get("/api/github/repos/{owner}/{repo}")
async def api_github_repo_info(owner: str, repo: str):
    """Get repo details."""
    from trazezzo.modules.github import get_repo_info
    return get_repo_info(owner, repo)


@app.get("/api/github/repos/{owner}/{repo}/branches")
async def api_github_branches(owner: str, repo: str):
    """List branches in a repo."""
    from trazezzo.modules.github import list_branches
    return list_branches(owner, repo)


@app.get("/api/github/repos/{owner}/{repo}/commits")
async def api_github_commits(owner: str, repo: str, branch: str = "main"):
    """List recent commits."""
    from trazezzo.modules.github import list_commits
    return list_commits(owner, repo, branch)


@app.post("/api/github/push")
async def api_github_push(request: Request):
    """Git add + commit + push in a local repo."""
    from trazezzo.modules.github import create_commit_and_push
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    body = await request.json()
    repo_path = body.get("repo_path", "")
    files = body.get("files", None)
    message = body.get("message", "Update from Trazezzo")
    branch = body.get("branch", None)
    result = create_commit_and_push(repo_path, files, message, branch)
    store = get_store()
    store.add(ServerEvent(
        type=EventType.AGENT_ACTION,
        source="github",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:github"),
        message=f"Git push: {repo_path} — {message}",
        payload=result,
        severity="warning",
    ))
    return result


@app.post("/api/github/pr")
async def api_github_create_pr(request: Request):
    """Create a pull request."""
    from trazezzo.modules.github import create_pull_request
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    body = await request.json()
    result = create_pull_request(
        owner=body.get("owner", ""),
        repo=body.get("repo", ""),
        title=body.get("title", "PR from Trazezzo"),
        head=body.get("head", ""),
        base=body.get("base", "main"),
        body=body.get("body", ""),
    )
    store = get_store()
    store.add(ServerEvent(
        type=EventType.AGENT_ACTION,
        source="github",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:github"),
        message=f"PR created: {body.get('title', '')}",
        payload=result,
        severity="warning",
    ))
    return result


# ── Processes ─────────────────────────────────────────────────────────

@app.get("/processes", response_class=HTMLResponse)
async def processes_page(request: Request):
    """Process manager page."""
    from trazezzo.modules.processes import get_processes
    procs = get_processes(sort_by="cpu", limit=50)
    return templates.TemplateResponse(request, "processes.html", {
        "request": request,
        "active": "processes",
        "processes": procs,
    })

@app.get("/api/processes")
async def api_processes(sort_by: str = "cpu", limit: int = 100):
    from trazezzo.modules.processes import get_processes
    return get_processes(sort_by=sort_by, limit=limit)

@app.post("/api/processes/{pid}/kill")
async def api_kill_process(pid: int, signal: str = "TERM"):
    from trazezzo.modules.processes import kill_process
    return kill_process(pid, signal)


# ── Network Connections ──────────────────────────────────────────────

@app.get("/connections", response_class=HTMLResponse)
async def connections_page(request: Request):
    """Network connections page."""
    from trazezzo.modules.connections import get_connections, get_listening_ports
    return templates.TemplateResponse(request, "connections.html", {
        "request": request,
        "active": "connections",
        "connections": get_connections(),
        "listening_ports": get_listening_ports(),
    })

@app.get("/api/connections")
async def api_connections():
    from trazezzo.modules.connections import get_connections
    return get_connections()

@app.get("/api/listening-ports")
async def api_listening_ports():
    from trazezzo.modules.connections import get_listening_ports
    return get_listening_ports()


# ── Cron Jobs ────────────────────────────────────────────────────────

@app.get("/cron", response_class=HTMLResponse)
async def cron_page(request: Request):
    """Cron job manager page."""
    from trazezzo.modules.cron import get_cron_jobs
    return templates.TemplateResponse(request, "cron.html", {
        "request": request,
        "active": "cron",
        "cron": get_cron_jobs(),
    })

@app.get("/api/cron")
async def api_cron():
    from trazezzo.modules.cron import get_cron_jobs
    return get_cron_jobs()

@app.post("/api/cron/add")
async def api_cron_add(request: Request):
    from trazezzo.modules.cron import add_cron_job
    body = await request.json()
    return add_cron_job(body.get("schedule", ""), body.get("command", ""))

@app.post("/api/cron/delete/{index}")
async def api_cron_delete(index: int):
    from trazezzo.modules.cron import delete_cron_job
    return delete_cron_job(index)


# ── SSL Certificates ─────────────────────────────────────────────────

@app.get("/certificates", response_class=HTMLResponse)
async def certificates_page(request: Request):
    """SSL certificate manager page."""
    from trazezzo.modules.ssl_certs import get_certs
    return templates.TemplateResponse(request, "certificates.html", {
        "request": request,
        "active": "certificates",
        "certs": get_certs(),
    })

@app.get("/api/certificates")
async def api_certificates():
    from trazezzo.modules.ssl_certs import get_certs
    return get_certs()


# ── Credential Scanner ───────────────────────────────────────────────

@app.get("/credentials", response_class=HTMLResponse)
async def credentials_page(request: Request):
    """Credential scanner page."""
    from trazezzo.modules.credentials import scan_credentials
    return templates.TemplateResponse(request, "credentials.html", {
        "request": request,
        "active": "credentials",
        "credentials": scan_credentials(),
    })


# ── Docker Compose ───────────────────────────────────────────────────

@app.get("/api/compose")
async def api_compose():
    from trazezzo.modules.compose import get_compose_projects
    return get_compose_projects()

@app.post("/api/compose/{action}")
async def api_compose_action(action: str, request: Request):
    from trazezzo.modules.compose import compose_action
    body = await request.json()
    return compose_action(body.get("project_dir", ""), action)


# ── SSH Connection Manager ────────────────────────────────────────────

@app.get("/ssh", response_class=HTMLResponse)
async def ssh_terminal_page(request: Request):
    """SSH terminal page — multi-tab remote terminal."""
    from trazezzo.modules.ssh_connections import list_connections, list_groups, list_ssh_keys
    return templates.TemplateResponse(request, "ssh_terminal.html", {
        "request": request,
        "active": "ssh",
        "connections": list_connections(),
        "groups": list_groups(),
        "ssh_keys": list_ssh_keys(),
    })


@app.get("/api/ssh/connections")
async def api_ssh_connections():
    from trazezzo.modules.ssh_connections import list_connections, list_groups
    return {"connections": list_connections(), "groups": list_groups()}


@app.post("/api/ssh/connections")
async def api_ssh_add_connection(request: Request):
    from trazezzo.modules.ssh_connections import add_connection
    body = await request.json()
    return add_connection(
        label=body.get("label", ""),
        host=body.get("host", ""),
        port=body.get("port", 22),
        user=body.get("user", "root"),
        auth_method=body.get("auth_method", "key"),
        key_path=body.get("key_path"),
        password=body.get("password"),
        group=body.get("group", "default"),
        proxy_jump=body.get("proxy_jump"),
    )


@app.put("/api/ssh/connections/{conn_id}")
async def api_ssh_update_connection(conn_id: str, request: Request):
    from trazezzo.modules.ssh_connections import update_connection
    body = await request.json()
    return update_connection(conn_id, **body)


@app.delete("/api/ssh/connections/{conn_id}")
async def api_ssh_delete_connection(conn_id: str):
    from trazezzo.modules.ssh_connections import delete_connection
    return {"success": delete_connection(conn_id)}


@app.post("/api/ssh/connections/{conn_id}/test")
async def api_ssh_test_connection(conn_id: str):
    from trazezzo.modules.ssh_connections import test_connection
    return test_connection(conn_id)


@app.get("/api/ssh/keys")
async def api_ssh_keys():
    from trazezzo.modules.ssh_connections import list_ssh_keys
    return {"keys": list_ssh_keys()}


@app.post("/api/ssh/keys/upload")
async def api_ssh_upload_key(request: Request):
    from trazezzo.modules.ssh_connections import upload_ssh_key
    body = await request.json()
    return upload_ssh_key(body.get("name", ""), body.get("content", ""))


@app.delete("/api/ssh/keys/{key_id}")
async def api_ssh_delete_key(key_id: str):
    from trazezzo.modules.ssh_connections import delete_ssh_key
    return {"success": delete_ssh_key(key_id)}


@app.get("/api/ssh/sessions")
async def api_ssh_sessions():
    from trazezzo.modules.ssh_terminal import list_active_sessions
    return {"sessions": list_active_sessions()}


# ── Sysctl / Kernel Tuning ────────────────────────────────────────────

@app.get("/sysctl", response_class=HTMLResponse)
async def sysctl_page(request: Request):
    from trazezzo.modules.sysctl import get_sysctl
    params = get_sysctl()
    return templates.TemplateResponse(request, "sysctl.html", {
        "request": request,
        "active": "sysctl",
        "params": params,
    })

@app.get("/api/sysctl")
async def api_sysctl():
    from trazezzo.modules.sysctl import get_sysctl
    return get_sysctl()

@app.post("/api/sysctl/set")
async def api_set_sysctl(request: Request):
    from trazezzo.modules.sysctl import set_sysctl
    body = await request.json()
    return set_sysctl(body.get("key", ""), body.get("value", ""))


# ── Database Management ───────────────────────────────────────────────

@app.get("/database", response_class=HTMLResponse)
async def database_page(request: Request):
    from trazezzo.modules.database import detect_databases
    servers = detect_databases()
    return templates.TemplateResponse(request, "database.html", {
        "request": request,
        "active": "database",
        "servers": servers,
    })

@app.get("/api/database/info/{db_type}")
async def api_db_info(db_type: str):
    from trazezzo.modules.database import db_info
    return db_info(db_type)

@app.post("/api/database/backup")
async def api_db_backup(request: Request):
    from trazezzo.modules.database import db_backup
    body = await request.json()
    return db_backup(body.get("db_type", ""), body.get("db_name", ""))


# ── Process Renice ────────────────────────────────────────────────────

@app.post("/api/processes/{pid}/renice")
async def api_renice_process(pid: int, priority: int):
    from trazezzo.modules.processes import renice_process
    return renice_process(pid, priority)


# ── Package Manager ───────────────────────────────────────────────────

@app.get("/api/packages/search")
async def api_search_package(q: str):
    from trazezzo.modules.updates import search_package
    return search_package(q)

@app.post("/api/packages/install")
async def api_install_package(request: Request):
    from trazezzo.modules.updates import install_package
    body = await request.json()
    return install_package(body.get("packages", []))

@app.post("/api/packages/remove")
async def api_remove_package(request: Request):
    from trazezzo.modules.updates import remove_package
    body = await request.json()
    return remove_package(body.get("packages", []), body.get("purge", False))


# ── Firewall Editor ───────────────────────────────────────────────────

@app.get("/api/firewall/rules")
async def api_firewall_rules():
    from trazezzo.modules.networking import get_firewall_rules
    return get_firewall_rules()

@app.post("/api/firewall/add")
async def api_firewall_add(request: Request):
    from trazezzo.modules.networking import add_firewall_rule
    body = await request.json()
    return add_firewall_rule(body.get("action", ""), body.get("port", ""), body.get("proto", "tcp"), body.get("source", ""))

@app.post("/api/firewall/delete")
async def api_firewall_delete(request: Request):
    from trazezzo.modules.networking import delete_firewall_rule
    body = await request.json()
    return delete_firewall_rule(body.get("rule_num", ""))

@app.post("/api/firewall/toggle")
async def api_firewall_toggle(request: Request):
    from trazezzo.modules.networking import toggle_firewall
    body = await request.json()
    return toggle_firewall(body.get("enable", False))


# ── VPN / Tunnels ────────────────────────────────────────────────────

@app.get("/api/vpn/tunnels")
async def api_vpn_tunnels():
    from trazezzo.modules.networking import get_vpn_tunnels
    return get_vpn_tunnels()


# ── systemd Timers ────────────────────────────────────────────────────

@app.get("/api/timers")
async def api_timers():
    from trazezzo.modules.cron import get_systemd_timers
    return get_systemd_timers()


# ── Run ───────────────────────────────────────────────────────────────

# ── Domain/App Inventory ─────────────────────────────────────────────

@app.get("/api/domain-inventory")
async def api_domain_inventory():
    from trazezzo.modules.domain_inventory import get_domain_inventory
    return {"domains": get_domain_inventory()}

@app.get("/api/github/read-file")
async def api_github_read_file(owner: str, repo: str, path: str, branch: str = ""):
    from trazezzo.modules.github import read_repo_file
    return read_repo_file(owner, repo, path, branch)

@app.get("/api/github/read-tree")
async def api_github_read_tree(owner: str, repo: str, branch: str = ""):
    from trazezzo.modules.github import read_repo_tree
    return read_repo_tree(owner, repo, branch)

@app.get("/api/github/linked-repos")
async def api_github_linked_repos():
    from trazezzo.modules.github import get_linked_repos
    return {"repos": get_linked_repos()}

# ── App Inventory ────────────────────────────────────────────────────

@app.get("/api/app-inventory")
async def api_app_inventory():
    from trazezzo.modules.app_inventory import get_app_inventory
    return {"apps": get_app_inventory()}

@app.get("/api/app-inventory/{app_id}/context")
async def api_app_context(app_id: str):
    from trazezzo.modules.app_inventory import get_app_context
    return get_app_context(app_id)

@app.post("/api/app-inventory/{app_id}/override")
async def api_app_override(app_id: str, request: Request):
    from trazezzo.modules.app_inventory import set_app_override
    body = await request.json()
    return set_app_override(app_id, body.get("repo"), body.get("description"))

@app.get("/api/app-inventory/repos")
async def api_available_repos():
    from trazezzo.modules.app_inventory import get_available_repos
    return {"repos": get_available_repos()}

@app.get("/api/github/tracked")
async def api_github_tracked():
    from trazezzo.modules.github import list_tracked_repos
    return list_tracked_repos()

@app.post("/api/github/tracked/add")
async def api_github_add_tracked(request: Request):
    from trazezzo.modules.github import add_tracked_repo
    body = await request.json()
    return add_tracked_repo(body.get("owner", ""), body.get("repo", ""))

@app.post("/api/github/tracked/remove")
async def api_github_remove_tracked(request: Request):
    from trazezzo.modules.github import remove_tracked_repo
    body = await request.json()
    return remove_tracked_repo(body.get("owner", ""), body.get("repo", ""))


# ── Run ───────────────────────────────────────────────────────────────

def run_dashboard():
    """Run the dashboard server."""
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="info")


if __name__ == "__main__":
    run_dashboard()
