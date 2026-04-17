from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.tools.helpers import (
    _get_langgraph_agent,
    _message_to_text,
    _resolve_api_key,
    _resolve_model,
    _safe_tool_content,
    build_tools,
)
from api.utils.trimmer import trim_messages

SYSTEM_PROMPT = """You are AutoBase, an expert data intelligence agent connected to a MySQL database.
You help users query data and explore the schema.

Your capabilities:
- Translate natural language to SQL and execute queries
- Retrieve the full database schema

Guidelines:
- Always use tools to fetch real data; never fabricate results
- When writing SQL, prefer clean, readable queries
- For schema questions, use get_schema first
- Format results clearly. Reference column names in backticks
- If a query might be slow, warn the user first
- Be concise but thorough"""


@dataclass
class AgenticWorkflow:
    """Core orchestration layer for tool-based agent actions."""

    max_steps: int = 8
    _checkpointer: InMemorySaver = field(default_factory=InMemorySaver, init=False, repr=False)
    _agent_cache: dict[tuple[str, str], Any] = field(default_factory=dict, init=False, repr=False)
    _langgraph_tools: list[Any] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._langgraph_tools = build_tools()

    def run_chat(
        self,
        user_message: str,
        session_id: str = "default",
    ) -> dict[str, Any]:
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
