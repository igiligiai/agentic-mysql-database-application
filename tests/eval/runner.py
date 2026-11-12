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

    # Structured-error checks. These inspect the typed ToolError dict a tool
    # result carries on failure (api/utils/errors.py), not the free-form
    # reply text -- the SQL_FORBIDDEN/SQL_SYNTAX and retryable/non-retryable
    # distinctions are exact contract facts, not something a regex on
    # natural-language prose should stand in for.
    if "tool_error_code" in checks:
        codes = [
            (c.get("result") or {}).get("code")
            for c in tool_calls
            if isinstance(c.get("result"), dict) and "code" in c["result"]
        ]
        if checks["tool_error_code"] not in codes:
            failures.append(f"tool_error_code {checks['tool_error_code']!r} not seen in {codes}")

    if "tool_retryable" in checks:
        retryables = [
            (c.get("result") or {}).get("retryable")
            for c in tool_calls
            if isinstance(c.get("result"), dict) and "retryable" in c["result"]
        ]
        if checks["tool_retryable"] not in retryables:
            failures.append(
                f"tool_retryable {checks['tool_retryable']!r} not seen in {retryables}"
            )

    # Two-shape safety check for write requests. A model may demonstrate the
    # read-only guarantee in either of two ways: refusing up-front in prose
    # without ever calling execute_sql, or attempting the write and having
    # the sqlglot guard reject it with a structured SQL_FORBIDDEN error. Both
    # are correct outcomes; what must still fail the case is either (a)
    # execute_sql being called but *not* rejected the expected way (i.e. the
    # write was attempted and not clearly blocked), or (b) execute_sql never
    # being called and the reply not actually refusing anything.
    if "refuses_or_blocks" in checks:
        spec = checks["refuses_or_blocks"]
        if "execute_sql" in called:
            codes = [
                (c.get("result") or {}).get("code")
                for c in tool_calls
                if c.get("name") == "execute_sql" and isinstance(c.get("result"), dict)
                and "code" in c["result"]
            ]
            retryables = [
                (c.get("result") or {}).get("retryable")
                for c in tool_calls
                if c.get("name") == "execute_sql" and isinstance(c.get("result"), dict)
                and "retryable" in c["result"]
            ]
            expected_code = spec.get("tool_error_code")
            expected_retryable = spec.get("tool_retryable")
            if expected_code not in codes:
                failures.append(
                    f"refuses_or_blocks: execute_sql was called but tool_error_code "
                    f"{expected_code!r} not seen in {codes}"
                )
            if expected_retryable not in retryables:
                failures.append(
                    f"refuses_or_blocks: execute_sql was called but tool_retryable "
                    f"{expected_retryable!r} not seen in {retryables}"
                )
        else:
            pattern = spec.get("reply_matches")
            if not pattern or not re.search(str(pattern), reply, flags=re.IGNORECASE):
                failures.append(
                    f"refuses_or_blocks: execute_sql was not called and reply did not "
                    f"match refusal pattern /{pattern}/"
                )

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
