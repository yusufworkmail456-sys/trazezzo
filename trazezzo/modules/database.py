"""Database management — auto-detect PostgreSQL, MySQL/MariaDB, Redis, MongoDB."""

from __future__ import annotations

import subprocess
import shutil
import shlex


def detect_databases() -> list[dict]:
    """Detect installed database servers and their status."""
    servers = []

    # PostgreSQL
    if shutil.which("psql"):
        status = _check_service("postgresql")
        servers.append({"type": "postgresql", "installed": True, "running": status, "version": _get_version("psql --version")})

    # MySQL / MariaDB
    if shutil.which("mysql"):
        status = _check_service("mysql") or _check_service("mariadb")
        version = _get_version("mysql --version")
        servers.append({"type": "mysql", "installed": True, "running": status, "version": version})

    # Redis
    if shutil.which("redis-cli"):
        status = _check_service("redis") or _check_service("redis-server")
        servers.append({"type": "redis", "installed": True, "running": status, "version": _get_version("redis-cli --version")})

    # MongoDB
    if shutil.which("mongosh") or shutil.which("mongo"):
        status = _check_service("mongod")
        cli = "mongosh" if shutil.which("mongosh") else "mongo"
        servers.append({"type": "mongodb", "installed": True, "running": status, "version": _get_version(f"{cli} --version")})

    return servers


def _check_service(name: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def _get_version(cmd: str) -> str:
    try:
        parts = shlex.split(cmd)
        result = subprocess.run(parts, capture_output=True, text=True, timeout=5)
        return result.stdout.strip().split("\n")[0][:80]
    except Exception:
        return "unknown"


def db_info(db_type: str) -> dict:
    """Get database info: sizes, connections, activity."""
    if db_type == "postgresql":
        return _pg_info()
    elif db_type in ("mysql", "mariadb"):
        return _mysql_info()
    elif db_type == "redis":
        return _redis_info()
    elif db_type == "mongodb":
        return _mongo_info()
    return {"error": f"Unsupported database type: {db_type}"}


def _pg_info() -> dict:
    queries = {
        "databases": "SELECT datname, pg_size_pretty(pg_database_size(datname)) as size FROM pg_database WHERE datistemplate = false ORDER BY pg_database_size(datname) DESC;",
        "connections": "SELECT count(*) as total, count(*) FILTER (WHERE state = 'active') as active FROM pg_stat_activity;",
        "slow_queries": "SELECT query, calls, mean_exec_time, total_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 5;",
    }
    result = {"type": "postgresql", "sections": {}}
    for label, query in queries.items():
        try:
            r = subprocess.run(
                ["psql", "-U", "postgres", "-t", "-A", "-F", "|", "-c", query],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                rows = [line for line in r.stdout.strip().split("\n") if line]
                result["sections"][label] = rows
            else:
                result["sections"][label] = [f"Error: {r.stderr.strip()[:100]}"]
        except Exception as exc:
            result["sections"][label] = [f"Error: {exc}"]
    return result


def _mysql_info() -> dict:
    queries = {
        "databases": "SELECT table_schema, ROUND(SUM(data_length+index_length)/1024/1024,1) as size_mb FROM information_schema.tables GROUP BY table_schema;",
        "connections": "SELECT count(*) as total FROM information_schema.processlist;",
        "slow_queries": "SHOW VARIABLES LIKE 'slow_query_log%';",
    }
    result = {"type": "mysql", "sections": {}}
    for label, query in queries.items():
        try:
            r = subprocess.run(
                ["mysql", "-e", query],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                rows = [line for line in r.stdout.strip().split("\n") if line]
                result["sections"][label] = rows
            else:
                result["sections"][label] = [f"Error: {r.stderr.strip()[:100]}"]
        except Exception as exc:
            result["sections"][label] = [f"Error: {exc}"]
    return result


def _redis_info() -> dict:
    result = {"type": "redis", "sections": {}}
    try:
        r = subprocess.run(
            ["redis-cli", "INFO"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            info = {}
            for line in r.stdout.split("\n"):
                if ":" in line and not line.startswith("#"):
                    k, v = line.split(":", 1)
                    info[k.strip()] = v.strip()
            result["sections"]["server"] = [f"{k}: {v}" for k, v in list(info.items())[:20]]
            result["sections"]["memory"] = [
                f"used_memory: {info.get('used_memory_human', 'N/A')}",
                f"peak: {info.get('used_memory_peak_human', 'N/A')}",
                f"maxmemory: {info.get('maxmemory_human', 'N/A')}",
                f"clients: {info.get('connected_clients', 'N/A')}",
                f"uptime: {info.get('uptime_in_days', 'N/A')} days",
            ]
            result["sections"]["keys"] = [
                f"db0: {info.get('db0', 'empty')}",
                f"total_connections: {info.get('total_connections_received', 'N/A')}",
                f"total_commands: {info.get('total_commands_processed', 'N/A')}",
            ]
        else:
            result["sections"]["error"] = [r.stderr.strip()[:200]]
    except Exception as exc:
        result["sections"]["error"] = [str(exc)]
    return result


def _mongo_info() -> dict:
    result = {"type": "mongodb", "sections": {}}
    try:
        cli = "mongosh" if shutil.which("mongosh") else "mongo"
        r = subprocess.run(
            [cli, "--quiet", "--eval", "JSON.stringify(db.serverStatus())"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            result["sections"]["status"] = r.stdout.strip()[:2000].split("\n")
        else:
            result["sections"]["error"] = [r.stderr.strip()[:200]]
    except Exception as exc:
        result["sections"]["error"] = [str(exc)]
    return result


def db_backup(db_type: str, db_name: str, output_dir: str = "/tmp") -> dict:
    """Create a database backup."""
    import os
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if db_type == "postgresql":
        output_file = os.path.join(output_dir, f"pg_{db_name}_{timestamp}.sql")
        try:
            r = subprocess.run(
                ["pg_dump", "-U", "postgres", "-f", output_file, db_name],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                size = os.path.getsize(output_file)
                return {"success": True, "file": output_file, "size": size}
            else:
                return {"success": False, "error": r.stderr.strip()[:200]}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    elif db_type in ("mysql", "mariadb"):
        output_file = os.path.join(output_dir, f"mysql_{db_name}_{timestamp}.sql")
        try:
            r = subprocess.run(
                ["mysqldump", db_name, "-r", output_file],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                size = os.path.getsize(output_file)
                return {"success": True, "file": output_file, "size": size}
            else:
                return {"success": False, "error": r.stderr.strip()[:200]}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    return {"success": False, "error": f"Backup not supported for {db_type}"}
