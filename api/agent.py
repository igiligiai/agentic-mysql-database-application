from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.semantics.loader import load_semantics, render_semantics
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
from api.utils.errors import MAX_CONSECUTIVE_REPAIRS, is_tool_error
from api.utils.trimmer import trim_messages

BASE_SYSTEM_PROMPT = """You are AutoBase, an expert data intelligence agent connected to a MySQL car database.
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

Current database: Car data warehouse (bronze/silver/gold layers)"""

# The semantic layer is part of the prompt, so changing glossary.yaml or
# exemplars.yaml is a behaviour change. Every glossary term is covered by an
# eval case in tests/eval/cases.yaml.
SYSTEM_PROMPT = f"{BASE_SYSTEM_PROMPT}\n\n{render_semantics(load_semantics())}"

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

    def __post_init__(self) -> None:
        self._langgraph_tools = _build_langgraph_tools(self.run_tool, TOOL_SCHEMA_BY_NAME)

    def run_tool(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = args or {}
        result = dispatch_tool(tool, payload)

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
        api_key = _resolve_api_key()
        model = _resolve_model()
        agent = _get_langgraph_agent(
            agent_cache=self._agent_cache,
            checkpointer=self._checkpointer,
            langgraph_tools=self._langgraph_tools,
            api_key=api_key,
            model=model,
            system_prompt=SYSTEM_PROMPT,
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

        return {
            "reply": reply_text or "Agent finished without a text response.",
            "tool_calls": list(tool_trace_map.values()),
            "session_id": session_id,
        }
