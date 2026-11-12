import pytest

from api.utils import telemetry


@pytest.fixture(autouse=True)
def _clean():
    telemetry.reset()
    yield
    telemetry.reset()


def _record(duration_ms: int, tool: str = "execute_sql", error_code: str | None = None):
    telemetry.record_turn(
        {
            "session_id": "s1",
            "duration_ms": duration_ms,
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 50,
            "repairs": 1,
            "error_code": error_code,
            "tool_spans": [{"name": tool, "duration_ms": 5, "ok": error_code is None,
                            "error_code": error_code}],
        }
    )


def test_snapshot_of_an_empty_buffer_is_safe():
    snap = telemetry.snapshot()
    assert snap["turns"] == 0
    assert snap["p50_latency_ms"] == 0
    assert snap["p95_latency_ms"] == 0
    assert snap["tools"] == {}


def test_buffer_is_bounded():
    for i in range(telemetry.MAX_RECORDS + 50):
        _record(i)
    assert telemetry.snapshot()["turns"] == telemetry.MAX_RECORDS


def test_percentiles_are_computed():
    for value in range(1, 101):
        _record(value)
    snap = telemetry.snapshot()
    assert snap["p50_latency_ms"] == 50
    assert snap["p95_latency_ms"] == 95


def test_tool_and_error_histograms_are_counted():
    _record(10, tool="execute_sql")
    _record(20, tool="execute_sql")
    _record(30, tool="get_schema", error_code="SQL_SYNTAX")
    snap = telemetry.snapshot()
    assert snap["tools"] == {"execute_sql": 2, "get_schema": 1}
    assert snap["errors"] == {"SQL_SYNTAX": 1}


def test_tokens_and_repair_rate_are_aggregated():
    _record(10)
    _record(20)
    snap = telemetry.snapshot()
    assert snap["input_tokens"] == 200
    assert snap["output_tokens"] == 100
    assert snap["repair_rate"] == 1.0


def test_message_content_is_never_stored():
    telemetry.record_turn(
        {
            "session_id": "s1",
            "duration_ms": 1,
            "model": "m",
            "reply": "secret customer data",
            "message": "secret question",
        }
    )
    stored = telemetry.records()[0]
    assert "reply" not in stored
    assert "message" not in stored


def test_estimate_cost_is_zero_for_an_unknown_model():
    assert telemetry.estimate_cost("not-a-model", 1000, 1000) == 0.0
