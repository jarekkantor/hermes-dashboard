"""Analytics queries across dashboard.db and state.db."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ALERT_DAILY_USD, ALERT_MONTHLY_USD, ALERT_WEEKLY_USD, HERMES_HOME
from .database import get_dashboard_db, get_state_db
from .pricing import calculate_cost


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


# ── Helpers ───────────────────────────────────────────────────────────

def _ts_start_of_day(date_str: str) -> float:
    """Convert 'YYYY-MM-DD' to unix timestamp at midnight UTC."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def _ts_ago(days: int) -> float:
    """Unix timestamp for N days ago."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()


# ── KPIs ──────────────────────────────────────────────────────────────

async def get_kpis() -> dict[str, Any]:
    db = get_dashboard_db()
    today = _today()
    yesterday = _days_ago(1)
    week_ago = _days_ago(7)
    month_start = today[:8] + "01"

    async def _scalar(sql: str, params: tuple = ()) -> Any:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return row[0] if row else 0

    tokens_today = await _scalar(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM api_requests WHERE ts >= ?",
        (today,),
    )
    cost_today = await _scalar(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM api_requests WHERE ts >= ?",
        (today,),
    )
    requests_today = await _scalar(
        "SELECT COUNT(*) FROM api_requests WHERE ts >= ?",
        (today,),
    )
    cost_month = await _scalar(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM api_requests WHERE ts >= ?",
        (month_start,),
    )
    cost_week = await _scalar(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM api_requests WHERE ts >= ?",
        (week_ago,),
    )

    sessions_today = 0
    tokens_yesterday = 0
    cost_yesterday = 0.0
    tokens_7d = 0
    cost_7d = 0.0

    state = get_state_db()
    if state:
        ts_today = _ts_start_of_day(today)
        ts_yesterday = _ts_start_of_day(yesterday)
        ts_week = _ts_ago(7)
        ts_month = _ts_start_of_day(month_start)

        sessions_today = await _scalar_state(
            state, "SELECT COUNT(*) FROM sessions WHERE started_at >= ?", (ts_today,),
        )
        tokens_yesterday = await _scalar_state(
            state,
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM sessions WHERE started_at >= ? AND started_at < ?",
            (ts_yesterday, ts_today),
        )
        cost_yesterday = await _scalar_state(
            state,
            "SELECT COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0) FROM sessions WHERE started_at >= ? AND started_at < ?",
            (ts_yesterday, ts_today),
        )
        tokens_7d = await _scalar_state(
            state,
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM sessions WHERE started_at >= ?",
            (ts_week,),
        )
        cost_7d = await _scalar_state(
            state,
            "SELECT COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0) FROM sessions WHERE started_at >= ?",
            (ts_week,),
        )
        state_tokens_today = await _scalar_state(
            state,
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM sessions WHERE started_at >= ?",
            (ts_today,),
        )
        state_cost_today = await _scalar_state(
            state,
            "SELECT COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0) FROM sessions WHERE started_at >= ?",
            (ts_today,),
        )
        if state_tokens_today > tokens_today:
            tokens_today = state_tokens_today
        if state_cost_today > cost_today:
            cost_today = state_cost_today

        state_cost_month = await _scalar_state(
            state,
            "SELECT COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0) FROM sessions WHERE started_at >= ?",
            (ts_month,),
        )
        if state_cost_month > cost_month:
            cost_month = state_cost_month

    avg_daily_7d = max(cost_week, cost_7d) / 7 if (cost_week or cost_7d) else 0
    projected_monthly = round(avg_daily_7d * 30, 2)

    alerts = []
    if ALERT_DAILY_USD and cost_today >= ALERT_DAILY_USD:
        alerts.append(f"Daily spend ${cost_today:.2f} exceeds ${ALERT_DAILY_USD:.2f} threshold")
    if ALERT_WEEKLY_USD and cost_week >= ALERT_WEEKLY_USD:
        alerts.append(f"Weekly spend ${cost_week:.2f} exceeds ${ALERT_WEEKLY_USD:.2f} threshold")
    if ALERT_MONTHLY_USD and cost_month >= ALERT_MONTHLY_USD:
        alerts.append(f"Monthly spend ${cost_month:.2f} exceeds ${ALERT_MONTHLY_USD:.2f} threshold")

    return {
        "tokens_today": tokens_today,
        "cost_today": round(cost_today, 2),
        "requests_today": requests_today,
        "sessions_today": sessions_today,
        "cost_month": round(cost_month, 2),
        "cost_week": round(cost_week, 2),
        "projected_monthly": projected_monthly,
        "tokens_yesterday": tokens_yesterday,
        "cost_yesterday": round(cost_yesterday, 2),
        "tokens_7d": tokens_7d,
        "cost_7d": round(cost_7d, 2),
        "alerts": alerts,
    }


async def _scalar_state(db, sql: str, params: tuple = ()) -> Any:
    try:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


# ── Daily token usage by model ────────────────────────────────────────

async def get_daily_usage(days: int = 30) -> list[dict]:
    db = get_dashboard_db()
    since = _days_ago(days)
    cur = await db.execute(
        """SELECT date(ts) as day, model,
                  SUM(input_tokens) as input_tok,
                  SUM(output_tokens) as output_tok,
                  SUM(input_tokens + output_tokens) as total_tok,
                  SUM(cost_usd) as cost
           FROM api_requests
           WHERE ts >= ?
           GROUP BY day, model
           ORDER BY day""",
        (since,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Daily cost trend ──────────────────────────────────────────────────

async def get_daily_cost(days: int = 30) -> list[dict]:
    db = get_dashboard_db()
    since = _days_ago(days)
    cur = await db.execute(
        """SELECT date(ts) as day, model, SUM(cost_usd) as cost
           FROM api_requests WHERE ts >= ?
           GROUP BY day, model ORDER BY day""",
        (since,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Platform analytics (from state.db) ───────────────────────────────

async def get_platform_usage(days: int = 30) -> list[dict]:
    state = get_state_db()
    if not state:
        return []
    since_ts = _ts_ago(days)
    try:
        cur = await state.execute(
            """SELECT date(started_at, 'unixepoch') as day, source as platform,
                      COUNT(*) as sessions,
                      COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens
               FROM sessions
               WHERE started_at >= ?
               GROUP BY day, platform
               ORDER BY day""",
            (since_ts,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


async def get_platform_summary(days: int = 30) -> list[dict]:
    state = get_state_db()
    if not state:
        return []
    since_ts = _ts_ago(days)
    try:
        cur = await state.execute(
            """SELECT source as platform,
                      COUNT(*) as sessions,
                      COUNT(DISTINCT user_id) as unique_users,
                      COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                      COALESCE(SUM(message_count), 0) as total_messages,
                      COALESCE(SUM(tool_call_count), 0) as total_tool_calls
               FROM sessions
               WHERE started_at >= ?
               GROUP BY platform
               ORDER BY sessions DESC""",
            (since_ts,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Tool usage analytics (from state.db messages) ────────────────────

async def _extract_tool_counts(state, since_unix: float = 0) -> list[dict]:
    """Extract tool names from the tool_calls JSON column and count them.

    Hermes stores tool calls as JSON arrays in messages.tool_calls with
    structure: [{"function": {"name": "tool_name"}, ...}].
    The tool_name column is typically empty for gateway sessions.
    """
    try:
        if since_unix:
            cur = await state.execute(
                """SELECT m.tool_calls FROM messages m
                   JOIN sessions s ON m.session_id = s.id
                   WHERE m.tool_calls IS NOT NULL AND m.tool_calls != ''
                         AND m.tool_calls != 'null' AND m.role = 'assistant'
                         AND s.started_at >= ?""",
                (since_unix,),
            )
        else:
            cur = await state.execute(
                """SELECT tool_calls FROM messages
                   WHERE tool_calls IS NOT NULL AND tool_calls != ''
                         AND tool_calls != 'null' AND role = 'assistant'"""
            )
        rows = await cur.fetchall()
        counts: dict[str, int] = {}
        for (tc_json,) in rows:
            try:
                calls = json.loads(tc_json)
                for call in calls:
                    name = call.get("function", {}).get("name", "")
                    if name:
                        counts[name] = counts.get(name, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue
        sorted_tools = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:20]
        return [{"tool_name": name, "count": cnt} for name, cnt in sorted_tools]
    except Exception:
        return []


async def get_tool_usage(days: int = 30) -> list[dict]:
    state = get_state_db()
    if not state:
        return []
    return await _extract_tool_counts(state, _ts_ago(days))


async def get_tool_usage_over_time(days: int = 30) -> list[dict]:
    state = get_state_db()
    if not state:
        return []
    try:
        cur = await state.execute(
            """SELECT m.tool_calls, date(s.started_at, 'unixepoch') as day
               FROM messages m
               JOIN sessions s ON m.session_id = s.id
               WHERE m.tool_calls IS NOT NULL AND m.tool_calls != ''
                     AND m.tool_calls != 'null' AND m.role = 'assistant'
                     AND s.started_at >= ?""",
            (_ts_ago(days),),
        )
        rows = await cur.fetchall()
        day_cat: dict[tuple[str, str], int] = {}
        for tc_json, day in rows:
            try:
                calls = json.loads(tc_json)
                for call in calls:
                    name = call.get("function", {}).get("name", "")
                    if name:
                        cat = _categorize_tool(name)
                        key = (day, cat)
                        day_cat[key] = day_cat.get(key, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue
        return sorted(
            [{"day": k[0], "category": k[1], "count": v} for k, v in day_cat.items()],
            key=lambda x: x["day"],
        )
    except Exception:
        return []


# ── Model distribution ────────────────────────────────────────────────

async def get_model_distribution(days: int = 30) -> list[dict]:
    db = get_dashboard_db()
    since = _days_ago(days)
    cur = await db.execute(
        """SELECT model,
                  SUM(input_tokens + output_tokens) as total_tokens,
                  SUM(cost_usd) as total_cost,
                  COUNT(*) as requests
           FROM api_requests WHERE ts >= ?
           GROUP BY model ORDER BY total_tokens DESC""",
        (since,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Hourly heatmap ────────────────────────────────────────────────────

async def get_hourly_heatmap(days: int = 30) -> list[dict]:
    db = get_dashboard_db()
    since = _days_ago(days)
    cur = await db.execute(
        """SELECT CAST(strftime('%w', ts) AS INTEGER) as dow,
                  CAST(strftime('%H', ts) AS INTEGER) as hour,
                  COUNT(*) as count,
                  SUM(input_tokens + output_tokens) as tokens
           FROM api_requests WHERE ts >= ?
           GROUP BY dow, hour""",
        (since,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Conversation depth (input token distribution) ─────────────────────

async def get_context_depth(days: int = 30) -> dict:
    db = get_dashboard_db()
    since = _days_ago(days)
    cur = await db.execute(
        """SELECT input_tokens FROM api_requests
           WHERE ts >= ? AND status_code = 200
           ORDER BY ts""",
        (since,),
    )
    rows = await cur.fetchall()
    values = [r[0] for r in rows]
    if not values:
        return {"histogram": [], "avg_over_time": []}

    buckets = [0]*10
    max_val = max(values) or 1
    for v in values:
        idx = min(int(v / (max_val / 10)), 9)
        buckets[idx] += 1

    labels = [f"{int(max_val/10*i)}-{int(max_val/10*(i+1))}" for i in range(10)]

    cur2 = await db.execute(
        """SELECT date(ts) as day, AVG(input_tokens) as avg_input
           FROM api_requests
           WHERE ts >= ? AND status_code = 200
           GROUP BY day ORDER BY day""",
        (since,),
    )
    avg_rows = await cur2.fetchall()

    return {
        "histogram": [{"label": l, "count": c} for l, c in zip(labels, buckets)],
        "avg_over_time": [dict(r) for r in avg_rows],
    }


# ── Output token distribution ────────────────────────────────────────

async def get_output_distribution(days: int = 30) -> list[dict]:
    db = get_dashboard_db()
    since = _days_ago(days)
    brackets = [
        (0, 100, "0-100"),
        (100, 500, "100-500"),
        (500, 1000, "500-1K"),
        (1000, 2000, "1K-2K"),
        (2000, 5000, "2K-5K"),
        (5000, 10000, "5K-10K"),
        (10000, 999999999, "10K+"),
    ]
    result = []
    for lo, hi, label in brackets:
        cur = await db.execute(
            "SELECT COUNT(*) FROM api_requests WHERE ts >= ? AND output_tokens >= ? AND output_tokens < ?",
            (since, lo, hi),
        )
        row = await cur.fetchone()
        result.append({"label": label, "count": row[0]})
    return result


# ── Error tracking ────────────────────────────────────────────────────

async def get_error_stats(days: int = 30) -> dict:
    db = get_dashboard_db()
    since = _days_ago(days)

    cur = await db.execute(
        """SELECT date(ts) as day,
                  SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as errors,
                  COUNT(*) as total
           FROM api_requests WHERE ts >= ?
           GROUP BY day ORDER BY day""",
        (since,),
    )
    daily = [dict(r) for r in await cur.fetchall()]

    cur2 = await db.execute(
        """SELECT ts, status_code, model, error_type
           FROM api_requests
           WHERE ts >= ? AND status_code >= 400
           ORDER BY ts DESC LIMIT 50""",
        (since,),
    )
    recent_errors = [dict(r) for r in await cur2.fetchall()]

    cur3 = await db.execute(
        """SELECT status_code, COUNT(*) as count
           FROM api_requests
           WHERE ts >= ? AND status_code >= 400
           GROUP BY status_code ORDER BY count DESC""",
        (since,),
    )
    by_status = [dict(r) for r in await cur3.fetchall()]

    return {"daily": daily, "recent_errors": recent_errors, "by_status": by_status}


# ── Recent requests log ──────────────────────────────────────────────

async def get_recent_requests(limit: int = 100) -> list[dict]:
    db = get_dashboard_db()
    cur = await db.execute(
        """SELECT ts, model, input_tokens, output_tokens,
                  cache_creation_tokens, cache_read_tokens,
                  cost_usd, latency_ms, status_code, is_streaming, stop_reason
           FROM api_requests
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Top cost days ─────────────────────────────────────────────────────

