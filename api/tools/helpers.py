from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from fastapi import HTTPException
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from api.utils.db import fetch_all, fetch_matrix


def _safe_tool_content(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _message_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text_value = block.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content or "")


def _resolve_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY in backend env.")
    return api_key


def _resolve_model() -> str:
    model = os.getenv("OPENAI_MODEL", "").strip()
    if not model:
        raise ValueError("OpenAI model is required. Set OPENAI_MODEL in backend env.")
    return model


def build_tools() -> list[Any]:
    @tool(description="Execute a SQL SELECT query against the MySQL database and return results.")
    def execute_sql(query: str, explanation: str) -> str:
        """Run a SELECT query on the MySQL database."""
        query = query.strip()
        if not re.match(r"^SELECT\s", query, flags=re.IGNORECASE):
            raise HTTPException(status_code=400, detail="Only SELECT queries are permitted.")
        start = time.perf_counter()
        columns, rows = fetch_matrix(query)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return json.dumps({
            "query": query,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "duration_ms": duration_ms,
        })

    @tool(description="Get the full database schema including all tables and columns.")
    def get_schema() -> str:
        """Return full database schema."""
        cols = fetch_all(
            """
            SELECT
              c.TABLE_NAME,
              c.COLUMN_NAME,
              c.DATA_TYPE,
              c.COLUMN_TYPE,
              c.IS_NULLABLE,
              c.COLUMN_KEY,
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
            SELECT TABLE_NAME, COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
            """
        )
        fk_set = {f"{fk['TABLE_NAME']}.{fk['COLUMN_NAME']}" for fk in fks}

        tables: dict[str, dict[str, Any]] = {}
        for col in cols:
            tname = col["TABLE_NAME"]
            if tname not in tables:
                tables[tname] = {
                    "name": tname,
                    "type": "VIEW" if col["TABLE_TYPE"] == "VIEW" else "TABLE",
                    "columns": [],
                }
            is_fk = f"{tname}.{col['COLUMN_NAME']}" in fk_set
            key = ""
            if col["COLUMN_KEY"] == "PRI":
                key = "PK"
            elif is_fk:
                key = "FK"
            elif col["COLUMN_KEY"] in {"UNI", "MUL"}:
                key = "IDX"
            tables[tname]["columns"].append({
                "name": col["COLUMN_NAME"],
                "type": col.get("COLUMN_TYPE") or col.get("DATA_TYPE"),
                "key": key,
                "nullable": col["IS_NULLABLE"] == "YES",
            })
        return json.dumps({"schema": list(tables.values())})

    return [execute_sql, get_schema]


def _get_langgraph_agent(
    agent_cache: dict[tuple[str, str], Any],
    checkpointer: Any,
    langgraph_tools: list[Any],
    api_key: str,
    model: str,
    system_prompt: str,
) -> Any:
    cache_key = (api_key, model)
    cached = agent_cache.get(cache_key)
    if cached is not None:
        return cached

    llm = ChatOpenAI(model=model, api_key=api_key, temperature=0)
    agent = create_react_agent(
        model=llm,
        tools=langgraph_tools,
        checkpointer=checkpointer,
        prompt=system_prompt,
    )
    agent_cache[cache_key] = agent
    return agent

