from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.semantics.loader import get_semantic_prompt_block
from api.tools import dispatch_tool
from api.tools.helpers import (
    _build_langgraph_tools,
    _get_langgraph_agent,
    _load_tools,
    _message_to_text,
    _resolve_api_key,
    _resolve_model,
    _safe_tool_content,
)
from api.utils import telemetry
from api.utils.errors import MAX_CONSECUTIVE_REPAIRS, is_tool_error
from api.utils.trimmer import trim_messages

BASE_SYSTEM_PROMPT = """You are AutoBase, an expert data intelligence agent connected to a MySQL database.
You help users query data, explore schema, trace table lineage, and understand their data.

Your capabilities:
- Translate natural language to SQL and execute queries
- Describe tables, views, and their relationships
- Generate lineage graphs showing data flow (bronze -> silver -> gold pattern)
- Run data quality checks
- Detect schema changes and anomalies

Guidelines:
- Always use tools to fetch real data; never fabricate results
- When writing SQL, prefer clean, readable queries
- For lineage questions, use get_table_lineage
- For schema questions, use get_schema or describe_table
- Format results clearly. Reference column names in backticks
- If a query might be slow, warn the user first
- Be concise but thorough

Current database: connected via live schema introspection (bronze/silver/gold layers)"""

# The semantic block is built from a live information_schema introspection,
# not curated YAML, so it is computed lazily on first use rather than at
# import time: introspecting eagerly at import would make a database outage
# break module import instead of just degrading the prompt. Memoised in
# api.semantics.loader so introspection does not re-run per request.
_system_prompt: str | None = None


def _get_system_prompt() -> str:
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = f"{BASE_SYSTEM_PROMPT}\n\n{get_semantic_prompt_block()}"
    return _system_prompt


TOOLS_FILE_PATH = Path(__file__).resolve().parent / "utils" / "tools.json"


TOOL_SCHEMAS = _load_tools(TOOLS_FILE_PATH)
TOOL_SCHEMA_BY_NAME = {
    item["function"]["name"]: item["function"]
    for item in TOOL_SCHEMAS
    if isinstance(item, dict) and isinstance(item.get("function"), dict)
}


@dataclass
class AgenticWorkflow:
    """Core orchestration layer for tool-based agent actions."""

    max_steps: int = 8
    history: list[dict[str, Any]] = field(default_factory=list)
    _checkpointer: InMemorySaver = field(default_factory=InMemorySaver, init=False, repr=False)
    _agent_cache: dict[tuple[str, str], Any] = field(default_factory=dict, init=False, repr=False)
    _langgraph_tools: list[Any] = field(default_factory=list, init=False, repr=False)
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _tool_spans: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._langgraph_tools = _build_langgraph_tools(self.run_tool, TOOL_SCHEMA_BY_NAME)

    def run_tool(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = args or {}
        start = time.perf_counter()
        result = dispatch_tool(tool, payload)
        self._tool_spans.append(
            {
                "name": tool,
                "duration_ms": int((time.perf_counter() - start) * 1000),
                "ok": not is_tool_error(result),
                "error_code": result.get("code") if is_tool_error(result) else None,
            }
        )

        if is_tool_error(result) and result.get("retryable"):
            self._consecutive_failures += 1
            if self._consecutive_failures > MAX_CONSECUTIVE_REPAIRS:
                # The loop is not converging. Stop it spending the rest of its
                # step budget guessing, and hand the problem back to the user.
                result = dict(result)
                result["retryable"] = False
                result["hint"] = (
                    "Repeated attempts have failed. Stop retrying and ask the user "
                    "to clarify what they want."
                )
        elif not is_tool_error(result):
            self._consecutive_failures = 0

        self.history.append({"tool": tool, "args": payload, "result": result})
        return result

    def run_chat(
        self,
        user_message: str,
        session_id: str = "default",
    ) -> dict[str, Any]:
        self._consecutive_failures = 0
        self._tool_spans = []
        turn_started = time.perf_counter()
        api_key = _resolve_api_key()
        model = _resolve_model()
        agent = _get_langgraph_agent(
            agent_cache=self._agent_cache,
            checkpointer=self._checkpointer,
            langgraph_tools=self._langgraph_tools,
            api_key=api_key,
            model=model,
            system_prompt=_get_system_prompt(),
        )
        config = {
            "configurable": {
                "thread_id": session_id,
            },
            "recursion_limit": self.max_steps * 2,
        }

        existing_messages_count = 0
        try:
            prior_state = agent.get_state(config)
            if prior_state and isinstance(prior_state.values, dict):
                prior_messages = prior_state.values.get("messages")
                if isinstance(prior_messages, list):
                    existing_messages_count = len(prior_messages)

                    # ── Trim context window before sending new message ──
                    trimmed = trim_messages(prior_messages)
                    if len(trimmed) < len(prior_messages):
                        agent.update_state(
                            config,
                            {"messages": trimmed},
                        )
                        existing_messages_count = len(trimmed)
        except Exception:
            existing_messages_count = 0

        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config=config,
        )

        all_messages = result.get("messages", [])
        if not isinstance(all_messages, list):
            all_messages = []
        new_messages = all_messages[existing_messages_count:]

        tool_trace_map: dict[str, dict[str, Any]] = {}
        reply_text = ""

        for message in new_messages:
            if isinstance(message, AIMessage):
                if message.tool_calls:
                    for call in message.tool_calls:
                        call_id = str(call.get("id") or "")
                        if not call_id:
                            continue
                        tool_trace_map[call_id] = {
                            "name": str(call.get("name") or ""),
                            "args": call.get("args") or {},
                            "result": None,
                        }
                else:
                    text = _message_to_text(message.content)
                    if text.strip():
                        reply_text = text

            if isinstance(message, ToolMessage):
                tool_call_id = str(getattr(message, "tool_call_id", "") or "")
                if tool_call_id and tool_call_id in tool_trace_map:
                    tool_trace_map[tool_call_id]["result"] = _safe_tool_content(message.content)

        if not reply_text:
            for message in reversed(all_messages):
                if isinstance(message, AIMessage):
                    text = _message_to_text(message.content)
                    if text.strip():
                        reply_text = text
                        break

        input_tokens = 0
        output_tokens = 0
        for message in new_messages:
            usage = getattr(message, "usage_metadata", None)
            if isinstance(usage, dict):
                input_tokens += int(usage.get("input_tokens") or 0)
                output_tokens += int(usage.get("output_tokens") or 0)

        failed_spans = [s for s in self._tool_spans if not s["ok"]]
        telemetry.record_turn(
            {
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_ms": int((time.perf_counter() - turn_started) * 1000),
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "est_cost": telemetry.estimate_cost(model, input_tokens, output_tokens),
                "tool_spans": list(self._tool_spans),
                "repairs": len(failed_spans),
                "error_code": failed_spans[-1]["error_code"] if failed_spans else None,
            }
        )

        return {
            "reply": reply_text or "Agent finished without a text response.",
            "tool_calls": list(tool_trace_map.values()),
            "session_id": session_id,
        }
