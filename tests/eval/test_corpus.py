"""End-to-end agent evaluation. Marked `eval` and excluded from the default run
because it needs OPENAI_API_KEY and a live MySQL database.

Run with:  uv run pytest -m eval -v

Cases in cases.yaml are schema-agnostic: they use the literal placeholders
"{table}" and "{fixture_table}" instead of a hard-coded table name. This
module resolves "{table}" at run time via live information_schema
introspection (through the same pooled api.utils.db helper the rest of the
app uses) so the corpus keeps working against whatever database is actually
connected. "{fixture_table}" resolves to AUTOBASE_EVAL_FIXTURE_TABLE (see
tests/eval/fixture.sql) and only matters for `requires_fixture: true` cases.

To additionally exercise the `requires_fixture: true` cases, load
tests/eval/fixture.sql into the database first (see its header comment for the
exact commands) and set AUTOBASE_EVAL_FIXTURE=1.
"""

from __future__ import annotations

import json
import os

import pytest

from api.agent import AgenticWorkflow
from api.utils.db import fetch_all
from tests.eval.runner import (
    ACCURACY_TOLERANCE,
    BASELINE_PATH,
    LAST_RUN_PATH,
    aggregate,
    load_cases,
    score_case,
)

pytestmark = pytest.mark.eval

DEFAULT_FIXTURE_TABLE = "autobase_eval_fixture"


@pytest.fixture(scope="module")
def workflow() -> AgenticWorkflow:
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set; cannot run the live agent eval corpus")
    return AgenticWorkflow()


@pytest.fixture(scope="module")
def sample_table() -> str:
    """One real base table name from the connected database.

    Discovered via introspection -- never hard-coded -- so the corpus works
    against any schema, not just the one this harness happened to be built
    against.
    """
    try:
        rows = fetch_all(
            """
            SELECT TABLE_NAME AS name
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
            LIMIT 1
            """
        )
    except Exception as exc:  # pragma: no cover - depends on a live DB
        pytest.skip(f"cannot introspect the connected database for a sample table: {exc}")
    if not rows:
        pytest.skip("the connected database has no base tables to use in eval cases")
    return str(rows[0]["name"])


def _render(question: str, *, table: str, fixture_table: str) -> str:
    return question.format(table=table, fixture_table=fixture_table)


def test_corpus(workflow: AgenticWorkflow, sample_table: str) -> None:
    fixture_table = os.getenv("AUTOBASE_EVAL_FIXTURE_TABLE", DEFAULT_FIXTURE_TABLE)
    results = []
    for index, case in enumerate(load_cases()):
        if case.get("requires_fixture") and not os.getenv("AUTOBASE_EVAL_FIXTURE"):
            continue
        question = _render(case["question"], table=sample_table, fixture_table=fixture_table)
        response = workflow.run_chat(question, session_id=f"eval-{index}")
        results.append(
            score_case(case, response["reply"], response["tool_calls"])
        )

    summary = aggregate(results)
    summary["results"] = results
    LAST_RUN_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    for result in results:
        if not result["passed"]:
            print(f"FAIL {result['id']}: {'; '.join(result['failures'])}")

    if not BASELINE_PATH.is_file():
        pytest.skip("no baseline.json yet; copy last_run.json to baseline.json")

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    if not baseline.get("collected", True):
        pytest.skip(
            "baseline.json is a placeholder (collected=false); run this suite "
            "with credentials and copy tests/eval/last_run.json over it to "
            "record a real baseline"
        )

    floor = baseline["accuracy"] - ACCURACY_TOLERANCE
    assert summary["accuracy"] >= floor, (
        f"accuracy regressed: {summary['accuracy']} < {floor} "
        f"(baseline {baseline['accuracy']})"
    )
