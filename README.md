# Trazezzo

**AI-native server operations platform** — Cockpit-style dashboard + black box event recorder + proactive AI agent, in a single Python process.

Trazezzo watches your server (journald, auditd, psutil, optional eBPF), records every event in a searchable store, and gives you a web dashboard plus an AI chatbot that can answer questions about your server's state and history — or actively fix problems with your approval.

Full documentation: **https://ys.amital.co.id/trazezzo-landing/docs.html**

## Features

- **System Overview** — realtime CPU / RAM / disk / load, failed services, proactive mode indicator
- **Events** — black box recorder with Live Feed (WebSocket) + Causal Timeline ("what happened before the crash?")
- **Logs** — journald viewer with a realtime streaming tail, pause, and filtering
- **Services / Processes / Containers / Cron / Updates** — list and manage with one click
- **Networking / Storage / Accounts / Security** — interfaces, firewall, disks (local vs network fs), users, credential scanner
- **File Editor + Web Terminal** — edit configs (auto-backup) and run an interactive shell from the browser
- **Event Export** — download captured events as `.json.gz` for any time range
- **Auto Recommendations** — rule-based checks (failed services, CPU/mem/disk/load, brute-force, OOM) with action buttons
- **Proactive Agent** — background loop: `calm` (advisory only) or `aggressive` (proposes auto-remediation; allowlisted actions run on approval)
- **AI Chatbot** — Q&A mode + full Agent mode (exec, file read/write, git push, service management). Paste images (Ctrl+V) for visual analysis
- **GitHub Integration** — store a PAT, browse repos, push fixes, open PRs from the dashboard

## Requirements

- Linux (tested on Ubuntu 22.04+), kernel ≥ 5.8 recommended for eBPF capture
- Python ≥ 3.12
- Root (event capture + service management need privileged access)
- Optional: `auditd` (security events), `bpftrace` (eBPF tracing)

## Quick Start (bundle mode)

```bash
git clone https://github.com/yusufworkmail456-sys/trazezzo.git
cd trazezzo
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# system packages
apt-get install -y auditd audispd-plugins
systemctl enable --now auditd

mkdir -p /var/lib/trazezzo

# run (foreground test)
python -m trazezzo.daemon_entry
```

Dashboard: http://localhost:9122 — default login `admin` / `trazezzo` (**change it in production**, see below).

### CLI shortcut

`trazezzo init` automates venv + dependencies + systemd unit + nginx snippet; then `trazezzo up`.

```
trazezzo init      # venv, deps, auditd, systemd, nginx
trazezzo up        # start service
trazezzo status    # service + API status
trazezzo down      # stop
trazezzo update    # git pull + reinstall + restart
trazezzo log       # tail service logs
```

## LLM configuration (AI chatbot)

