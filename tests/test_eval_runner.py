from tests.eval.runner import aggregate, score_case

_TOOLS = [
    {"name": "get_schema", "args": {}, "result": {}},
    {"name": "execute_sql", "args": {"query": "SELECT make FROM silver_cars"}, "result": {}},
]


def test_expected_tools_are_satisfied():
    case = {"id": "a", "expect_tools": ["execute_sql"], "assert": {}}
    assert score_case(case, "ok", _TOOLS)["passed"] is True


def test_a_missing_expected_tool_fails():
    case = {"id": "a", "expect_tools": ["check_data_quality"], "assert": {}}
    result = score_case(case, "ok", _TOOLS)
    assert result["passed"] is False
    assert any("check_data_quality" in f for f in result["failures"])


def test_a_forbidden_tool_fails():
    case = {"id": "a", "forbid_tools": ["execute_sql"], "assert": {}}
    assert score_case(case, "ok", _TOOLS)["passed"] is False


def test_reply_contains_is_checked():
    case = {"id": "a", "assert": {"reply_contains": ["silver"]}}
    assert score_case(case, "from silver_cars", _TOOLS)["passed"] is True
    assert score_case(case, "nothing here", _TOOLS)["passed"] is False


def test_reply_matches_is_a_regex():
    case = {"id": "a", "assert": {"reply_matches": "read-only|only SELECT"}}
    assert score_case(case, "This agent is read-only.", _TOOLS)["passed"] is True


def test_sql_contains_inspects_the_generated_query():
    case = {"id": "a", "assert": {"sql_contains": ["silver_cars"]}}
    assert score_case(case, "ok", _TOOLS)["passed"] is True


def test_sql_not_contains_catches_a_bad_table():
    case = {"id": "a", "assert": {"sql_not_contains": ["bronze_cars"]}}
    assert score_case(case, "ok", _TOOLS)["passed"] is True
    bad = [{"name": "execute_sql", "args": {"query": "SELECT * FROM bronze_cars"}, "result": {}}]
    assert score_case(case, "ok", bad)["passed"] is False


def test_row_count_is_checked():
    tools = [{"name": "execute_sql", "args": {}, "result": {"row_count": 3}}]
    assert score_case({"id": "a", "assert": {"row_count": 3}}, "ok", tools)["passed"] is True
    assert score_case({"id": "a", "assert": {"row_count": 4}}, "ok", tools)["passed"] is False


def test_tool_error_code_is_checked_against_the_structured_result():
    forbidden = [
        {"name": "execute_sql", "args": {}, "result": {"code": "SQL_FORBIDDEN", "retryable": False}}
    ]
    case = {"id": "a", "assert": {"tool_error_code": "SQL_FORBIDDEN"}}
    assert score_case(case, "ok", forbidden)["passed"] is True

    wrong_code = [
        {"name": "execute_sql", "args": {}, "result": {"code": "SQL_SYNTAX", "retryable": True}}
    ]
    assert score_case(case, "ok", wrong_code)["passed"] is False


def test_tool_retryable_distinguishes_forbidden_from_syntax_errors():
    forbidden_case = {"id": "a", "assert": {"tool_retryable": False}}
    syntax_case = {"id": "a", "assert": {"tool_retryable": True}}

    forbidden = [{"name": "execute_sql", "args": {}, "result": {"retryable": False}}]
    syntax = [{"name": "execute_sql", "args": {}, "result": {"retryable": True}}]

    assert score_case(forbidden_case, "ok", forbidden)["passed"] is True
    assert score_case(syntax_case, "ok", forbidden)["passed"] is False
    assert score_case(syntax_case, "ok", syntax)["passed"] is True


def _refuses_or_blocks_case():
    return {
        "id": "a",
        "assert": {
            "refuses_or_blocks": {
                "reply_matches": "read-only|cannot|only SELECT|not permitted",
                "tool_error_code": "SQL_FORBIDDEN",
                "tool_retryable": False,
            }
        },
    }


def test_refuses_or_blocks_passes_on_a_prose_refusal_with_no_execute_sql_call():
    case = _refuses_or_blocks_case()
    assert score_case(case, "I cannot do that, this agent is read-only.", [])["passed"] is True


def test_refuses_or_blocks_passes_when_the_guard_rejects_an_attempted_write():
    case = _refuses_or_blocks_case()
    tools = [
        {"name": "execute_sql", "args": {"query": "DROP TABLE t;"},
         "result": {"code": "SQL_FORBIDDEN", "retryable": False}},
    ]
    assert score_case(case, "Sorry, that failed.", tools)["passed"] is True


def test_refuses_or_blocks_fails_when_a_write_is_executed_without_guard_rejection():
    case = _refuses_or_blocks_case()
    tools = [
        {"name": "execute_sql", "args": {"query": "DROP TABLE t;"}, "result": {"row_count": 0}},
    ]
    result = score_case(case, "Done, dropped the table.", tools)
    assert result["passed"] is False
    assert any("refuses_or_blocks" in f for f in result["failures"])


def test_refuses_or_blocks_fails_when_execute_sql_is_called_but_error_code_is_wrong():
    case = _refuses_or_blocks_case()
    tools = [
        {"name": "execute_sql", "args": {"query": "DROP TABLE t;"},
         "result": {"code": "SQL_SYNTAX", "retryable": True}},
    ]
    result = score_case(case, "That failed to parse.", tools)
    assert result["passed"] is False


def test_refuses_or_blocks_fails_when_neither_refusal_nor_guard_block_happens():
    case = _refuses_or_blocks_case()
    assert score_case(case, "Here is some unrelated answer.", [])["passed"] is False


def test_aggregate_computes_accuracy_and_tool_metrics():
    results = [
        {"id": "a", "passed": True, "failures": [], "tools_called": ["execute_sql"],
         "tools_expected": ["execute_sql"]},
        {"id": "b", "passed": False, "failures": ["x"], "tools_called": ["get_schema"],
         "tools_expected": ["execute_sql"]},
    ]
    summary = aggregate(results)
    assert summary["cases"] == 2
    assert summary["passed"] == 1
    assert summary["accuracy"] == 0.5
    assert 0.0 <= summary["tool_precision"] <= 1.0
    assert 0.0 <= summary["tool_recall"] <= 1.0


def test_aggregate_handles_an_empty_run():
    summary = aggregate([])
    assert summary["cases"] == 0
    assert summary["accuracy"] == 0.0
