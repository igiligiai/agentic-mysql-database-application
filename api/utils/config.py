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
