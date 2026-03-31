"""System health metrics: CPU, RAM, disk, systemd, uptime, logs, skills."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from .config import DASHBOARD_DB, HERMES_HOME, STATE_DB


async def get_system_health() -> dict[str, Any]:
    results = await asyncio.gather(
        _get_resources(),
        _get_gateway_status(),
        _get_db_sizes(),
        _get_recent_errors(),
        _get_skills(),
        return_exceptions=True,
    )

    resources = results[0] if not isinstance(results[0], Exception) else {}
    gateway = results[1] if not isinstance(results[1], Exception) else {}
    db_sizes = results[2] if not isinstance(results[2], Exception) else {}
    errors = results[3] if not isinstance(results[3], Exception) else []
    skills = results[4] if not isinstance(results[4], Exception) else []

    return {
        "resources": resources,
        "gateway": gateway,
        "db_sizes": db_sizes,
        "recent_errors": errors,
        "skills": skills,
    }


async def _get_resources() -> dict:
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime_secs = (datetime.now() - boot).total_seconds()

    return {
        "cpu_percent": cpu,
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / (1024**3), 1),
        "memory_total_gb": round(mem.total / (1024**3), 1),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
        "uptime_hours": round(uptime_secs / 3600, 1),
    }


async def _get_gateway_status() -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "hermes-gateway",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        status = stdout.decode().strip()

        proc2 = await asyncio.create_subprocess_exec(
            "systemctl", "show", "hermes-gateway",
            "--property=ActiveEnterTimestamp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout2, _ = await proc2.communicate()
        ts_line = stdout2.decode().strip()
        active_since = ts_line.split("=", 1)[1] if "=" in ts_line else ""

        return {"status": status, "active_since": active_since}
    except Exception:
        return {"status": "unknown", "active_since": ""}


async def _get_db_sizes() -> dict:
    sizes = {}
    if STATE_DB.exists():
        sizes["state_db_mb"] = round(STATE_DB.stat().st_size / (1024**2), 1)
    if DASHBOARD_DB.exists():
        sizes["dashboard_db_mb"] = round(DASHBOARD_DB.stat().st_size / (1024**2), 1)
    return sizes


async def _get_recent_errors(n: int = 10) -> list[str]:
    error_log = HERMES_HOME / "logs" / "errors.log"
    if not error_log.exists():
        return []
    try:
        text = error_log.read_text(errors="replace")
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        return lines[-n:]
    except Exception:
        return []


async def _get_skills() -> list[dict]:
    skills_dir = HERMES_HOME / "skills"
    if not skills_dir.exists():
        return []
    result = []
    try:
        for item in sorted(skills_dir.iterdir()):
            if item.is_dir():
                skill_file = item / "SKILL.md"
                created = datetime.fromtimestamp(item.stat().st_ctime).strftime("%Y-%m-%d")
                result.append({"name": item.name, "has_skill_md": skill_file.exists(), "created": created})
    except Exception:
        pass
    return result


async def get_uptime_timeline(hours: int = 168) -> list[dict]:
    """Parse journalctl for gateway start/stop events to build uptime timeline."""
    try:
        since = f"{hours}h ago"
        proc = await asyncio.create_subprocess_exec(
            "journalctl", "-u", "hermes-gateway",
            f"--since={since}", "--output=json", "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)

        import json
        events = []
        for line in stdout.decode(errors="replace").strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = int(entry.get("__REALTIME_TIMESTAMP", "0")) // 1_000_000
                msg = entry.get("MESSAGE", "")
                if any(kw in msg.lower() for kw in ("started", "starting", "running")):
                    events.append({"ts": ts, "event": "start"})
                elif any(kw in msg.lower() for kw in ("stopped", "stopping", "failed", "exited")):
                    events.append({"ts": ts, "event": "stop"})
            except (json.JSONDecodeError, ValueError):
                continue
        return events
    except Exception:
        return []


async def get_cron_jobs() -> list[dict]:
    """Run `hermes cron list` and parse the block-format output."""
    try:
        import re

        env = {**os.environ, "HOME": str(HERMES_HOME.parent) if HERMES_HOME.name == ".hermes" else "/root"}
        env["PATH"] = "/root/.local/bin:" + env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        proc = await asyncio.create_subprocess_exec(
            "hermes", "cron", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode(errors="replace").strip()

        if not output or "no cron" in output.lower():
            return []

        header_re = re.compile(r"^\s+([a-f0-9]+)\s+\[(active|paused)\]")
        kv_re = re.compile(r"^\s+(\w[\w\s]*):\s+(.+)$")
        key_map = {
            "name": "name",
            "schedule": "schedule",
            "repeat": "repeat",
            "next run": "next_run",
            "deliver": "deliver",
        }

        jobs: list[dict] = []
        current: dict | None = None

        for line in output.split("\n"):
            hm = header_re.match(line)
            if hm:
                if current is not None:
                    jobs.append(current)
                current = {"id": hm.group(1), "status": hm.group(2)}
                continue
            if current is None:
                continue
            kvm = kv_re.match(line)
            if kvm:
                key = kvm.group(1).strip().lower()
                value = kvm.group(2).strip()
                if key in key_map:
                    current[key_map[key]] = value

        if current is not None:
            jobs.append(current)

        return jobs
    except Exception:
        return []
