from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[2]
FRONT_DIR = BASE_DIR / "front"

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "cars_db"),
    "port": int(os.getenv("DB_PORT", "3306")),
}


def get_port(default: int = 8000) -> int:
    return int(os.getenv("PORT", str(default)))


# ── Context window / message trimming defaults ──────────────────
# All tuneable at runtime via the /context-config endpoint.
CONTEXT_DEFAULTS: dict[str, int | bool] = {
    "max_messages": int(os.getenv("CTX_MAX_MESSAGES", "40")),
    "max_tool_result_chars": int(os.getenv("CTX_MAX_TOOL_RESULT_CHARS", "4000")),
    "keep_system": True,
    "keep_last_n_pairs": int(os.getenv("CTX_KEEP_LAST_N_PAIRS", "10")),
    "trim_enabled": os.getenv("CTX_TRIM_ENABLED", "true").lower() == "true",
}

# ── Query safety defaults ───────────────────────────────────────
# max_rows is the hard ceiling the SQL guard enforces by rewriting the query.
# statement_timeout_ms is applied per connection checkout in db.py.
QUERY_DEFAULTS: dict[str, int] = {
    "max_rows": int(os.getenv("QUERY_MAX_ROWS", "1000")),
    "statement_timeout_ms": int(os.getenv("QUERY_TIMEOUT_MS", "5000")),
}
