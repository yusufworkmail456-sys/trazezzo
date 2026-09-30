"""Configuration — paths, ports, retention settings."""

from __future__ import annotations

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TRAZEZZO_DATA_DIR", "/var/lib/trazezzo"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "events.db"
CONFIG_PATH = DATA_DIR / "config.json"

# ── Network ────────────────────────────────────────────────────────────
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 9122

# ── Auth ────────────────────────────────────────────────────────────────
AUTH_USERNAME = os.environ.get("TRAZEZZO_AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.environ.get("TRAZEZZO_AUTH_PASSWORD", "trazezzo")
SESSION_SECRET = os.environ.get("TRAZEZZO_SESSION_SECRET", "trazezzo-session-secret-change-me")

# ── Retention ──────────────────────────────────────────────────────────
WARM_RETENTION_DAYS = 7
RING_BUFFER_HOT_SECONDS = 300  # 5 min in-memory
BATCH_INSERT_INTERVAL = 1.0    # seconds between bulk inserts
BATCH_INSERT_MAX_EVENTS = 500

# ── Proactive agent ────────────────────────────────────────────────────
PROACTIVE_INTERVAL = 30  # seconds between checks
PROACTIVE_MODE = "calm"  # calm | aggressive

# ── LLM (reuse 9router from KC) ───────────────────────────────────────
LLM_BASE_URL = "https://9router.amital.co.id/v1"
LLM_MODEL = "coding"
LLM_ENV_KEY_NAME = "HERMES_CUSTOM_9ROUTER_AMITAL_CO_ID_API_KEY"
LLM_ENV_PATH = "/root/.hermes/.env"

# ── Capture sources ───────────────────────────────────────────────────
CAPTURE_JOURNALD = True
CAPTURE_AUDITD = True
CAPTURE_PSUTIL = True
CAPTURE_METRICS_INTERVAL = 5  # seconds

# ── nginx ─────────────────────────────────────────────────────────────
NGINX_LOCATION = "/trazezzo/"
NGINX_PROXY_TARGET = "http://127.0.0.1:9122"
