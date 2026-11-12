from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from api.tools.helpers import infer_layer, validate_identifier
from api.tools.sql_tools import execute_sql_tool
from api.utils.db import fetch_all, fetch_one


def get_schema_tool() -> dict[str, Any]:
    cols = fetch_all(
        """
        SELECT
          c.TABLE_NAME,
          c.COLUMN_NAME,
          c.DATA_TYPE,
          c.COLUMN_TYPE,
          c.IS_NULLABLE,
          c.COLUMN_KEY,
          c.COLUMN_DEFAULT,
          c.EXTRA,
          t.TABLE_TYPE
        FROM information_schema.COLUMNS c
        JOIN information_schema.TABLES t
          ON c.TABLE_NAME = t.TABLE_NAME AND c.TABLE_SCHEMA = t.TABLE_SCHEMA
        WHERE c.TABLE_SCHEMA = DATABASE()
        ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
        """
    )

    fks = fetch_all(
        """
        SELECT
          TABLE_NAME,
          COLUMN_NAME,
          REFERENCED_TABLE_NAME,
          REFERENCED_COLUMN_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
        """
    )
    fk_set = {f"{fk['TABLE_NAME']}.{fk['COLUMN_NAME']}" for fk in fks}

    tables: dict[str, dict[str, Any]] = {}
    for col in cols:
        table_name = col["TABLE_NAME"]
        if table_name not in tables:
            tables[table_name] = {
                "name": table_name,
                "type": "VIEW" if col["TABLE_TYPE"] == "VIEW" else "TABLE",
                "layer": infer_layer(table_name),
                "columns": [],
            }

        is_fk = f"{table_name}.{col['COLUMN_NAME']}" in fk_set
        key = ""
        if col["COLUMN_KEY"] == "PRI":
            key = "PK"
        elif is_fk:
            key = "FK"
        elif col["COLUMN_KEY"] in {"UNI", "MUL"}:
            key = "IDX"

        tables[table_name]["columns"].append(
            {
                "name": col["COLUMN_NAME"],
                "type": col.get("COLUMN_TYPE") or col.get("DATA_TYPE"),
                "key": key,
                "nullable": col["IS_NULLABLE"] == "YES",
            }
        )

    return {"schema": list(tables.values())}


def get_table_lineage_tool(args: dict[str, Any]) -> dict[str, Any]:
    table_name = str(args.get("table_name") or "all")

    tables = fetch_all(
        """
        SELECT TABLE_NAME AS name, TABLE_TYPE AS type
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
        """
    )
    views = fetch_all(
        """
        SELECT TABLE_NAME, VIEW_DEFINITION
        FROM information_schema.VIEWS
        WHERE TABLE_SCHEMA = DATABASE()
        """
    )
    fks = fetch_all(
        """
        SELECT TABLE_NAME, REFERENCED_TABLE_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
        GROUP BY TABLE_NAME, REFERENCED_TABLE_NAME
        """
    )

    nodes = [
        {
            "id": t["name"],
            "type": "VIEW" if t["type"] == "VIEW" else "TABLE",
            "layer": infer_layer(t["name"]),
            "desc": "Database view" if t["type"] == "VIEW" else "Base table",
        }
        for t in tables
    ]

    edges: list[dict[str, str]] = []
    dedupe: set[tuple[str, str, str]] = set()

    for fk in fks:
        edge = (fk["REFERENCED_TABLE_NAME"], fk["TABLE_NAME"], "FK")
        if edge not in dedupe:
            dedupe.add(edge)
            edges.append({"source": edge[0], "target": edge[1], "label": edge[2]})

    for view in views:
        view_name = view["TABLE_NAME"]
        view_def = (view.get("VIEW_DEFINITION") or "").upper()
        label = "JOIN" if "JOIN" in view_def else "SELECT"
        for t in tables:
            source = t["name"]
            if source == view_name:
                continue
            if source.upper() in view_def:
                edge = (source, view_name, label)
                if edge not in dedupe:
                    dedupe.add(edge)
                    edges.append({"source": edge[0], "target": edge[1], "label": edge[2]})

    if table_name and table_name != "all":
        connected = {table_name}
        for edge in edges:
            if edge["source"] == table_name:
                connected.add(edge["target"])
            if edge["target"] == table_name:
                connected.add(edge["source"])

        nodes = [node for node in nodes if node["id"] in connected]
        edges = [
            edge
            for edge in edges
            if edge["source"] in connected and edge["target"] in connected
        ]

    return {"lineage": {"nodes": nodes, "edges": edges}}


