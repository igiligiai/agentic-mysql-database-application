from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from api.utils.config import CONTEXT_DEFAULTS

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
    """Hard-cap the message list to max_messages.

    Always cuts at a HumanMessage boundary so we never hand OpenAI an
    orphaned ToolMessage or a dangling AIMessage(tool_calls) without its
    ToolMessage response — both cause a 400 from the API.
    """
    max_messages: int = int(_settings.get("max_messages", 40))

    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    non_system = [m for m in messages if not isinstance(m, SystemMessage)]

    budget = max_messages - len(system_msgs)
    if len(non_system) <= budget:
        return messages  # nothing to do

    # Naive cut index — we need to trim this many messages from the front
    cut = len(non_system) - budget

    # Advance forward until we land on a HumanMessage so we never start
    # mid-tool-call sequence.
    while cut < len(non_system) and not isinstance(non_system[cut], HumanMessage):
        cut += 1

    # If no HumanMessage found (shouldn't happen in practice), keep everything
    if cut >= len(non_system):
        return messages

    return system_msgs + non_system[cut:]
