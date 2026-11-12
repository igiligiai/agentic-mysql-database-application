import api.tools.sql_tools as sql_tools
from api.tools import dispatch_tool
from api.utils.errors import is_tool_error
from mysql.connector import Error as MySQLError


def test_execute_sql_returns_columns_rows_and_timing(monkeypatch):
    monkeypatch.setattr(
        sql_tools, "fetch_matrix", lambda sql: (["make"], [["Toyota"], ["BMW"]])
    )
    result = sql_tools.execute_sql_tool({"query": "SELECT make FROM cars"})
    assert result["columns"] == ["make"]
    assert result["row_count"] == 2
    assert isinstance(result["duration_ms"], int)


def test_dispatch_routes_execute_sql_to_sql_tools(monkeypatch):
    monkeypatch.setattr(sql_tools, "fetch_matrix", lambda sql: (["n"], [[1]]))
    result = dispatch_tool("execute_sql", {"query": "SELECT 1 AS n"})
    assert result["row_count"] == 1


def test_dispatch_unknown_tool_returns_a_structured_tool_error():
    result = dispatch_tool("no_such_tool", {})
    assert is_tool_error(result)
    assert result["code"] == "TOOL_BAD_ARGS"
    assert "no_such_tool" in result["error"]


def test_write_attempt_returns_a_forbidden_tool_error(monkeypatch):
    monkeypatch.setattr(sql_tools, "fetch_matrix", lambda sql: (["x"], []))
    result = sql_tools.execute_sql_tool({"query": "DELETE FROM cars"})
    assert result["code"] == "SQL_FORBIDDEN"
    assert result["retryable"] is False


def test_limit_is_injected_and_reported(monkeypatch):
    seen: dict[str, str] = {}

    def fake_fetch(sql: str):
        seen["sql"] = sql
        return (["make"], [["Toyota"]])

    monkeypatch.setattr(sql_tools, "fetch_matrix", fake_fetch)
    result = sql_tools.execute_sql_tool({"query": "SELECT make FROM cars"})
    assert result["limit_injected"] is True
    assert seen["sql"].endswith("LIMIT 1000")


def test_driver_failure_becomes_a_retryable_tool_error(monkeypatch):
    def boom(sql: str):
        raise MySQLError(msg="Unknown column 'nope' in 'field list'", errno=1054)

    monkeypatch.setattr(sql_tools, "fetch_matrix", boom)
    result = sql_tools.execute_sql_tool({"query": "SELECT nope FROM cars"})
    assert result["code"] == "SQL_UNKNOWN_COLUMN"
    assert result["retryable"] is True


def test_missing_query_argument_is_a_bad_args_error():
    result = sql_tools.execute_sql_tool({})
    assert result["code"] == "TOOL_BAD_ARGS"


import json

_PLAN = {
    "query_block": {
        "cost_info": {"query_cost": "412.55"},
        "table": {
            "table_name": "cars",
            "access_type": "ALL",
            "rows_examined_per_scan": 18420,
        },
    }
}


def test_explain_query_extracts_cost_and_detects_a_full_scan(monkeypatch):
    monkeypatch.setattr(
        sql_tools, "fetch_all", lambda sql: [{"EXPLAIN": json.dumps(_PLAN)}]
    )
    result = sql_tools.explain_query_tool({"query": "SELECT * FROM cars"})
    assert result["estimated_cost"] == 412.55
    assert result["rows_examined"] == 18420
    assert result["full_scan"] is True


def test_explain_query_reports_no_full_scan_for_an_indexed_plan(monkeypatch):
    plan = {
        "query_block": {
            "cost_info": {"query_cost": "1.20"},
            "table": {"access_type": "ref", "rows_examined_per_scan": 3},
        }
    }
    monkeypatch.setattr(
        sql_tools, "fetch_all", lambda sql: [{"EXPLAIN": json.dumps(plan)}]
    )
    result = sql_tools.explain_query_tool({"query": "SELECT * FROM cars WHERE id = 1"})
    assert result["full_scan"] is False
    assert result["rows_examined"] == 3


def test_explain_query_refuses_a_write(monkeypatch):
    result = sql_tools.explain_query_tool({"query": "DROP TABLE cars"})
    assert result["code"] == "SQL_FORBIDDEN"


def test_dispatch_routes_explain_query(monkeypatch):
    monkeypatch.setattr(
        sql_tools, "fetch_all", lambda sql: [{"EXPLAIN": json.dumps(_PLAN)}]
    )
    result = dispatch_tool("explain_query", {"query": "SELECT * FROM cars"})
    assert result["full_scan"] is True