def describe_table_tool(args: dict[str, Any]) -> dict[str, Any]:
    table_name = str(args.get("table_name") or "").strip()
    if not table_name:
        raise HTTPException(status_code=400, detail="table_name is required")

    safe_table = validate_identifier(table_name, "table name")
    count_row = fetch_one(f"SELECT COUNT(*) AS cnt FROM {safe_table}") or {"cnt": 0}
    sample_rows = fetch_all(f"SELECT * FROM {safe_table} LIMIT 3")
    table_info = fetch_one(
        """
        SELECT UPDATE_TIME
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table_name,),
    )

    return {
        "table_name": table_name,
        "row_count": count_row.get("cnt", 0),
        "last_updated": (table_info or {}).get("UPDATE_TIME") or "Unknown",
        "sample_data": sample_rows,
        "layer": infer_layer(table_name),
    }


def check_data_quality_tool(args: dict[str, Any]) -> dict[str, Any]:
    table_name = str(args.get("table_name") or "").strip()
    if not table_name:
        raise HTTPException(status_code=400, detail="table_name is required")

    safe_table = validate_identifier(table_name, "table name")
    cols = fetch_all(
        """
        SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    total_row = fetch_one(f"SELECT COUNT(*) AS cnt FROM {safe_table}") or {"cnt": 0}
    total = int(total_row.get("cnt", 0) or 0)

    checks: list[dict[str, Any]] = []
    for col in cols:
        if col["IS_NULLABLE"] != "YES":
            continue

        col_name = col["COLUMN_NAME"]
        safe_col = validate_identifier(col_name, "column name")
        null_row = fetch_one(
            f"SELECT COUNT(*) AS nulls FROM {safe_table} WHERE {safe_col} IS NULL"
        ) or {"nulls": 0}
        nulls = int(null_row.get("nulls", 0) or 0)

        if nulls > 0:
            pct = (nulls * 100.0 / total) if total else 0
            checks.append(
                {
                    "name": f"Nulls: {col_name}",
                    "status": "warn" if pct > 10 else "ok",
                    "detail": f"{nulls} null rows ({pct:.1f}%)",
                }
            )

    pk_col = fetch_one(
        """
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_KEY = 'PRI'
        LIMIT 1
        """,
        (table_name,),
    )
    if pk_col:
        pk_name = pk_col["COLUMN_NAME"]
        safe_pk = validate_identifier(pk_name, "primary key")
        dup_row = fetch_one(
            f"SELECT COUNT(*) - COUNT(DISTINCT {safe_pk}) AS dups FROM {safe_table}"
        ) or {"dups": 0}
        dups = int(dup_row.get("dups", 0) or 0)
        checks.append(
            {
                "name": "Duplicate primary keys",
                "status": "error" if dups > 0 else "ok",
                "detail": f"{dups} duplicate keys found!" if dups > 0 else "No duplicates",
            }
        )

    checks.append(
        {
            "name": "Row count",
            "status": "ok",
            "detail": f"{total:,} total rows",
        }
    )
    return {"table": table_name, "checks": checks}


def dispatch_tool(tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = args or {}

    if tool == "execute_sql":
        return execute_sql_tool(payload)
    if tool == "get_schema":
        return get_schema_tool()
    if tool == "get_table_lineage":
        return get_table_lineage_tool(payload)
    if tool == "describe_table":
        return describe_table_tool(payload)
    if tool == "check_data_quality":
        return check_data_quality_tool(payload)

    return {"error": f"Unknown tool: {tool}"}
