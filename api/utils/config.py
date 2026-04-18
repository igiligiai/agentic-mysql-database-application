from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

DB_CONFIG= {
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
CONTEXT_DEFAULTS: dict[str, int] = {
    "max_messages": int(os.getenv("CTX_MAX_MESSAGES", "40")),
    "max_response_tokens": int(os.getenv("CTX_MAX_RESPONSE_TOKENS", "1024")),
}
