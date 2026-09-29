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
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trazezzo.config import DASHBOARD_HOST, DASHBOARD_PORT, PROACTIVE_MODE
from trazezzo.agent.store.sqlite_warm import get_store
from trazezzo.dashboard.ws import websocket_endpoint

log = logging.getLogger("trazezzo.dashboard")

# ── Paths ─────────────────────────────────────────────────────────────
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# ── App ────────────────────────────────────────────────────────────────
app = FastAPI(title="Trazezzo", version="0.1.0")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "overview": overview,
        "failed_services": failed,
        "counts": counts,
        "proactive_mode": PROACTIVE_MODE,
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
    """Security page (SELinux, SSH keys, credentials)."""
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
        "credentials": scan_credentials(),
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
    mode = body.get("mode", "reasoning")  # reasoning | coding
    image_data = body.get("image", None)  # base64 image (data URI)

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

    # ── Agent mode: full operation ────────────────────────────────────
    if mode == "agent":
        from trazezzo.agent.chat_agent import run_agent
        # Pass chat history from client
        chat_history = body.get("history", [])

        async def agent_stream():
            try:
                async for event_json in run_agent(user_message, context_parts, chat_history, image_data):
                    yield f"data: {event_json}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        return StreamingResponse(agent_stream(), media_type="text/event-stream")

    # ── Reasoning mode (default) ──────────────────────────────────────

    system_prompt = (
        "Kamu adalah Trazezzo, AI server operations assistant.\n\n"
        "## Tentang Trazezzo\n"
        "Trazezzo adalah platform operasi server AI-native (terinspirasi Cockpit OS). "
        "Trazezzo berjalan di server ini dan punya komponen berikut:\n\n"
        "### Arsitektur\n"
        "- **Agent daemon**: asyncio, 4 capture sources (journald, auditd, psutil, eBPF via bpftrace)\n"
        "- **Warm store**: SQLite WAL, retensi 7 hari, ring buffer, batched writes\n"
        "- **Dashboard**: FastAPI + Jinja2, port 9122, 3-column layout (nav | content | chat panel)\n"
        "- **LLM**: 9router (OpenAI-compatible API, model 'coding')\n"
        "- **Attribution**: tiap event diklasifikasi actor (user/agent/system)\n\n"
        "### Fitur Utama\n"
        "1. **System Overview**: CPU/RAM/disk/load realtime, failed services, proactive mode indicator\n"
        "2. **Services**: list/start/stop/restart systemd services\n"
        "3. **Logs**: journald viewer with filter\n"
        "4. **Activity Feed**: black box recorder, semua event dikategorikan (user/agent/system), live WebSocket\n"
        "5. **Causal Timeline**: 'apa yang terjadi sebelum crash/down' — vis-timeline, window reconstruction\n"
        "6. **System Diagram**: auto-discover services, ports, mounts, network → cytoscape.js graph\n"
        "7. **Networking**: interfaces, routes, firewall, bonds\n"
        "8. **Storage**: disk usage, mounts, LVM, block devices\n"
        "9. **Containers**: podman/docker list + action\n"
        "10. **Accounts**: user management, active sessions, groups\n"
        "11. **Updates**: apt/dnf package updates\n"
        "12. **Security**: SELinux, SSH keys, kdump, tuned profiles, credential scanner (plaintext secret detection)\n"
        "13. **Diagnostic**: sosreport generation\n"
        "14. **File Editor**: browse/read/edit config files with auto-backup\n"
        "15. **Sync & Archive**: warm→cold gzip archive, manual + cron scheduled\n"
        "16. **AI Chatbot** (kamu): talk to server, streaming response, image paste support\n"
        "17. **Auto Recommendations**: cek real-time failed services, CPU/mem/disk/load, brute-force, OOM — dengan action button\n"
        "18. **Execution Panel**: real-time agent command execution display, slides in next to chat\n"
        "19. **Proactive Agent**: 2 mode — Calm (advisory only) / Aggressive (propose auto-remediate with user approval: restart service, kill OOM, clean disk, renice CPU)\n\n"
        "20. **Agent Chat Mode**: Toggle Q&A ↔ Agent di chat panel. Agent mode bisa eksekusi command, baca/tulis file, git push, service management langsung dari chat. Contoh: \"server lemot, kenapa?\", \"ada bug di aplikasi ini tolong betulkan\", \"deploy versi ini tanpa push ke git dulu\"\n\n"
        "21. **GitHub Integration**: secure token storage, list repos, view commits, push changes, create PRs — page /trazezzo/github\n\n"
        "### Capture Sources\n"
        "- **journald**: journalctl --follow --output=json\n"
        "- **auditd**: tail /var/log/audit/audit.log (execve, login, file events)\n"
        "- **psutil**: CPU/mem/disk/net metrics + anomaly detection (threshold-based)\n"
        "- **eBPF**: bpftrace (execve, connect, kill syscalls)\n\n"
        "### Event Attribution\n"
        "Setiap event diklasifikasi:\n"
        "- `user`: event dari login session (UID >= 1000 atau sudo)\n"
        "- `agent`: event dari Trazezzo sendiri (proactive action, recommendation execute, dashboard action)\n"
        "- `system`: event dari kernel/systemd/daemon\n\n"
        "### URL\n"
        "- Local: http://127.0.0.1:9122\n"
        "- Public: https://ys.amital.co.id/trazezzo/\n"
        "- Nginx reverse proxy: /trazezzo/ → :9122\n"
        "- systemd unit: trazezzo.service\n\n"
        "### Cara Pakai\n"
        "- Toggle chat mode (Q&A / Agent) di chat panel header — Q&A untuk tanya jawab, Agent untuk eksekusi command\n"
        "- Toggle proactive mode (Calm/Aggressive) di chat panel header\n"
        "- Aggressive mode: agent propose remediation, user approve via chat sebelum eksekusi\n"
        "- Klik recommendation action button untuk execute fix\n"
        "- Execution panel auto-open saat agent menjalankan command\n"
        "- Chat history persistent via localStorage (tombol 🗑 untuk clear)\n"
        "- Paste gambar (Ctrl+V) untuk analisis visual\n"
        "- GitHub integration di /trazezzo/github — simpan PAT, list repos, push, create PR\n"
        "## Instruksi Jawaban\n"
        "Jawab dalam Bahasa Indonesia, singkat dan actionable. "
        "Jika user tanya kondisi server, gunakan data context. "
        "Jika user minta action, jelaskan command yang perlu dijalankan. "
        "Jika user tanya tentang Trazezzo, gunakan pengetahuan di atas."
    )

    # Build user message — text context + optional image
    user_content_parts = [f"Context:\n{chr(10).join(context_parts)}\n\nPertanyaan: {user_message}"]

    messages = [{"role": "system", "content": system_prompt}]

    # If image provided, send as multimodal message (OpenAI vision format)
    if image_data:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"Context:\n{chr(10).join(context_parts)}\n\nPertanyaan: {user_message}"},
                {"type": "image_url", "image_url": {"url": image_data}},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_content_parts[0]})

    async def stream():
        try:
            async for chunk in chat_completion_stream(messages, temperature=0.5, max_tokens=1500):
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

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


