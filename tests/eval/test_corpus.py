"""End-to-end agent evaluation. Marked `eval` and excluded from the default run
because it needs OPENAI_API_KEY and a live MySQL database.

Run with:  uv run pytest -m eval -v

To additionally exercise the `requires_fixture: true` cases, load
tests/eval/fixture.sql into the database first (see its header comment for the
exact commands) and set AUTOBASE_EVAL_FIXTURE=1.
"""

from __future__ import annotations

import json
import os

import pytest

from api.agent import AgenticWorkflow
from tests.eval.runner import (
    ACCURACY_TOLERANCE,
    BASELINE_PATH,
    LAST_RUN_PATH,
    aggregate,
    load_cases,
    score_case,
)

pytestmark = pytest.mark.eval


@pytest.fixture(scope="module")
def workflow() -> AgenticWorkflow:
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set; cannot run the live agent eval corpus")
    return AgenticWorkflow()


def test_corpus(workflow: AgenticWorkflow) -> None:
    results = []
    for index, case in enumerate(load_cases()):
        if case.get("requires_fixture") and not os.getenv("AUTOBASE_EVAL_FIXTURE"):
            continue
        response = workflow.run_chat(case["question"], session_id=f"eval-{index}")
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
