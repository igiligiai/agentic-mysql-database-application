"""Scoring logic for the eval corpus.

Kept free of any agent or database dependency so it can be unit-tested fast.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

CASES_PATH = Path(__file__).resolve().parent / "cases.yaml"
BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"
LAST_RUN_PATH = Path(__file__).resolve().parent / "last_run.json"
ACCURACY_TOLERANCE = 0.02


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or []
    if not isinstance(loaded, list):
        raise ValueError("cases.yaml must contain a YAML list")
    ids = [c["id"] for c in loaded]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in cases.yaml")
    return loaded


def _generated_sql(tool_calls: list[dict[str, Any]]) -> str:
    parts = [
        str((call.get("args") or {}).get("query") or "")
        for call in tool_calls
        if call.get("name") in {"execute_sql", "explain_query"}
    ]
    return " ".join(parts)


def score_case(
    case: dict[str, Any], reply: str, tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    failures: list[str] = []
    called = [str(c.get("name") or "") for c in tool_calls]
    expected = list(case.get("expect_tools") or [])

    for name in expected:
        if name not in called:
            failures.append(f"expected tool {name} was not called")

    for name in case.get("forbid_tools") or []:
        if name in called:
            failures.append(f"forbidden tool {name} was called")

    checks = case.get("assert") or {}
    lowered_reply = reply.lower()
    sql = _generated_sql(tool_calls).lower()

    for needle in checks.get("reply_contains") or []:
        if str(needle).lower() not in lowered_reply:
            failures.append(f"reply missing {needle!r}")

    pattern = checks.get("reply_matches")
    if pattern and not re.search(str(pattern), reply, flags=re.IGNORECASE):
        failures.append(f"reply did not match /{pattern}/")

    for needle in checks.get("sql_contains") or []:
        if str(needle).lower() not in sql:
            failures.append(f"sql missing {needle!r}")

    for needle in checks.get("sql_not_contains") or []:
        if str(needle).lower() in sql:
            failures.append(f"sql unexpectedly contained {needle!r}")

    if "row_count" in checks:
        counts = [
            (c.get("result") or {}).get("row_count")
            for c in tool_calls
            if isinstance(c.get("result"), dict) and "row_count" in c["result"]
        ]
        if checks["row_count"] not in counts:
            failures.append(f"row_count {checks['row_count']} not seen in {counts}")

    return {
        "id": case["id"],
        "passed": not failures,
        "failures": failures,
        "tools_called": called,
        "tools_expected": expected,
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {
            "cases": 0,
            "passed": 0,
            "accuracy": 0.0,
            "tool_precision": 0.0,
            "tool_recall": 0.0,
        }

    passed = sum(1 for r in results if r["passed"])

    true_positives = 0
    called_total = 0
    expected_total = 0
    for result in results:
        called = set(result.get("tools_called") or [])
        expected = set(result.get("tools_expected") or [])
        true_positives += len(called & expected)
        called_total += len(called)
        expected_total += len(expected)

    return {
        "cases": len(results),
        "passed": passed,
        "accuracy": round(passed / len(results), 4),
        "tool_precision": round(true_positives / called_total, 4) if called_total else 0.0,
        "tool_recall": round(true_positives / expected_total, 4) if expected_total else 0.0,
    }
