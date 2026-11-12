"""Tools that execute SQL. Separated from handlers.py because these are the
only tools that run agent-authored text against the database.
"""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import HTTPException

from api.utils.db import fetch_matrix


def execute_sql_tool(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not re.match(r"^SELECT\s", query, flags=re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Only SELECT queries are permitted.")

    start = time.perf_counter()
    columns, rows = fetch_matrix(query)
    duration_ms = int((time.perf_counter() - start) * 1000)

    return {
        "query": query,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "duration_ms": duration_ms,
    }
