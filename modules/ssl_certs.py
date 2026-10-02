"""SSL certificate manager -- scan and check expiry."""

from __future__ import annotations
import subprocess
import re
from datetime import datetime
from pathlib import Path

CERT_DIRS = [
    "/etc/letsencrypt/live",
    "/etc/ssl/certs",
    "/etc/ssl/private",
    "/etc/nginx/ssl",
    "/etc/apache2/ssl",
]


def get_certs() -> list[dict]:
    """Scan common cert directories and return cert info."""
    certs = []
    seen = set()

    for cert_dir in CERT_DIRS:
        d = Path(cert_dir)
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix not in (".pem", ".crt", ".cert"):
                continue
            if f.name in seen:
                continue
            seen.add(f.name)
            info = _inspect_cert(str(f))
            if info:
                certs.append(info)

    certs.extend(_scan_nginx_certs())

    certs.sort(key=lambda x: x.get("days_left", 999))
    return certs


def _inspect_cert(path: str) -> dict | None:
    """Use openssl to inspect a certificate."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", path, "-noout", "-subject", "-enddate", "-issuer", "-dates"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None

        output = result.stdout
        subject = ""
        issuer = ""
        not_after = ""
        not_before = ""

        for line in output.split("\n"):
            if line.startswith("subject="):
                subject = line.split("=", 1)[1].strip()
            elif line.startswith("issuer="):
                issuer = line.split("=", 1)[1].strip()
            elif line.startswith("notAfter="):
                not_after = line.split("=", 1)[1].strip()
            elif line.startswith("notBefore="):
                not_before = line.split("=", 1)[1].strip()

        if not not_after:
            return None

        try:
            expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
        except ValueError:
            expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y")
        now = datetime.now()
        days_left = (expiry - now).days

        cn = ""
        m = re.search(r'CN\s*=\s*([^,]+)', subject)
        if m:
            cn = m.group(1).strip()

        return {
            "path": path,
            "subject": subject,
            "cn": cn,
            "issuer": issuer,
            "not_before": not_before,
            "not_after": not_after,
            "days_left": days_left,
            "expired": days_left < 0,
            "expiring_soon": 0 <= days_left <= 30,
        }
    except Exception:
        return None


def _scan_nginx_certs() -> list[dict]:
    """Scan nginx config for ssl_certificate directives."""
    certs = []
    nginx_conf = Path("/etc/nginx")
    if not nginx_conf.exists():
        return certs

    try:
        result = subprocess.run(
            ["nginx", "-T"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return certs

        for m in re.finditer(r'ssl_certificate\s+([^;]+);', result.stdout):
            path = m.group(1).strip()
            if path not in [c["path"] for c in certs]:
                info = _inspect_cert(path)
                if info:
                    certs.append(info)
    except Exception:
        pass

    return certs
