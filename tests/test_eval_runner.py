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
