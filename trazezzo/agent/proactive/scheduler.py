"""Proactive scheduler — calm (advisory) / aggressive (allowlist exec)."""

from __future__ import annotations

import logging
import subprocess
import os
from datetime import datetime, timezone, timedelta

from trazezzo.agent.schema import EventType, ActorKind, ServerEvent, Actor
from trazezzo.agent.store.sqlite_warm import get_store
from trazezzo.agent.proactive.approval import store_pending_approval as _store_pending_approval

log = logging.getLogger("trazezzo.proactive")

# ── Aggressive mode allowlist ──────────────────────────────────────────
# Only these actions may be auto-executed in aggressive mode.
# Everything else is advisory only.
AGGRESSIVE_ALLOWLIST = {
    "service.restart": "systemctl restart {unit}",
    "service.start": "systemctl start {unit}",
    "process.kill_oom": "kill -9 {pid}",
    "disk.cleanup_journal": "journalctl --vacuum-time=2d",
    "disk.cleanup_apt": "apt-get clean",
    "process.nice": "renice +10 {pid}",
}


async def proactive_check(store, mode: str = "calm"):
    """Run a single proactive check cycle."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=PROACTIVE_INTERVAL_MINUTES)

    # ── Advisory cooldown (per issue key) ────────────────────────────
    # Same finding recurring within COOLDOWN_MINUTES is suppressed, so a flapping
    # unit does not spam an advisory every check interval.
    global _last_advisory_at
    COOLDOWN_MINUTES = 60

    # Check for recent anomalies / failures
    events = store.query(
        since=since,
        limit=100,
    )

    findings = []

    # ── Detect: failed services ────────────────────────────────────
    failed_events = [e for e in events if e.type == EventType.SERVICE_FAIL]
    if failed_events:
        seen_units = set()
        for ev in failed_events[:3]:
            unit = ev.payload.get("unit", "unknown")
            # Guard: strip a trailing description if a legacy payload contains one
            if " - " in unit:
                unit = unit.split(" - ", 1)[0].strip()
            if unit in seen_units:
                continue
            seen_units.add(unit)
            findings.append({
                "issue": f"Service failed: {unit}",
                "severity": "error",
                "action_type": "service.restart",
                "action_args": {"unit": unit},
                "advisory": f"Service '{unit}' failed. Recommend: systemctl restart {unit}",
            })

    # ── Detect: high CPU/memory ────────────────────────────────────
    metric_spikes = [e for e in events if e.type == EventType.METRIC_SPIKE]
    if metric_spikes:
        seen_metrics = set()
        for ev in metric_spikes[:5]:
            metric = ev.payload.get("metric", "unknown")
            if metric in seen_metrics:
                continue
            seen_metrics.add(metric)
            value = ev.payload.get("value", 0)
            threshold = ev.payload.get("threshold", 0)
            action_type = None
            action_args = {}

            if metric.startswith("DISK:") and value > 95:
                action_type = "disk.cleanup_journal"
            elif metric == "CPU" and value > 95:
                try:
                    import psutil
                    procs = []
                    for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
                        try:
                            procs.append((p.info["pid"], p.info["name"], p.info["cpu_percent"]))
                        except Exception:
                            pass
                    procs.sort(key=lambda x: x[2] or 0, reverse=True)
                    if procs:
                        action_type = "process.nice"
                        action_args = {"pid": procs[0][0]}
                except Exception:
                    pass

            findings.append({
                "issue": f"Metric spike: {metric}={value} (threshold={threshold})",
                "severity": "warning",
                "action_type": action_type,
                "action_args": action_args,
                "advisory": f"High {metric}: {value} (threshold {threshold}).",
            })

    # ── Detect: auth failures (brute force) ────────────────────────
    auth_fails = [e for e in events if e.type == EventType.AUTH_FAIL]
    if len(auth_fails) > 10:
        ips = set()
        for ev in auth_fails:
            ip = ev.payload.get("source_ip")
            if ip:
                ips.add(ip)
        findings.append({
            "issue": f"Possible brute-force: {len(auth_fails)} auth failures from {len(ips)} IPs",
            "severity": "warning",
            "action_type": None,
            "advisory": f"Consider blocking source IPs: {', '.join(list(ips)[:5])}. {len(auth_fails)} failed auth attempts.",
        })

    # ── Detect: OOM kills ──────────────────────────────────────────
    oom_events = [e for e in events if e.type == EventType.PROCESS_OOM]
    if oom_events:
        for ev in oom_events[:2]:
            pid = ev.payload.get("pid", "unknown")
            findings.append({
                "issue": f"OOM kill detected: PID {pid}",
                "severity": "error",
                "action_type": None,
                "advisory": f"Process {pid} was OOM-killed. Consider increasing swap or reducing memory usage.",
            })

    # ── Act on findings ────────────────────────────────────────────
    for finding in findings:
        # Cooldown: skip if the same issue was advised recently
        issue_key = finding["issue"]
        last_at = _last_advisory_at.get(issue_key)
        if last_at and (now - last_at) < timedelta(minutes=COOLDOWN_MINUTES):
            continue
        _last_advisory_at[issue_key] = now
        # Bound the cooldown map
        if len(_last_advisory_at) > 200:
            horizon = now - timedelta(minutes=COOLDOWN_MINUTES * 4)
            _last_advisory_at = {k: v for k, v in _last_advisory_at.items() if v > horizon}

        log.info("[proactive:%s] %s", mode, finding["issue"])

        if mode == "aggressive" and finding.get("action_type"):
            action_type = finding["action_type"]
            if action_type in AGGRESSIVE_ALLOWLIST:
                cmd_template = AGGRESSIVE_ALLOWLIST[action_type]
                args = finding.get("action_args", {})
                try:
                    cmd = cmd_template.format(**args)
                except KeyError:
                    cmd = cmd_template

                # Store as pending approval — do NOT auto-execute
                _store_pending_approval(cmd, finding, store)
                store.add(ServerEvent(
                    type=EventType.AGENT_ADVISORY,
                    source="proactive",
                    actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="proactive:aggressive"),
                    message=f"⏳ Approval required: {cmd}",
                    payload={"finding": finding, "cmd": cmd, "action_type": action_type, "pending_approval": True},
                    severity="warning",
                ))
            else:
                log.warning("[proactive] Action %s not in allowlist — advisory only", action_type)
                store.add(ServerEvent(
                    type=EventType.AGENT_ADVISORY,
                    source="proactive",
                    actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="proactive:aggressive"),
                    message=finding["advisory"],
                    payload={"finding": finding, "reason": "action_not_in_allowlist"},
                    severity=finding["severity"],
                ))
        else:
            # Calm mode: record advisory
            store.add(ServerEvent(
                type=EventType.AGENT_ADVISORY,
                source="proactive",
                actor=Actor(kind=ActorKind.AGENT, id="trazezzo", session="proactive:calm"),
                message=finding["advisory"],
                payload={"finding": finding},
                severity=finding["severity"],
            ))

    return findings


# Config
PROACTIVE_INTERVAL_MINUTES = 5  # lookback window for each check

# Cooldown state: issue key → last advisory timestamp (per process)
_last_advisory_at: dict[str, datetime] = {}
