from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from mysql.connector import pooling

from .config import DB_CONFIG, QUERY_DEFAULTS


def _sanitize(value: Any) -> Any:
    """Convert MySQL-specific types to JSON-safe primitives."""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _sanitize(v) for k, v in row.items()}


def _sanitize_list(row: list[Any]) -> list[Any]:
    return [_sanitize(v) for v in row]

_pool: pooling.MySQLConnectionPool | None = None


def get_pool() -> pooling.MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(pool_name="autobase", pool_size=10, **DB_CONFIG)
    return _pool


def _apply_session_limits(cursor: Any) -> None:
    """Cap how long a SELECT may run on this pooled connection.

    MySQL's max_execution_time applies only to read-only SELECTs, which is
    exactly the surface the agent can reach.
    """
    timeout_ms = int(QUERY_DEFAULTS["statement_timeout_ms"])
    cursor.execute(f"SET SESSION max_execution_time = {timeout_ms}")


def fetch_all(query: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    conn = get_pool().get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        _apply_session_limits(cursor)
        cursor.execute(query, params or ())
        return [_sanitize_row(r) for r in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()


def fetch_one(query: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    rows = fetch_all(query, params)
    return rows[0] if rows else None


def fetch_matrix(query: str) -> tuple[list[str], list[list[Any]]]:
    conn = get_pool().get_connection()
    cursor = conn.cursor()
    try:
        _apply_session_limits(cursor)
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [col[0] for col in (cursor.description or [])]
        return columns, [_sanitize_list(list(r)) for r in rows]
    finally:
        cursor.close()
        conn.close()
