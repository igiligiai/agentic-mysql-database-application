from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InvalidIdentifierError(Exception):
    """Raised by validate_identifier for a malformed table/column name.

    A dedicated exception (not HTTPException) so handlers can convert it into
    a ToolError the agent can act on, instead of it propagating as an HTTP
    error that kills the whole chat turn.
    """


def infer_layer(table_name: str) -> str:
    n = table_name.lower()
    if "bronze" in n or "raw" in n or "landing" in n or "stg_" in n:
        return "bronze"
    if "silver" in n or "_clean" in n or "_typed" in n:
        return "silver"
    if "gold" in n or "vw_" in n or "_summary" in n or "_report" in n:
        return "gold"
    if n.startswith("vw"):
        return "gold"
    return "silver"


def validate_identifier(name: str, label: str = "identifier") -> str:
    if not name or not _IDENTIFIER_RE.fullmatch(name):
        raise InvalidIdentifierError(f"Invalid {label}: {name!r}")
    return f"`{name}`"


def _load_tools(tools_file_path: Path) -> list[dict[str, Any]]:
    with tools_file_path.open("r", encoding="utf-8") as tools_file:
        loaded = json.load(tools_file)
    if not isinstance(loaded, list):
        raise ValueError("tools.json must contain a JSON array")
    return loaded


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


def _tool_description(
    tool_schema_by_name: dict[str, dict[str, Any]], tool_name: str, fallback: str
) -> str:
    return str(tool_schema_by_name.get(tool_name, {}).get("description") or fallback)


def _build_langgraph_tools(
    run_tool_fn: Callable[[str, dict[str, Any] | None], dict[str, Any]],
    tool_schema_by_name: dict[str, dict[str, Any]],
) -> list[Any]:
    @tool(
        description=_tool_description(
            tool_schema_by_name,
            "execute_sql",
            "Execute SELECT SQL.",
        )
    )
    def execute_sql(query: str, explanation: str) -> str:
        """Run a SELECT query on the MySQL database."""

        result = run_tool_fn("execute_sql", {"query": query, "explanation": explanation})
        return json.dumps(result)

    @tool(
        description=_tool_description(
            tool_schema_by_name,
            "explain_query",
            "Estimate the cost of a SELECT query without running it.",
        )
    )
    def explain_query(query: str) -> str:
        """Return the optimizer's plan and cost estimate for a SELECT."""

        result = run_tool_fn("explain_query", {"query": query})
        return json.dumps(result)

    @tool(description=_tool_description(tool_schema_by_name, "get_schema", "Get database schema."))
    def get_schema() -> str:
        """Return full database schema."""

        result = run_tool_fn("get_schema", {})
        return json.dumps(result)

    @tool(
        description=_tool_description(
            tool_schema_by_name,
            "get_table_lineage",
            "Get table lineage.",
        )
    )
    def get_table_lineage(table_name: str) -> str:
        """Return lineage graph for a table or all."""

        result = run_tool_fn("get_table_lineage", {"table_name": table_name})
        return json.dumps(result)

    @tool(description=_tool_description(tool_schema_by_name, "describe_table", "Describe table."))
    def describe_table(table_name: str) -> str:
        """Describe a table with sample rows and metadata."""

        result = run_tool_fn("describe_table", {"table_name": table_name})
        return json.dumps(result)

    @tool(
        description=_tool_description(
            tool_schema_by_name,
            "check_data_quality",
            "Check data quality.",
        )
    )
    def check_data_quality(table_name: str) -> str:
        """Run quality checks for a table."""

        result = run_tool_fn("check_data_quality", {"table_name": table_name})
        return json.dumps(result)

    return [
        execute_sql,
        explain_query,
        get_schema,
        get_table_lineage,
        describe_table,
        check_data_quality,
    ]


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
