"""Credential scanner — detect plaintext secrets in config files.

Ported from Knowledge Center. Read-only: only reads files, never exposes secret values.
Reports: file path, type of secret detected, line number — value is MASKED.
"""

from __future__ import annotations

import os
import re

# Patterns for common secret types
SECRET_PATTERNS = [
    ("API Key (generic)", re.compile(r'(?i)(api[_-]?key)\s*[=:]\s*["\']?([a-zA-Z0-9\-_]{20,})')),
    ("Bearer Token", re.compile(r'(?i)bearer\s+([a-zA-Z0-9\-_\.]{20,})')),
    ("AWS Access Key", re.compile(r'(AKIA[0-9A-Z]{16})')),
    ("AWS Secret Key", re.compile(r'(?i)aws[_-]?secret[_-]?access[_-]?key\s*[=:]\s*["\']?([a-zA-Z0-9/+=]{40})')),
    ("GitHub PAT", re.compile(r'(github_pat_[a-zA-Z0-9_]{20,})')),
    ("GitHub Token (ghp)", re.compile(r'(ghp_[a-zA-Z0-9]{36})')),
    ("Google API Key", re.compile(r'(AIza[0-9A-Za-z\-_]{35})')),
    ("Private Key", re.compile(r'-----BEGIN (RSA |EC |DSA |OPENSSH |)PRIVATE KEY-----')),
    ("Password assignment", re.compile(r'(?i)(password|passwd|pwd)\s*[=:]\s*["\']?([^\s"\']{4,})')),
    ("Database URL", re.compile(r'(?i)(postgres|mysql|mongodb|redis)://[^\s"\']+:[^\s"\']+@')),
    ("Connection string", re.compile(r'(?i)(connection[_-]?string|conn[_-]?str)\s*[=:]\s*["\']([^\s"\']{10,})')),
    ("Slack Token", re.compile(r'(xox[baprs]-[0-9a-zA-Z\-]{10,})')),
    ("Generic secret assignment", re.compile(r'(?i)(secret|token|auth)\s*[=:]\s*["\']?([a-zA-Z0-9\-_]{16,})')),
]

# Directories to scan
SCAN_PATHS = [
    "/etc/nginx",
    "/etc/systemd/system",
    "/etc/cron.d",
    "/root/.hermes",
    "/opt/trazezzo",
    "/opt",
    "/var/www",
]

SCAN_EXTENSIONS = ['.conf', '.env', '.yaml', '.yml', '.json', '.toml', '.ini',
                   '.sh', '.py', '.js', '.ts', '.service', '.properties', '']

SKIP_FILES = ['.pyc', '.pyo', '.so', '.png', '.jpg', '.jpeg', '.gif', '.ico',
              '.woff', '.ttf', '.eot', '.svg', '.db', '.sqlite', '.log', '.pid',
              '.lock', '.sock', '.gz', '.zip', '.tar', '.pdf']

MAX_FILE_SIZE = 1024 * 1024  # 1MB


def scan_credentials():
    """Scan config files for potential plaintext credentials.

    Returns dict with files_scanned, credentials_found, findings.
    Secret values are NEVER included — only masked indicators.
    """
    findings = []
    files_scanned = 0

    for scan_path in SCAN_PATHS:
        if not os.path.exists(scan_path):
            continue

        if os.path.isfile(scan_path):
            findings.extend(_scan_file(scan_path))
            files_scanned += 1
            continue

        for root, dirs, files in os.walk(scan_path):
            depth = root.replace(scan_path, '').count(os.sep)
            if depth > 2:
                dirs[:] = []
                continue

            dirs[:] = [d for d in dirs if d not in
                       ['__pycache__', '.git', 'node_modules', 'venv', '.venv',
                        'kc-venv', 'hf', 'image_cache', 'audio_cache', 'backups',
                        '.cache', 'cache', 'sessions', 'plugin-data', 'plugins',
                        'skills', 'sandboxes']]

            for fname in files:
                if any(fname.endswith(ext) for ext in SKIP_FILES):
                    continue
                if not any(fname.endswith(ext) for ext in SCAN_EXTENSIONS):
                    if not fname.startswith('.'):
                        continue

                filepath = os.path.join(root, fname)
                findings.extend(_scan_file(filepath))
                files_scanned += 1

    # Deduplicate
    seen = set()
    unique = []
    for f in findings:
        key = (f["file"], f["line"])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return {
        "files_scanned": files_scanned,
        "credentials_found": len(unique),
        "findings": unique,
    }


def _scan_file(filepath):
    """Scan a single file for credential patterns."""
    results = []

    try:
        size = os.path.getsize(filepath)
        if size > MAX_FILE_SIZE or size == 0:
            return []

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num, line in enumerate(f, 1):
                for secret_type, pattern in SECRET_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        groups = match.groups()
                        matched_value = groups[-1] if groups else match.group(0)

                        if len(matched_value) > 8:
                            masked = matched_value[:3] + "••••••••" + matched_value[-3:]
                        else:
                            masked = "••••••••"

                        results.append({
                            "file": filepath,
                            "line": line_num,
                            "type": secret_type,
                            "masked_value": masked,
                            "context": _safe_context(line, match),
                        })

    except (PermissionError, FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
        pass

    return results


def _safe_context(line, match):
    """Return a sanitized context line with the secret masked."""
    start, end = match.span()
    groups = match.groups()
    if groups:
        secret_val = max(groups, key=len) if groups else ""
        if secret_val and len(secret_val) > 3:
            masked_secret = secret_val[:2] + "*" * (len(secret_val) - 4) + secret_val[-2:]
            line = line.replace(secret_val, masked_secret)

    line = line.strip()
    if len(line) > 120:
        line = line[:120] + "..."

    return line
