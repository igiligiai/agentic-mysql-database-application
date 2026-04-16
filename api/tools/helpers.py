from __future__ import annotations

import re

from fastapi import HTTPException

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def infer_layer(table_name: str) -> str:
    n = table_name.lower()
    if "bronze" in n or "raw" in n or "landing" in n or "stg_" in n:
        return "bronze"
    if "silver" in n or "_clean" in n or "_typed" in n:
        return "silver"
    if "gold" in n or "vw_" in n or "_summary" in n or "_report" in n:
        return "gold"
    if n.startswith("vw"):
        return "gold"
    return "silver"


def validate_identifier(name: str, label: str = "identifier") -> str:
    if not name or not _IDENTIFIER_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {name!r}")
    return f"`{name}`"
