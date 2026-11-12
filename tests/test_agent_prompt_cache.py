"""Regression coverage for api.agent._get_system_prompt caching.

api.semantics.loader.get_semantic_prompt_block() was fixed to not memoise a
failed introspection, but production only reaches the loader through
api.agent._get_system_prompt(), which unconditionally memoised the assembled
prompt on first call. That second cache defeated the loader fix: a DB outage
on the first chat turn would freeze the "introspection was unavailable"
fallback for the process lifetime. These tests go through the agent entry
point, not the loader directly, to prove the fix actually reaches production.
"""

from __future__ import annotations

import api.agent as agent_module
import api.semantics.loader as loader


def _reset() -> None:
    agent_module._reset_system_prompt_for_tests()
    loader._reset_cache_for_tests()


def test_system_prompt_recovers_after_a_failed_first_introspection(monkeypatch):
    _reset()

    def boom(query, params=None):
        raise RuntimeError("Can't connect to MySQL server on '10.0.0.5' (111)")

    monkeypatch.setattr(loader, "fetch_all", boom)
    first = agent_module._get_system_prompt()
    assert "Schema introspection was unavailable" in first
    assert "cars_silver" not in first

    def fake_fetch_all(query, params=None):
        return [{"name": "cars_silver", "type": "BASE TABLE"}]

    monkeypatch.setattr(loader, "fetch_all", fake_fetch_all)
    second = agent_module._get_system_prompt()
    assert "cars_silver" in second

    _reset()


def test_system_prompt_is_cached_once_introspection_succeeds(monkeypatch):
    _reset()
    calls = {"count": 0}

    def fake_fetch_all(query, params=None):
        calls["count"] += 1
        return [{"name": "cars_silver", "type": "BASE TABLE"}]

    monkeypatch.setattr(loader, "fetch_all", fake_fetch_all)

    first = agent_module._get_system_prompt()
    second = agent_module._get_system_prompt()

    assert calls["count"] == 1, "a successful block must still be memoised"
    assert first == second
    assert "cars_silver" in first

    _reset()
