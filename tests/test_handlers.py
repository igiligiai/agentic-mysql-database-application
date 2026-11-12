"""Error-contract coverage for the four handlers that previously bypassed it.

get_schema_tool, get_table_lineage_tool, describe_table_tool, and
check_data_quality_tool used to let MySQLError propagate uncaught and raised
HTTPException directly for bad arguments. Both bypassed the structured,
redacting ToolError contract that lets the agent self-correct and keeps
driver text out of HTTP responses.
"""

from __future__ import annotations

import api.tools.handlers as handlers
from api.utils.errors import is_tool_error
from mysql.connector import Error as MySQLError


def _credential_error() -> MySQLError:
    return MySQLError(
        msg="Access denied for user 'root'@'10.0.0.5' (using password: YES)",
        errno=1045,
    )


def test_get_schema_tool_returns_a_redacted_tool_error_on_db_failure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise _credential_error()

    monkeypatch.setattr(handlers, "fetch_all", boom)

    result = handlers.get_schema_tool()

    assert is_tool_error(result)
    assert result["code"] == "SQL_UNAVAILABLE"
    assert "10.0.0.5" not in result["error"]
    assert "root" not in result["error"]


def test_get_table_lineage_tool_returns_a_redacted_tool_error_on_db_failure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise _credential_error()

    monkeypatch.setattr(handlers, "fetch_all", boom)

    result = handlers.get_table_lineage_tool({"table_name": "all"})

    assert is_tool_error(result)
    assert result["code"] == "SQL_UNAVAILABLE"
    assert "10.0.0.5" not in result["error"]


def test_describe_table_tool_rejects_a_missing_table_name():
    result = handlers.describe_table_tool({})

    assert is_tool_error(result)
    assert result["code"] == "TOOL_BAD_ARGS"


def test_describe_table_tool_rejects_an_invalid_identifier():
    result = handlers.describe_table_tool({"table_name": "cars; DROP TABLE x"})

    assert is_tool_error(result)
    assert result["code"] == "TOOL_BAD_ARGS"


def test_describe_table_tool_returns_a_redacted_tool_error_on_db_failure(monkeypatch):
    monkeypatch.setattr(handlers, "fetch_one", lambda *a, **k: (_ for _ in ()).throw(_credential_error()))
    monkeypatch.setattr(handlers, "fetch_all", lambda *a, **k: [])

    result = handlers.describe_table_tool({"table_name": "cars"})

    assert is_tool_error(result)
    assert result["code"] == "SQL_UNAVAILABLE"
    assert "10.0.0.5" not in result["error"]


def test_check_data_quality_tool_rejects_a_missing_table_name():
    result = handlers.check_data_quality_tool({})

    assert is_tool_error(result)
    assert result["code"] == "TOOL_BAD_ARGS"


def test_check_data_quality_tool_rejects_an_invalid_identifier():
    result = handlers.check_data_quality_tool({"table_name": "cars; DROP TABLE x"})

    assert is_tool_error(result)
    assert result["code"] == "TOOL_BAD_ARGS"


def test_check_data_quality_tool_returns_a_redacted_tool_error_on_db_failure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise _credential_error()

    monkeypatch.setattr(handlers, "fetch_all", boom)

    result = handlers.check_data_quality_tool({"table_name": "cars"})

    assert is_tool_error(result)
    assert result["code"] == "SQL_UNAVAILABLE"
    assert "10.0.0.5" not in result["error"]
