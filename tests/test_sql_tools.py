import api.tools.sql_tools as sql_tools
from api.tools import dispatch_tool


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


def test_dispatch_rejects_an_unknown_tool():
    assert "error" in dispatch_tool("no_such_tool", {})
