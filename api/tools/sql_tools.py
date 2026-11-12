"""Tools that execute SQL. Separated from handlers.py because these are the
only tools that run agent-authored text against the database.

These tools return ToolError dicts rather than raising, so the ReAct loop can
read a failure and repair it. api/app.py converts them back into HTTP errors
for the non-LLM /tool callers.
"""

from __future__ import annotations

import json
import time
from typing import Any

from mysql.connector import Error as MySQLError

from api.utils.config import DB_CONFIG, QUERY_DEFAULTS
from api.utils.db import fetch_all, fetch_matrix
from api.utils.errors import from_mysql_error, tool_error
from api.utils.sql_guard import GuardRejection, validate_query


def _allowed_schemas() -> frozenset[str]:
    name = str(DB_CONFIG.get("database") or "").strip()
    return frozenset({name}) if name else frozenset()


def execute_sql_tool(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return tool_error("TOOL_BAD_ARGS", detail="The 'query' argument is required.")

    guarded = validate_query(
        query,
        max_rows=int(QUERY_DEFAULTS["max_rows"]),
        allowed_schemas=_allowed_schemas(),
    )
    if isinstance(guarded, GuardRejection):
        return tool_error(guarded.code, detail=guarded.reason)

    start = time.perf_counter()
    try:
        columns, rows = fetch_matrix(guarded.sql)
    except MySQLError as exc:
        return from_mysql_error(exc)
    duration_ms = int((time.perf_counter() - start) * 1000)

    return {
        "query": guarded.sql,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "duration_ms": duration_ms,
        "limit_injected": guarded.limit_injected,
        "limit_clamped": guarded.limit_clamped,
    }


def _walk_plan(node: Any, found: list[dict[str, Any]]) -> None:
    """Collect every dict in the EXPLAIN tree that describes a table access."""
    if isinstance(node, dict):
        if "access_type" in node:
            found.append(node)
        for value in node.values():
            _walk_plan(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_plan(item, found)


def explain_query_tool(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return tool_error("TOOL_BAD_ARGS", detail="The 'query' argument is required.")

    guarded = validate_query(
        query,
        max_rows=int(QUERY_DEFAULTS["max_rows"]),
        allowed_schemas=_allowed_schemas(),
    )
    if isinstance(guarded, GuardRejection):
        return tool_error(guarded.code, detail=guarded.reason)

    try:
        rows = fetch_all(f"EXPLAIN FORMAT=JSON {guarded.sql}")
    except MySQLError as exc:
        return from_mysql_error(exc)

    raw = ""
    if rows:
        raw = str(next(iter(rows[0].values()), "") or "")
    try:
        plan = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        plan = {}

    accesses: list[dict[str, Any]] = []
    _walk_plan(plan, accesses)

    rows_examined = sum(
        int(a.get("rows_examined_per_scan") or 0) for a in accesses
    )
    full_scan = any(str(a.get("access_type") or "").upper() == "ALL" for a in accesses)

    cost_raw = (plan.get("query_block") or {}).get("cost_info", {}).get("query_cost")
    try:
        estimated_cost = float(cost_raw) if cost_raw is not None else None
    except (TypeError, ValueError):
        estimated_cost = None

    return {
        "query": guarded.sql,
        "estimated_cost": estimated_cost,
        "rows_examined": rows_examined,
        "full_scan": full_scan,
        "plan": plan,
    }
