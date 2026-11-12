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
#
# Entries are (pattern, replacement) pairs. Most replace the whole match with
# "[redacted]"; the host-context entries carry a capture group for the
# triggering keyword/sigil so only the host value itself is redacted -- this
# keeps the patterns anchored to genuine host *context* instead of a bare
# "any dotted token" match, which would also swallow the db.table / table.column
# forms (e.g. "cars.price", "cars_db.silver_cars") the agent needs to self-correct.
_REDACTIONS: tuple[tuple[str, str], ...] = (
    # scheme://user:pass@host[:port]/path credential URIs (mysql://, postgres://, ...).
    (r"(?i)\b[a-z][a-z0-9+.-]*://\S+", "[redacted]"),
    (r"'[^']*'@'[^']*'", "[redacted]"),
    (r"(?i)using password:[^)]*", "[redacted]"),
    # password=, passwd=, pwd= (and the ':' variants) in key=value form.
    (r"(?i)\b(?:password|passwd|pwd)\b\s*[:=]\s*\S+", "[redacted]"),
    (r"(?i)[a-z]:\\[^\s\"']+", "[redacted]"),
    # Windows UNC paths: \\host\share\path...
    (r"\\\\[\w.$-]+(?:\\[\w.$-]+)+", "[redacted]"),
    # POSIX filesystem paths: a "/" that opens a path, not one that divides
    # two bare tokens (e.g. "a/b/c" in a SQL arithmetic expression). Requiring
    # the leading "/" not be preceded by a word character keeps real paths
    # like "/etc/passwd" or "/var/lib/mysql/data.sock" redacted while leaving
    # "a/b/c" alone.
    (r"(?<![\w])/(?:[\w.-]+/)+[\w.-]+", "[redacted]"),
    # Bare IPv4 addresses, quoted or not, e.g. '10.0.0.5' or 10.0.0.5.
    (r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "[redacted]"),
    # Hostnames in genuine host context: after "host"/"hostname", after an
    # unquoted "@" (the quoted 'user'@'host' form is already handled above),
    # or alongside "connect(ing) to" phrasing. The keyword/sigil itself is
    # kept via the capture group -- only the host value is redacted.
    (r"(?i)(\bhost(?:name)?\b\s*[:=]?\s*)'?[\w.-]+'?", r"\1[redacted]"),
    (r"(@)[\w.-]+", r"\1[redacted]"),
    (r"(?i)(\bconnect(?:ing)?\s+to\s+)'?[\w.-]+'?", r"\1[redacted]"),
)


def _redact(text: str) -> str:
    cleaned = text
    for pattern, replacement in _REDACTIONS:
        cleaned = re.sub(pattern, replacement, cleaned)
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