async def get_top_cost_days(limit: int = 10) -> list[dict]:
    db = get_dashboard_db()
    cur = await db.execute(
        """SELECT date(ts) as day,
                  SUM(cost_usd) as cost,
                  SUM(input_tokens + output_tokens) as tokens,
                  COUNT(*) as requests
           FROM api_requests
           GROUP BY day ORDER BY cost DESC LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in await cur.fetchall()]


# ── Cache efficiency ──────────────────────────────────────────────────

async def get_cache_efficiency(days: int = 30) -> dict:
    db = get_dashboard_db()
    since = _days_ago(days)
    cur = await db.execute(
        """SELECT
              SUM(cache_read_tokens) as total_read,
              SUM(cache_creation_tokens) as total_write,
              SUM(cache_read_tokens + cache_creation_tokens) as total_cache
           FROM api_requests WHERE ts >= ?""",
        (since,),
    )
    row = await cur.fetchone()
    total_read = row[0] or 0
    total_cache = row[2] or 0
    efficiency = round(total_read / total_cache * 100, 1) if total_cache > 0 else 0
    return {
        "cache_read_tokens": total_read,
        "cache_write_tokens": row[1] or 0,
        "efficiency_pct": efficiency,
    }


# ── Sessions list from state.db ──────────────────────────────────────

async def get_recent_sessions(limit: int = 20) -> list[dict]:
    state = get_state_db()
    if not state:
        return []
    try:
        cur = await state.execute(
            """SELECT id, source, title, model,
                      datetime(started_at, 'unixepoch') as started_at,
                      datetime(ended_at, 'unixepoch') as ended_at,
                      message_count, tool_call_count,
                      input_tokens + output_tokens as total_tokens
               FROM sessions
               ORDER BY started_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]
    except Exception:
        return []


