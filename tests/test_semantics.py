from __future__ import annotations

import api.semantics.loader as loader
from api.semantics.loader import (
    MAX_RENDER_BYTES,
    get_semantic_prompt_block,
    introspect_schema,
    render_schema_inventory,
)


def _obj(name: str, type_: str = "BASE TABLE") -> dict[str, str]:
    return {"name": name, "type": "VIEW" if type_ == "VIEW" else "TABLE", "layer": "silver"}


def test_render_inventory_lists_objects_grouped_by_kind_and_layer():
    objects = [
        {"name": "events_bronze", "type": "TABLE", "layer": "bronze"},
        {"name": "vehicle_maneuvers", "type": "TABLE", "layer": "silver"},
        {"name": "v_asset_vehicle_data", "type": "VIEW", "layer": "gold"},
    ]
    rendered = render_schema_inventory(objects)

    assert "events_bronze" in rendered
    assert "vehicle_maneuvers" in rendered
    assert "v_asset_vehicle_data" in rendered
    assert "(table)" in rendered
    assert "(view)" in rendered
    assert "Bronze" in rendered and "Silver" in rendered and "Gold" in rendered
    # It must tell the agent that drill-down tools exist for the detail it omits.
    assert "get_schema" in rendered
    assert "describe_table" in rendered
    assert "get_table_lineage" in rendered


def test_render_inventory_ordering_is_deterministic_regardless_of_input_order():
    objects = [_obj(f"table_{i}") for i in reversed(range(20))]
    shuffled = [objects[i] for i in (3, 0, 10, 2, 19, 7)] + [
        o for i, o in enumerate(objects) if i not in (3, 0, 10, 2, 19, 7)
    ]

    first = render_schema_inventory(list(objects))
    second = render_schema_inventory(list(shuffled))
    assert first == second


def test_render_inventory_empty_when_no_objects_found():
    rendered = render_schema_inventory([])
    assert "get_schema" in rendered
    assert len(rendered.encode("utf-8")) <= MAX_RENDER_BYTES


def test_render_inventory_truncates_deterministically_when_over_budget():
    objects = [_obj(f"a_very_long_table_name_number_{i:04d}") for i in range(500)]

    rendered = render_schema_inventory(objects, max_bytes=512)

    assert len(rendered.encode("utf-8")) <= 512
    assert "truncated" in rendered.lower()
    assert "get_schema" in rendered

    # Same input, same budget -> identical output (stable ordering/truncation point).
    again = render_schema_inventory(objects, max_bytes=512)
    assert rendered == again


def test_a_huge_live_inventory_still_fits_the_prompt_budget():
    """Regression guard: even an 88-object schema must respect MAX_RENDER_BYTES."""
    objects = [_obj(f"object_{i:03d}") for i in range(200)]
    rendered = render_schema_inventory(objects)
    assert len(rendered.encode("utf-8")) <= MAX_RENDER_BYTES


def test_introspect_schema_uses_the_pooled_fetch_all(monkeypatch):
    calls: list[str] = []

    def fake_fetch_all(query, params=None):
        calls.append(query)
        return [
            {"name": "cars_bronze", "type": "BASE TABLE"},
            {"name": "vw_cars_gold", "type": "VIEW"},
        ]

    monkeypatch.setattr(loader, "fetch_all", fake_fetch_all)

    objects = introspect_schema()

    assert len(calls) == 1
    assert "information_schema.TABLES" in calls[0]
    assert "DATABASE()" in calls[0]
    names = {o["name"] for o in objects}
    assert names == {"cars_bronze", "vw_cars_gold"}
    by_name = {o["name"]: o for o in objects}
    assert by_name["vw_cars_gold"]["type"] == "VIEW"
    assert by_name["cars_bronze"]["type"] == "TABLE"


def test_introspect_schema_degrades_to_empty_list_when_the_db_raises(monkeypatch):
    def boom(query, params=None):
        raise RuntimeError("Can't connect to MySQL server on '10.0.0.5' (111)")

    monkeypatch.setattr(loader, "fetch_all", boom)

    assert introspect_schema() == []


def test_semantic_prompt_block_falls_back_gracefully_when_introspection_fails(monkeypatch):
    loader._reset_cache_for_tests()

    def boom(query, params=None):
        raise RuntimeError("Access denied for user 'root'@'10.0.0.5'")

    monkeypatch.setattr(loader, "fetch_all", boom)

    block = get_semantic_prompt_block()

    assert "get_schema" in block
    assert len(block.encode("utf-8")) <= MAX_RENDER_BYTES
    # Never leak driver text, hosts, or credentials into the prompt.
    assert "10.0.0.5" not in block
    assert "root" not in block
    assert "Access denied" not in block

    loader._reset_cache_for_tests()


def test_semantic_prompt_block_memoises_introspection_across_repeated_calls(monkeypatch):
    loader._reset_cache_for_tests()
    calls = {"count": 0}

    def fake_fetch_all(query, params=None):
        calls["count"] += 1
        return [{"name": "cars_silver", "type": "BASE TABLE"}]

    monkeypatch.setattr(loader, "fetch_all", fake_fetch_all)

    first = get_semantic_prompt_block()
    second = get_semantic_prompt_block()
    third = get_semantic_prompt_block()

    assert calls["count"] == 1, "introspection must run once, not per render"
    assert first == second == third

    loader._reset_cache_for_tests()


def test_semantic_prompt_block_does_not_cache_a_failed_introspection(monkeypatch):
    """Regression guard: a DB outage on the first call must not permanently
    freeze the prompt at the 'introspection was unavailable' fallback -- a
    later, successful introspection must be reflected once the DB recovers.
    """
    loader._reset_cache_for_tests()

    def boom(query, params=None):
        raise RuntimeError("Can't connect to MySQL server on '10.0.0.5' (111)")

    monkeypatch.setattr(loader, "fetch_all", boom)
    first = get_semantic_prompt_block()
    assert "get_schema" in first
    assert "cars_silver" not in first

    def fake_fetch_all(query, params=None):
        return [{"name": "cars_silver", "type": "BASE TABLE"}]

    monkeypatch.setattr(loader, "fetch_all", fake_fetch_all)
    second = get_semantic_prompt_block()
    assert "cars_silver" in second

    loader._reset_cache_for_tests()
