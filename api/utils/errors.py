"""The single error shape tools return instead of raising.

Returning a typed dict rather than raising lets the ReAct loop see a failure as
a ToolMessage it can act on. This narrowly relaxes the "never return exceptions
in tool results" rule in copilot.instructions.md §15, for this one controlled
shape only -- nothing here may ever carry a traceback or a credential.
"""

from __future__ import annotations

import re
from typing import Any

MAX_CONSECUTIVE_REPAIRS = 2

RETRYABLE_CODES: frozenset[str] = frozenset(
    {"SQL_SYNTAX", "SQL_UNKNOWN_TABLE", "SQL_UNKNOWN_COLUMN", "SQL_TIMEOUT", "TOOL_BAD_ARGS"}
)

_FALLBACK_CODE = "SQL_FAILED"

# code -> (default message, default hint, forwards the raw driver message?)
_CATALOG: dict[str, tuple[str, str, bool]] = {
    "SQL_SYNTAX": (
        "The SQL could not be parsed.",
        "Fix the syntax and try once more.",
        True,
    ),
    "SQL_UNKNOWN_TABLE": (
        "The query references a table that does not exist.",
        "Call get_schema to list the real tables before retrying.",
        True,
    ),
    "SQL_UNKNOWN_COLUMN": (
        "The query references a column that does not exist.",
        "Call describe_table on the table before retrying.",
        True,
    ),
    "SQL_TIMEOUT": (
        "The query took too long and was stopped.",
        "Add a WHERE filter or reduce the range, then try again.",
        False,
    ),
    "SQL_FORBIDDEN": (
        "The query was blocked by the read-only safety guard.",
        "Do not retry. Explain the restriction to the user.",
        True,
    ),
    "TOOL_BAD_ARGS": (
        "The tool was called with missing or malformed arguments.",
        "Check the tool's required arguments and call it again.",
        True,
    ),
    "SQL_UNAVAILABLE": (
        "The database is not reachable right now.",
        "Do not retry. Tell the user the database is unavailable.",
        False,
    ),
    _FALLBACK_CODE: (
        "The query could not be completed.",
        "Do not retry. Report the failure to the user.",
        False,
    ),
}

_ERRNO_TO_CODE: dict[int, str] = {
    1064: "SQL_SYNTAX",
    1149: "SQL_SYNTAX",
    1146: "SQL_UNKNOWN_TABLE",
    1109: "SQL_UNKNOWN_TABLE",
    1054: "SQL_UNKNOWN_COLUMN",
    1051: "SQL_UNKNOWN_TABLE",
    3024: "SQL_TIMEOUT",
    1317: "SQL_TIMEOUT",
    1045: "SQL_UNAVAILABLE",
    1044: "SQL_UNAVAILABLE",
    2003: "SQL_UNAVAILABLE",
    2006: "SQL_UNAVAILABLE",
    2013: "SQL_UNAVAILABLE",
}

# Anything resembling a credential, host, or source path is removed before the
# text can reach a tool result. Belt and braces: _CATALOG already decides which
# codes may forward a driver message at all.
_REDACTIONS: tuple[str, ...] = (
    r"'[^']*'@'[^']*'",
    r"(?i)using password:[^)]*",
    r"(?i)[a-z]:\\[^\s\"']+",
    r"/[\w./-]+\.py",
    r"(?i)\bpassword\b\s*[:=]\s*\S+",
)


def _redact(text: str) -> str:
    cleaned = text
    for pattern in _REDACTIONS:
        cleaned = re.sub(pattern, "[redacted]", cleaned)
    return cleaned.strip()


def tool_error(code: str, *, detail: str = "", hint: str = "") -> dict[str, Any]:
    """Build the canonical error dict. Unknown codes degrade to SQL_FAILED."""
    resolved = code if code in _CATALOG else _FALLBACK_CODE
    default_message, default_hint, forwards_detail = _CATALOG[resolved]

    message = default_message
    if detail and forwards_detail:
        message = f"{default_message} {_redact(detail)}"

    return {
        "error": message,
        "hint": hint or default_hint,
        "retryable": resolved in RETRYABLE_CODES,
        "code": resolved,
    }


def from_mysql_error(exc: Exception) -> dict[str, Any]:
    """Map a driver exception onto the contract without leaking anything."""
    errno = getattr(exc, "errno", None)
    code = _ERRNO_TO_CODE.get(errno, _FALLBACK_CODE) if isinstance(errno, int) else _FALLBACK_CODE
    return tool_error(code, detail=str(getattr(exc, "msg", None) or exc))


def is_tool_error(value: Any) -> bool:
    """True when ``value`` is a dict carrying the error contract."""
    return isinstance(value, dict) and {"error", "hint", "retryable", "code"} <= set(value)
