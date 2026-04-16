from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from api.tools import dispatch_tool

SYSTEM_PROMPT = """You are AutoBase, an expert data intelligence agent connected to a MySQL car database.
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

TOOLS_FILE_PATH = Path(__file__).resolve().parent / "utils" / "tools.json"


def _load_tools() -> list[dict[str, Any]]:
    with TOOLS_FILE_PATH.open("r", encoding="utf-8") as tools_file:
        loaded = json.load(tools_file)
    if not isinstance(loaded, list):
        raise ValueError("tools.json must contain a JSON array")
    return loaded


TOOLS = _load_tools()


@dataclass
class AgentSession:
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgenticWorkflow:
    """Core orchestration layer for tool-based agent actions."""

    max_steps: int = 8
    history: list[dict[str, Any]] = field(default_factory=list)
    sessions: dict[str, AgentSession] = field(default_factory=dict)

    def run_tool(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = args or {}
        result = dispatch_tool(tool, payload)
        self.history.append({"tool": tool, "args": payload, "result": result})
        return result

    def _resolve_api_key(self, override_key: str | None = None) -> str:
        api_key = (override_key or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "OpenAI API key is required. Set OPENAI_API_KEY or pass openai_api_key in request."
            )
        return api_key

    def _get_session(self, session_id: str) -> AgentSession:
        if session_id not in self.sessions:
            self.sessions[session_id] = AgentSession()
        return self.sessions[session_id]

    def run_chat(
        self,
        user_message: str,
        session_id: str = "default",
        openai_api_key: str | None = None,
        model: str = "gpt-4o",
    ) -> dict[str, Any]:
        api_key = self._resolve_api_key(openai_api_key)
        session = self._get_session(session_id)
        client = OpenAI(api_key=api_key)

        session.messages.append({"role": "user", "content": user_message})
        tool_trace: list[dict[str, Any]] = []

        for _ in range(self.max_steps):
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *session.messages],
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=2000,
            )

            message = completion.choices[0].message
            assistant_entry: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or "",
            }

            if message.tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in message.tool_calls
                ]

            session.messages.append(assistant_entry)

            if not message.tool_calls:
                return {
                    "reply": message.content or "",
                    "tool_calls": tool_trace,
                    "session_id": session_id,
                }

            for tool_call in message.tool_calls:
                arguments = json.loads(tool_call.function.arguments or "{}")
                result = self.run_tool(tool_call.function.name, arguments)

                tool_trace.append(
                    {
                        "name": tool_call.function.name,
                        "args": arguments,
                        "result": result,
                    }
                )

                session.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    }
                )

        return {
            "reply": "Agent reached maximum steps. Please try a more specific question.",
            "tool_calls": tool_trace,
            "session_id": session_id,
        }