# ── Error log stats (from errors.log) ─────────────────────────────────

_ERROR_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+ (WARNING|ERROR|INFO) .*?: (.+)$"
)


def _classify_error(msg: str) -> str:
    lower = msg.lower()
    if "response.content is empty" in lower:
        return "Empty response"
    if "connection error" in lower or "connect" in lower:
        return "Connection error"
    if "rate" in lower and "limit" in lower:
        return "Rate limit"
    if "retry" in lower:
        return "Retry"
    return "Other"


async def get_error_log_stats(days: int = 30) -> dict:
    error_log = HERMES_HOME / "logs" / "errors.log"
    if not error_log.exists():
        return {"daily": [], "by_type": [], "recent": [], "total_retries": 0, "total_failures": 0}

    cutoff = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    daily_map: dict[str, dict] = {}
    type_counts: dict[str, int] = {}
    recent: list[dict] = []
    total_retries = 0
    total_failures = 0

    try:
        text = error_log.read_text(errors="replace")
    except Exception:
        return {"daily": [], "by_type": [], "recent": [], "total_retries": 0, "total_failures": 0}

    for line in text.strip().split("\n"):
        m = _ERROR_LOG_RE.match(line.strip())
        if not m:
            continue
        date_str, time_str, level, message = m.groups()
        if date_str < cutoff_str:
            continue

        day = daily_map.setdefault(date_str, {"date": date_str, "warnings": 0, "errors": 0, "total": 0})
        day["total"] += 1
        if level == "WARNING":
            day["warnings"] += 1
        elif level == "ERROR":
            day["errors"] += 1

        err_type = _classify_error(message)
        type_counts[err_type] = type_counts.get(err_type, 0) + 1

        if "retry" in message.lower():
            total_retries += 1
        if level == "ERROR":
            total_failures += 1

        recent.append({"ts": f"{date_str} {time_str}", "level": level, "message": message})

    recent = recent[-20:]

    daily_sorted = sorted(daily_map.values(), key=lambda d: d["date"])
    by_type = sorted(
        [{"type": t, "count": c} for t, c in type_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )

    return {
        "daily": daily_sorted,
        "by_type": by_type,
        "recent": recent,
        "total_retries": total_retries,
        "total_failures": total_failures,
    }


# ── Token / efficiency analysis ──────────────────────────────────────

_TOOL_CATEGORIES = {
    "browser": {"browser_navigate", "browser_snapshot", "browser_click", "browser_type",
                "browser_tab", "browser_tabs", "browser_scroll", "browser_fill",
                "browser_select", "browser_hover", "browser_wait"},
    "web": {"web_search", "web_extract", "web_fetch", "fetch"},
    "terminal": {"terminal", "bash", "process", "shell"},
    "file": {"read_file", "write_file", "patch", "read", "write", "edit"},
    "memory": {"memory", "memory_search", "memory_read", "memory_write", "session_search"},
    "automation": {"cronjob", "delegate_task", "delegate", "send_message", "todo", "clarify"},
}


def _categorize_tool(name: str) -> str:
    lower = name.lower()
    for category, tools in _TOOL_CATEGORIES.items():
        if lower in tools or any(lower.startswith(t) for t in tools):
            return category
    return "other"


async def get_token_analysis(days: int = 30) -> dict:
    error_stats = await get_error_log_stats(days)

    retry_timeline = [
        {"day": d["date"], "warnings": d.get("warnings", 0), "errors": d.get("errors", 0), "total": d.get("total", 0)}
        for d in error_stats.get("daily", [])
    ]

    state = get_state_db()
    top_tools: list[dict] = []
    session_depth: list[dict] = []
    total_tool_calls = 0
    deepest_session_messages = 0
    total_input = 0
    total_output = 0

    if state:
        try:
            tool_counts = await _extract_tool_counts(state)
            top_tools = [{"name": t["tool_name"], "count": t["count"]} for t in tool_counts]
            total_tool_calls = sum(t["count"] for t in top_tools)
        except Exception:
            pass

        try:
            cur = await state.execute(
                """SELECT s.id, s.source, s.model, s.message_count, s.tool_call_count,
                          s.title, s.started_at, s.input_tokens, s.output_tokens
                   FROM sessions s
                   ORDER BY s.message_count DESC LIMIT 15"""
            )
            rows = await cur.fetchall()
            for r in rows:
                sid = r[0]
                est_tokens = (r[7] or 0) + (r[8] or 0)
                if est_tokens == 0:
                    try:
                        cur2 = await state.execute(
                            "SELECT SUM(LENGTH(content)) FROM messages WHERE session_id = ?", (sid,),
                        )
                        row2 = await cur2.fetchone()
                        if row2 and row2[0]:
                            est_tokens = row2[0] // 4
                    except Exception:
                        pass

                started_str = ""
                if r[6]:
                    try:
                        started_str = datetime.fromtimestamp(float(r[6]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        started_str = str(r[6])

                session_depth.append({
                    "id": sid, "source": r[1] or "", "model": r[2] or "",
                    "messages": r[3] or 0, "tool_calls": r[4] or 0,
                    "title": r[5] or "", "started_at": started_str,
                    "est_tokens": est_tokens,
                })
                if (r[3] or 0) > deepest_session_messages:
                    deepest_session_messages = r[3]
        except Exception:
            pass

        try:
            cur = await state.execute(
                "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM sessions"
            )
            row = await cur.fetchone()
            if row:
                total_input = row[0]
                total_output = row[1]
        except Exception:
            pass

        if total_input + total_output == 0:
            try:
                cur = await state.execute("SELECT COALESCE(SUM(LENGTH(content)), 0) FROM messages")
                row = await cur.fetchone()
                if row and row[0]:
                    est_total = row[0] // 4
                    total_input = int(est_total * 0.7)
                    total_output = int(est_total * 0.3)
            except Exception:
                pass

    db = get_dashboard_db()
    try:
        cur = await db.execute(
            "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM api_requests"
        )
        row = await cur.fetchone()
        if row:
            total_input = max(total_input, row[0])
            total_output = max(total_output, row[1])
    except Exception:
        pass

    total_retries = error_stats.get("total_retries", 0)

    avg_input_per_req = 0
    try:
        cur = await db.execute("SELECT AVG(input_tokens) FROM api_requests WHERE status_code = 200 AND input_tokens > 0")
        row = await cur.fetchone()
        avg_input_per_req = int(row[0]) if row and row[0] else 0
    except Exception:
        pass
    if avg_input_per_req == 0:
        avg_input_per_req = 2000

    retry_token_waste = total_retries * avg_input_per_req
    tool_overhead = total_tool_calls * 200
    total_tokens = total_input + total_output
    useful_output = max(total_tokens - retry_token_waste - tool_overhead, 0)
    estimated_waste_pct = min(round(retry_token_waste / total_tokens * 100, 1) if total_tokens > 0 else 0.0, 100.0)

    today_str = datetime.now().strftime("%Y-%m-%d")
    retries_today = 0
    for d in error_stats.get("daily", []):
        if d.get("date") == today_str:
            retries_today = d.get("total", 0)
            break

    return {
        "retry_timeline": retry_timeline,
        "top_tools": top_tools,
        "session_depth": session_depth,
        "token_breakdown": {
            "retry_waste": retry_token_waste,
            "tool_overhead": tool_overhead,
            "useful_output": useful_output,
        },
        "summary": {
            "total_retries": total_retries,
            "total_tool_calls": total_tool_calls,
            "deepest_session_messages": deepest_session_messages,
            "estimated_waste_pct": estimated_waste_pct,
            "retries_today": retries_today,
            "tool_calls_today": total_tool_calls,
        },
    }


# ── Token Forensics (request dump analysis) ──────────────────────────

async def get_token_forensics() -> dict:
    """Analyze request dumps and session data to show where tokens go."""
    sessions_dir = HERMES_HOME / "sessions"
    dumps: list[dict] = []
    tool_costs: dict[str, int] = {}
    system_prompt_tokens = 0
    tools_total_tokens = 0
    tool_count = 0

    if sessions_dir.exists():
        dump_files = sorted(
            [f for f in sessions_dir.iterdir() if f.name.startswith("request_dump") and f.suffix == ".json"],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for df in dump_files[:20]:
            try:
                raw = json.loads(df.read_text(errors="replace"))
                body_raw = raw.get("request", {}).get("body", "{}")
                body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw

                system = body.get("system", [])
                messages = body.get("messages", [])
                tools = body.get("tools", [])
                model = body.get("model", "unknown")

                sys_chars = len(json.dumps(system))
                msg_chars = len(json.dumps(messages))
                tool_chars = len(json.dumps(tools))

                sys_tok = sys_chars // 4
                msg_tok = msg_chars // 4
                tool_tok = tool_chars // 4
                total_tok = sys_tok + msg_tok + tool_tok

                ts = raw.get("timestamp", "")[:19]
                sid = raw.get("session_id", "")

                dumps.append({
                    "timestamp": ts,
                    "session_id": sid,
                    "model": model,
                    "system_tokens": sys_tok,
                    "message_tokens": msg_tok,
                    "tool_def_tokens": tool_tok,
                    "total_input_tokens": total_tok,
                    "message_count": len(messages),
                    "tool_count": len(tools),
                })

                if sys_tok > system_prompt_tokens:
                    system_prompt_tokens = sys_tok

                if len(tools) > 0 and tool_tok > tools_total_tokens:
                    tools_total_tokens = tool_tok
                    tool_count = len(tools)
                    tool_costs.clear()
                    for t in tools:
                        name = t.get("name", "unknown")
                        tool_costs[name] = len(json.dumps(t)) // 4

            except Exception:
                continue

    fixed_overhead = system_prompt_tokens + tools_total_tokens

    avg_variable = 0
    if dumps:
        avg_variable = sum(d["message_tokens"] for d in dumps) // len(dumps)
    avg_per_call = fixed_overhead + max(avg_variable, 3000)

    state = get_state_db()
    session_estimates: list[dict] = []
    total_estimated_input = 0
    total_api_calls = 0

    if state:
        try:
            cur = await state.execute(
                """SELECT id, source, message_count, tool_call_count,
                          input_tokens, output_tokens,
                          datetime(started_at, 'unixepoch') as started_at
                   FROM sessions ORDER BY started_at DESC LIMIT 20"""
            )
            rows = await cur.fetchall()
            for r in rows:
                sid, source, msg_count, tc_count = r[0], r[1], r[2] or 0, r[3] or 0
                real_input, real_output = r[4] or 0, r[5] or 0

                api_calls = max((msg_count + 1) // 2, 1) if msg_count > 0 else 1
                est_session_input = api_calls * avg_per_call

                if real_input > 0:
                    est_session_input = real_input

                total_estimated_input += est_session_input
                total_api_calls += api_calls

                session_estimates.append({
                    "id": sid,
                    "source": source,
                    "messages": msg_count,
                    "tool_calls": tc_count,
                    "api_calls_est": api_calls,
                    "est_input_tokens": est_session_input,
                    "real_input_tokens": real_input,
                    "started_at": r[6] or "",
                })
        except Exception:
            pass

    top_tools = sorted(tool_costs.items(), key=lambda x: x[1], reverse=True)

    return {
        "request_dumps": dumps,
        "fixed_overhead": {
            "system_prompt_tokens": system_prompt_tokens,
            "tool_definitions_tokens": tools_total_tokens,
            "tool_count": tool_count,
            "total_fixed": fixed_overhead,
            "pct_of_avg_request": round(fixed_overhead / max(sum(d["total_input_tokens"] for d in dumps) / max(len(dumps), 1), 1) * 100, 1) if dumps else 0,
        },
        "tool_token_costs": [{"name": n, "tokens": t} for n, t in top_tools],
        "session_estimates": session_estimates,
        "totals": {
            "total_estimated_input": total_estimated_input,
            "total_api_calls": total_api_calls,
            "avg_input_per_call": avg_per_call,
            "fixed_overhead_per_call": fixed_overhead,
            "variable_per_call": avg_per_call - fixed_overhead,
        },
    }