# ── Sync / Archive ────────────────────────────────────────────────────

@app.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request):
    """Sync/archive management page."""
    from trazezzo.modules.sync import get_sync_status, get_cron_status
    return templates.TemplateResponse(request, "sync.html", {
        "request": request,
        "status": get_sync_status(),
        "cron": get_cron_status(),
    })


@app.get("/api/sync/status")
async def api_sync_status():
    from trazezzo.modules.sync import get_sync_status, get_cron_status
    return {**get_sync_status(), **get_cron_status()}


@app.post("/api/sync/trigger")
async def api_sync_trigger():
    from trazezzo.modules.sync import trigger_sync
    from trazezzo.agent.schema import ServerEvent, EventType, Actor, ActorKind
    result = trigger_sync()
    store = get_store()
    store.add(ServerEvent(
        type=EventType.AGENT_ACTION,
        source="sync",
        actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="dashboard:sync"),
        message=f"Manual sync triggered: {result.get('archived', 0)} events archived",
        payload=result,
        severity="info",
    ))
    return result


@app.post("/api/sync/cron")
async def api_sync_cron(request: Request):
    from trazezzo.modules.sync import setup_cron, remove_cron
    body = await request.json()
    action = body.get("action", "setup")
    schedule = body.get("schedule", "0 3 * * 0")
    if action == "setup":
        return setup_cron(schedule)
    elif action == "remove":
        return remove_cron()
    return {"error": "Unknown action"}


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

@app.get("/api/proactive/status")
async def api_proactive_status():
    """Get proactive agent status."""
    from trazezzo.config import PROACTIVE_MODE, PROACTIVE_INTERVAL
    return {
        "mode": PROACTIVE_MODE,
        "interval": PROACTIVE_INTERVAL,
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


# ── Run ───────────────────────────────────────────────────────────────

def run_dashboard():
    """Run the dashboard server."""
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="info")


if __name__ == "__main__":
    run_dashboard()
