import api.agent as agent_module
from api.agent import AgenticWorkflow, _retryable_failed_spans
from api.utils.errors import tool_error


def _always_fails(tool, args):
    return tool_error("SQL_SYNTAX", detail="bad token")


def test_third_consecutive_failure_is_downgraded(monkeypatch):
    monkeypatch.setattr(agent_module, "dispatch_tool", _always_fails)
    workflow = AgenticWorkflow()

    first = workflow.run_tool("execute_sql", {"query": "SELCT 1"})
    second = workflow.run_tool("execute_sql", {"query": "SELCT 1"})
    third = workflow.run_tool("execute_sql", {"query": "SELCT 1"})

    assert first["retryable"] is True
    assert second["retryable"] is True
    assert third["retryable"] is False
    assert "clarif" in third["hint"].lower()


def test_a_success_resets_the_counter(monkeypatch):
    calls = {"n": 0}

    def flaky(tool, args):
        calls["n"] += 1
        if calls["n"] in (1, 2):
            return tool_error("SQL_SYNTAX", detail="bad token")
        if calls["n"] == 3:
            return {"rows": [], "row_count": 0}
        return tool_error("SQL_SYNTAX", detail="bad token")

    monkeypatch.setattr(agent_module, "dispatch_tool", flaky)
    workflow = AgenticWorkflow()

    workflow.run_tool("execute_sql", {})
    workflow.run_tool("execute_sql", {})
    workflow.run_tool("execute_sql", {})  # success resets
    after_reset = workflow.run_tool("execute_sql", {})

    assert after_reset["retryable"] is True


def test_non_retryable_errors_do_not_increment_the_counter(monkeypatch):
    monkeypatch.setattr(
        agent_module,
        "dispatch_tool",
        lambda tool, args: tool_error("SQL_FORBIDDEN", detail="blocked"),
    )
    workflow = AgenticWorkflow()

    for _ in range(5):
        result = workflow.run_tool("execute_sql", {})

    assert result["code"] == "SQL_FORBIDDEN"
    assert workflow._consecutive_failures == 0


def test_repair_count_excludes_non_retryable_spans():
    spans = [
        {"name": "execute_sql", "ok": False, "error_code": "SQL_SYNTAX"},
        {"name": "execute_sql", "ok": False, "error_code": "SQL_FORBIDDEN"},
        {"name": "get_schema", "ok": True, "error_code": None},
    ]
    assert [s["error_code"] for s in _retryable_failed_spans(spans)] == ["SQL_SYNTAX"]


def test_repair_count_is_zero_when_only_forbidden_spans_failed():
    spans = [
        {"name": "execute_sql", "ok": False, "error_code": "SQL_FORBIDDEN"},
        {"name": "execute_sql", "ok": False, "error_code": "SQL_FORBIDDEN"},
    ]
    assert _retryable_failed_spans(spans) == []
