"""SQLite database layer: dashboard.db (read-write) + state.db (read-only)."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from .config import DASHBOARD_DB, STATE_DB
from .pricing import calculate_cost

DASHBOARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    model       TEXT    NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL    NOT NULL DEFAULT 0,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    status_code INTEGER NOT NULL DEFAULT 200,
    is_streaming INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    error_type  TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_ts ON api_requests(ts);
CREATE INDEX IF NOT EXISTS idx_requests_model ON api_requests(model);
CREATE INDEX IF NOT EXISTS idx_requests_status ON api_requests(status_code);
"""


_dashboard_db: aiosqlite.Connection | None = None
_state_db: aiosqlite.Connection | None = None
_lock = asyncio.Lock()


async def init_dashboard_db() -> None:
    global _dashboard_db
    DASHBOARD_DB.parent.mkdir(parents=True, exist_ok=True)
    _dashboard_db = await aiosqlite.connect(str(DASHBOARD_DB))
    _dashboard_db.row_factory = aiosqlite.Row
    await _dashboard_db.executescript(DASHBOARD_SCHEMA)
    await _dashboard_db.execute("PRAGMA journal_mode=WAL")
    await _dashboard_db.execute("PRAGMA busy_timeout=5000")
    await _dashboard_db.commit()


async def init_state_db() -> None:
    global _state_db
    if not STATE_DB.exists():
        return
    _state_db = await aiosqlite.connect(str(STATE_DB), uri=True)
    _state_db.row_factory = aiosqlite.Row
    await _state_db.execute("PRAGMA query_only=ON")


async def close_dbs() -> None:
    global _dashboard_db, _state_db
    if _dashboard_db:
        await _dashboard_db.close()
        _dashboard_db = None
    if _state_db:
        await _state_db.close()
        _state_db = None


def get_dashboard_db() -> aiosqlite.Connection:
    assert _dashboard_db is not None, "dashboard DB not initialised"
    return _dashboard_db


def get_state_db() -> aiosqlite.Connection | None:
    return _state_db


async def log_request(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    latency_ms: int = 0,
    status_code: int = 200,
    is_streaming: bool = False,
    stop_reason: str | None = None,
    error_type: str | None = None,
) -> None:
    db = get_dashboard_db()
    cost = calculate_cost(model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens)
    await db.execute(
        """INSERT INTO api_requests
           (model, input_tokens, output_tokens, cache_creation_tokens,
            cache_read_tokens, cost_usd, latency_ms, status_code,
            is_streaming, stop_reason, error_type)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            model, input_tokens, output_tokens, cache_creation_tokens,
            cache_read_tokens, cost, latency_ms, status_code,
            1 if is_streaming else 0, stop_reason, error_type,
        ),
    )
    await db.commit()
