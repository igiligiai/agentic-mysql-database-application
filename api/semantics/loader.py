"""Render a live schema inventory into the system prompt.

Earlier this module loaded a hand-curated glossary describing a demo car-sales
warehouse. That glossary named tables (``cars_bronze``, ``vw_price_summary``,
...) that do not exist in whatever database is actually connected, which made
the prompt actively misleading. This module replaces it with a name-only
inventory built from ``information_schema`` at runtime, so the prompt always
describes the schema that is really there.

Deliberately not a per-column dump: a database can have dozens of tables, and
rendering every column would blow the prompt budget. The inventory just tells
the agent what exists (table vs. view, grouped by inferred medallion layer);
``get_schema``, ``describe_table`` and ``get_table_lineage`` remain the tools
for column-level and relationship detail.

Introspection runs through the existing pooled ``fetch_all`` helper (never a
new connection path) and is wrapped so a database outage degrades to an empty
inventory instead of breaking prompt construction -- and, since the agent
composes the prompt lazily on first use rather than at import time, instead of
breaking module import.
"""

from __future__ import annotations

from api.tools.helpers import infer_layer
from api.utils.db import fetch_all

MAX_RENDER_BYTES = 4096

_LAYER_ORDER = {"bronze": 0, "silver": 1, "gold": 2}

# Schema-agnostic SQL guidance kept from the old curated render: it holds for
# any connected schema, unlike the old glossary/exemplar tables which named
# one specific demo database.
STATIC_GUIDANCE = """## SQL guidance
- Prefer gold-layer views for reporting; they are already joined and aggregated.
- Always bound results with LIMIT or a selective WHERE before running a query.
- Qualify column names when joining, to avoid ambiguous-column errors."""

_EMPTY_INVENTORY = (
    "## Database inventory\n\n"
    "Schema introspection was unavailable (the database may be unreachable or "
    "empty), so no table/view inventory could be generated here. Call "
    "get_schema to discover the live schema before writing SQL."
)

_TRUNCATION_NOTE = (
    "\n\n_This list is truncated to fit the prompt budget. Call get_schema for "
    "the complete, column-level schema of every table and view._"
)


def introspect_schema() -> list[dict[str, str]]:
    """Return the tables/views in the connected database, or [] on any failure.

    Never raises: a down database, bad credentials, or an empty schema must
    not prevent the agent from starting with its base prompt, and no driver
    text may leak into the prompt.
    """
    try:
        rows = fetch_all(
            """
            SELECT TABLE_NAME AS name, TABLE_TYPE AS type
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
            """
        )
    except Exception:
        return []

    objects: list[dict[str, str]] = []
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        name = str(name)
        objects.append(
            {
                "name": name,
                "type": "VIEW" if row.get("type") == "VIEW" else "TABLE",
                "layer": infer_layer(name),
            }
        )
    return objects


def render_schema_inventory(
    objects: list[dict[str, str]], max_bytes: int = MAX_RENDER_BYTES
) -> str:
    """Render a compact, deterministically-ordered inventory of db objects.

    Grouped by inferred medallion layer, then sorted by name within a layer,
    so the output (and any truncation point) is stable across calls. If the
    full inventory would exceed ``max_bytes``, it is truncated at a line
    boundary and a note is appended saying the list is partial.
    """
    if not objects:
        return _EMPTY_INVENTORY

    ordered = sorted(
        objects,
        key=lambda o: (_LAYER_ORDER.get(o["layer"], len(_LAYER_ORDER)), o["name"].lower()),
    )

    header = (
        "## Database inventory\n\n"
        f"{len(ordered)} tables/views in the connected database, grouped by "
        "inferred medallion layer and annotated by kind (table/view). This is "
        "a name-only inventory -- call get_schema for full column detail, "
        "describe_table for one table, or get_table_lineage for data flow."
    )

    note_bytes = len(_TRUNCATION_NOTE.encode("utf-8"))
    assembled = header
    current_layer: str | None = None
    truncated = False

    for obj in ordered:
        piece = ""
        if obj["layer"] != current_layer:
            current_layer = obj["layer"]
            piece += f"\n\n### {current_layer.capitalize()}"
        kind = "view" if obj["type"] == "VIEW" else "table"
        piece += f"\n- {obj['name']} ({kind})"

        candidate = assembled + piece
        if len(candidate.encode("utf-8")) + note_bytes > max_bytes:
            truncated = True
            break
        assembled = candidate

    if truncated:
        assembled += _TRUNCATION_NOTE

    return assembled


_cached_block: str | None = None


def _build_semantic_prompt_block() -> tuple[str, bool]:
    """Return ``(block, is_fallback)``, memoising only a successful block.

    Introspection is a metadata query, not something to repeat on every turn
    or every tool call, so the result is cached here after first use. A
    *failed* introspection (empty objects list) is deliberately not cached:
    caching it would freeze the "introspection was unavailable" fallback text
    for the process lifetime even after the database recovers. ``is_fallback``
    lets callers that build their own derived, memoised value (e.g. the
    agent's system prompt) apply the same "never cache a failure" rule to
    their own cache instead of substring-matching the fallback text.
    """
    global _cached_block
    if _cached_block is None:
        objects = introspect_schema()
        inventory = render_schema_inventory(objects)
        block = f"{STATIC_GUIDANCE}\n\n{inventory}"
        if not objects:
            return block, True
        _cached_block = block
    return _cached_block, False


def get_semantic_prompt_block() -> str:
    """Build the semantic prompt block once and reuse it on later calls."""
    block, _ = _build_semantic_prompt_block()
    return block


def get_semantic_prompt_block_with_status() -> tuple[str, bool]:
    """Like ``get_semantic_prompt_block``, plus whether it is the fallback."""
    return _build_semantic_prompt_block()


def _reset_cache_for_tests() -> None:
    """Test-only helper to clear the memoised block between test cases."""
    global _cached_block
    _cached_block = None
