"""Load the curated semantic layer and render it into the system prompt.

Deliberately not a vector store. The schema is small enough that injecting the
whole glossary beats retrieving part of it. If render_semantics() ever exceeds
MAX_RENDER_BYTES, convert the glossary into a lookup_term tool rather than
growing the prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SEMANTICS_DIR = Path(__file__).resolve().parent
MAX_RENDER_BYTES = 2048


def _read_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"Missing semantics file: {path.name}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or []
    if not isinstance(loaded, list):
        raise ValueError(f"{path.name} must contain a YAML list")
    for entry in loaded:
        if not isinstance(entry, dict):
            raise ValueError(f"{path.name} entries must be mappings, got {entry!r}")
    return loaded


def load_semantics(base: Path = SEMANTICS_DIR) -> dict[str, list[dict[str, Any]]]:
    """Read and validate glossary.yaml and exemplars.yaml."""
    glossary = _read_list(base / "glossary.yaml")
    exemplars = _read_list(base / "exemplars.yaml")

    for entry in glossary:
        if not entry.get("term"):
            raise ValueError(f"Glossary entry has no 'term': {entry!r}")
        if not entry.get("definition"):
            raise ValueError(f"Glossary term {entry['term']!r} has no 'definition'")

    for entry in exemplars:
        if not entry.get("question"):
            raise ValueError(f"Exemplar has no 'question': {entry!r}")
        if not entry.get("sql"):
            raise ValueError(f"Exemplar {entry['question']!r} has no 'sql'")

    return {"glossary": glossary, "exemplars": exemplars}


def render_semantics(data: dict[str, list[dict[str, Any]]]) -> str:
    """Render the semantic layer as a compact Markdown block for the prompt."""
    lines: list[str] = ["## Business glossary", ""]
    for entry in data["glossary"]:
        detail = f"- **{entry['term']}**: {entry['definition']}"
        tables = entry.get("tables")
        if tables:
            detail += f" (tables: {', '.join(tables)})"
        note = entry.get("note")
        if note:
            detail += f" {note}"
        lines.append(detail)

    lines += ["", "## Query exemplars", ""]
    for entry in data["exemplars"]:
        lines.append(f"- Q: {entry['question']}")
        lines.append(f"  SQL: {' '.join(entry['sql'].split())}")
        note = entry.get("note")
        if note:
            lines.append(f"  Why: {note}")

    return "\n".join(lines)
