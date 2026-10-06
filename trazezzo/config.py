"""Configuration — paths, ports, retention settings."""

from __future__ import annotations

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
APP_VERSION = "0.2.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TRAZEZZO_DATA_DIR", "/var/lib/trazezzo"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "events.db"
CONFIG_PATH = DATA_DIR / "config.json"

# ── Network ────────────────────────────────────────────────────────────
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 9122

# ── Auth ────────────────────────────────────────────────────────────────
# No default password ships with the code. Set TRAZEZZO_AUTH_PASSWORD in your
# service environment, or on first start a random password is generated and
# written to DATA_DIR/initial_admin_password.txt (chmod 600).
import secrets

AUTH_USERNAME = os.environ.get("TRAZEZZO_AUTH_USERNAME", "admin")

_password_env = os.environ.get("TRAZEZZO_AUTH_PASSWORD", "").strip()
if _password_env:
    AUTH_PASSWORD = _password_env
else:
    _pw_file = DATA_DIR / "initial_admin_password.txt"
    if _pw_file.exists():
        AUTH_PASSWORD = _pw_file.read_text().strip()
    else:
        AUTH_PASSWORD = secrets.token_urlsafe(12)
        _pw_file.write_text(AUTH_PASSWORD)
        os.chmod(_pw_file, 0o600)
        print(f"[trazezzo] No TRAZEZZO_AUTH_PASSWORD set — generated an initial admin "
              f"password for user '{AUTH_USERNAME}'. Read it from: {_pw_file}")

# Session signing secret: env var, or generated once and persisted so logins
# survive service restarts.
_secret_env = os.environ.get("TRAZEZZO_SESSION_SECRET", "").strip()
if _secret_env:
    SESSION_SECRET = _secret_env
else:
    _sec_file = DATA_DIR / "session_secret"
    if _sec_file.exists():
        SESSION_SECRET = _sec_file.read_text().strip()
    else:
        SESSION_SECRET = secrets.token_urlsafe(32)
        _sec_file.write_text(SESSION_SECRET)
        os.chmod(_sec_file, 0o600)

# ── Retention ──────────────────────────────────────────────────────────
WARM_RETENTION_DAYS = 7
RING_BUFFER_HOT_SECONDS = 300  # 5 min in-memory
BATCH_INSERT_INTERVAL = 1.0    # seconds between bulk inserts
BATCH_INSERT_MAX_EVENTS = 500

# ── Proactive agent ────────────────────────────────────────────────────
PROACTIVE_INTERVAL = 30  # seconds between checks
PROACTIVE_MODE = "calm"  # calm | aggressive

# ── LLM (any OpenAI-compatible endpoint) ──────────────────────────────
# Defaults target 9Router (https://9router.com). Override via env vars to use
# OpenAI, Ollama, vLLM, LM Studio, or any OpenAI-compatible provider.
LLM_BASE_URL = os.environ.get("TRAZEZZO_LLM_BASE_URL", "https://9router.com/v1")
LLM_MODEL = os.environ.get("TRAZEZZO_LLM_MODEL", "coding")
LLM_ENV_KEY_NAME = os.environ.get("TRAZEZZO_LLM_KEY_NAME", "NINEROUTER_API_KEY")
LLM_ENV_PATH = os.environ.get("TRAZEZZO_LLM_ENV_PATH", "/etc/trazezzo/.env")

# ── Capture sources ───────────────────────────────────────────────────
CAPTURE_JOURNALD = True
CAPTURE_AUDITD = True
CAPTURE_PSUTIL = True
CAPTURE_METRICS_INTERVAL = 5  # seconds

# ── nginx ─────────────────────────────────────────────────────────────
NGINX_LOCATION = "/trazezzo/"
NGINX_PROXY_TARGET = "http://127.0.0.1:9122"
