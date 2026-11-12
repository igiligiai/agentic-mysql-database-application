"""Per-turn metrics. Shapes and numbers only -- never message content.

The buffer is in-process and bounded, so it cannot grow without limit and
cannot become an accidental transcript store.
"""

from __future__ import annotations

from collections import Counter, deque
from typing import Any

MAX_RECORDS = 500

# Only these keys are ever persisted. Anything else passed in is dropped,
# which is what keeps conversation text out of the buffer by construction.
_ALLOWED_KEYS = frozenset(
    {
        "session_id",
        "timestamp",
        "duration_ms",
        "model",
        "input_tokens",
        "output_tokens",
        "est_cost",
        "tool_spans",
        "repairs",
        "error_code",
    }
)

# USD per 1000 tokens. Unknown models cost 0.0 rather than guessing.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4.1": (0.002, 0.008),
    "gpt-4.1-mini": (0.0004, 0.0016),
}

_records: deque[dict[str, Any]] = deque(maxlen=MAX_RECORDS)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = MODEL_PRICING.get(model)
    if not prices:
        return 0.0
    in_price, out_price = prices
    return round((input_tokens / 1000) * in_price + (output_tokens / 1000) * out_price, 6)


def record_turn(record: dict[str, Any]) -> None:
    _records.append({k: v for k, v in record.items() if k in _ALLOWED_KEYS})


def records() -> list[dict[str, Any]]:
    return list(_records)


def reset() -> None:
    _records.clear()


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))
    return int(ordered[index])


def snapshot() -> dict[str, Any]:
    items = list(_records)
    latencies = [int(r.get("duration_ms") or 0) for r in items]

    tools: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    for record in items:
        for span in record.get("tool_spans") or []:
            name = str(span.get("name") or "")
            if name:
                tools[name] += 1
        code = record.get("error_code")
        if code:
            errors[str(code)] += 1
        for span in record.get("tool_spans") or []:
            span_code = span.get("error_code")
            if span_code and not record.get("error_code"):
                errors[str(span_code)] += 1

    input_tokens = sum(int(r.get("input_tokens") or 0) for r in items)
    output_tokens = sum(int(r.get("output_tokens") or 0) for r in items)
    repairs = sum(int(r.get("repairs") or 0) for r in items)

    return {
        "turns": len(items),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "tools": dict(tools),
        "errors": dict(errors),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "est_cost": round(sum(float(r.get("est_cost") or 0.0) for r in items), 6),
        "repair_rate": round(repairs / len(items), 3) if items else 0.0,
        "buffer_limit": MAX_RECORDS,
    }
