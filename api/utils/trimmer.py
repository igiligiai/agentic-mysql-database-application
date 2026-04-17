from __future__ import annotations

import copy
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from api.utils.config import CONTEXT_DEFAULTS

# Runtime-mutable settings (changed via /context-config endpoint)
_settings: dict[str, Any] = dict(CONTEXT_DEFAULTS)


def get_trim_settings() -> dict[str, Any]:
    return dict(_settings)


def update_trim_settings(patch: dict[str, Any]) -> dict[str, Any]:
    allowed = set(CONTEXT_DEFAULTS.keys())
    for key, value in patch.items():
        if key in allowed:
            _settings[key] = value
    return dict(_settings)


def trim_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Trim a message list to fit within the configured context window.

    Strategy:
    1. Always preserve the system message(s) at the front.
    2. Truncate oversized tool results to ``max_tool_result_chars``.
    3. Keep only the last ``keep_last_n_pairs`` user/AI conversational turns
       (each turn = user msg + AI response + any tool calls/results in between).
    4. Hard-cap at ``max_messages`` total.
    """
    if not _settings.get("trim_enabled", True):
        return messages

    max_messages: int = int(_settings.get("max_messages", 40))
    max_tool_chars: int = int(_settings.get("max_tool_result_chars", 4000))
    keep_pairs: int = int(_settings.get("keep_last_n_pairs", 10))
    keep_system: bool = bool(_settings.get("keep_system", True))

    # ── Step 1: separate system messages ────────────────────
    system_msgs: list[BaseMessage] = []
    non_system: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, SystemMessage) and keep_system:
            system_msgs.append(msg)
        else:
            non_system.append(msg)

    # ── Step 2: truncate tool results ───────────────────────
    trimmed: list[BaseMessage] = []
    for msg in non_system:
        if isinstance(msg, ToolMessage) and isinstance(msg.content, str) and len(msg.content) > max_tool_chars:
            shortened = copy.copy(msg)
            shortened.content = msg.content[:max_tool_chars] + f"\n... [truncated from {len(msg.content)} chars]"
            trimmed.append(shortened)
        else:
            trimmed.append(msg)

    # ── Step 3: keep last N conversational turns ────────────
    # Walk backwards and count user messages as turn boundaries.
    if keep_pairs > 0:
        turns_seen = 0
        cut_index = len(trimmed)
        for i in range(len(trimmed) - 1, -1, -1):
            if isinstance(trimmed[i], HumanMessage):
                turns_seen += 1
                if turns_seen >= keep_pairs:
                    cut_index = i
                    break
        trimmed = trimmed[cut_index:]

    # ── Step 4: hard-cap total messages ─────────────────────
    budget = max_messages - len(system_msgs)
    if len(trimmed) > budget:
        trimmed = trimmed[-budget:]

    # Ensure we don't start with an orphaned ToolMessage (no preceding AI call)
    while trimmed and isinstance(trimmed[0], ToolMessage):
        trimmed.pop(0)

    return system_msgs + trimmed