Trazezzo talks to **any OpenAI-compatible endpoint**. Defaults target [9Router](https://9router.com); to use OpenAI, Ollama, vLLM, LM Studio, etc., either edit `trazezzo/config.py` or set env vars on the service:

```bash
# /etc/trazezzo/.env  (chmod 600)
NINEROUTER_API_KEY=sk-...        # key name configurable
```

```ini
# systemd override example
Environment=TRAZEZZO_LLM_BASE_URL=https://api.openai.com/v1
Environment=TRAZEZZO_LLM_MODEL=gpt-4o
Environment=TRAZEZZO_LLM_KEY_NAME=OPENAI_API_KEY
Environment=TRAZEZZO_LLM_ENV_PATH=/etc/trazezzo/.env
```

## Configuration

All settings live in `trazezzo/config.py`; environment variables override defaults.

| Setting | Env override | Default | Description |
|---|---|---|---|
| `DASHBOARD_HOST` | — | `127.0.0.1` | Bind address (keep localhost, front with nginx) |
| `DASHBOARD_PORT` | — | `9122` | Dashboard port |
| `DATA_DIR` | `TRAZEZZO_DATA_DIR` | `/var/lib/trazezzo` | Event DB + state |
| `AUTH_USERNAME` | `TRAZEZZO_AUTH_USERNAME` | `admin` | Dashboard login |
| `AUTH_PASSWORD` | `TRAZEZZO_AUTH_PASSWORD` | `trazezzo` | **Change in production!** |
| `SESSION_SECRET` | `TRAZEZZO_SESSION_SECRET` | auto | Set for sessions that survive restarts |
| `WARM_RETENTION_DAYS` | — | `7` | Event retention in the SQLite warm store |
| `PROACTIVE_INTERVAL` | — | `30` | Proactive check interval (s) |
| `PROACTIVE_MODE` | — | `calm` | `calm` or `aggressive` |
| `LLM_BASE_URL` | `TRAZEZZO_LLM_BASE_URL` | `https://9router.com/v1` | OpenAI-compatible endpoint |
| `LLM_MODEL` | `TRAZEZZO_LLM_MODEL` | `coding` | Model name |
| `LLM_ENV_KEY_NAME` | `TRAZEZZO_LLM_KEY_NAME` | `NINEROUTER_API_KEY` | Env/file key name |
| `LLM_ENV_PATH` | `TRAZEZZO_LLM_ENV_PATH` | `/etc/trazezzo/.env` | File containing the API key |

## nginx reverse proxy

```nginx
# /etc/nginx/snippets/trazezzo.conf  (written automatically by `trazezzo init`)
location /trazezzo/ {
    proxy_pass http://127.0.0.1:9122/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # WebSocket support
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;

    # Streaming support (SSE / chat)
    proxy_buffering off;
    proxy_cache off;
}
```

Include it in your server block: `include snippets/trazezzo.conf;` then `nginx -t && systemctl reload nginx`.

## systemd service

```ini
[Unit]
Description=Trazezzo — AI-Native Server Operations Platform
After=network.target auditd.service

[Service]
Type=simple
ExecStart=/opt/trazezzo/.venv/bin/python -m trazezzo.daemon_entry
Restart=on-failure
RestartSec=5
Environment=TRAZEZZO_DATA_DIR=/var/lib/trazezzo
Environment=PYTHONUNBUFFERED=1
# Production credentials:
# Environment=TRAZEZZO_AUTH_USERNAME=you
# Environment=TRAZEZZO_AUTH_PASSWORD=strong-password
# Environment=TRAZEZZO_SESSION_SECRET=random-long-string
WorkingDirectory=/opt/trazezzo
User=root

[Install]
WantedBy=multi-user.target
```

## Architecture

```
┌────────────────────────── one Python process (bundle mode) ─────────────────────────┐
│                                                                                      │
│  Capture sources            Warm store                 Dashboard (FastAPI, :9122)    │
│  ├─ journald      ─┐                                   ├─ Web UI (Jinja2 + HTMX-ish) │
│  ├─ auditd         ├──▶  SQLite WAL (7d retention) ──▶ ├─ REST API + WebSocket bus   │
│  ├─ psutil        ─┤    + in-memory ring buffer        ├─ AI chatbot (SSE stream)    │
│  └─ eBPF (opt.)   ─┘                                   └─ Proactive agent loop       │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
                                            LLM (OpenAI-compatible)
```

- **Bundle mode** — dashboard + agent in one process (default; `daemon_entry.py`)
- **Separated mode** — run `trazezzo.main` (dashboard) and the agent loop as two services sharing the SQLite DB (see docs)

Every event is attributed as `user` / `agent` / `system`, so you can ask "what did the agent do this week?" or "what happened 5 minutes before the crash?"

## Safety model

- **Calm mode (default)** — the proactive agent only observes and recommends
- **Aggressive mode** — allowlisted remediations only (`systemctl restart {unit}`, `kill -9 {pid}`, journal/apt cleanup, `renice`); anything else lands in an **approval queue** shown in chat — nothing runs without your click
- Agent chat mode: dangerous commands (`rm -rf /`, `mkfs`, `dd`, `shutdown`, …) are blocked at the action parser

## Security notes

- Change default credentials before exposing Trazezzo (`TRAZEZZO_AUTH_*` env)
- Keep the dashboard bound to `127.0.0.1` and put nginx (TLS) in front
- The LLM API key is read from a 600-permission env file, kept in memory, never logged
- GitHub PAT is stored in `DATA_DIR/github_token.json` (created on save, file-permission restricted)

## License

MIT — see [LICENSE](LICENSE).
