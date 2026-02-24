from __future__ import annotations

from pathlib import Path


SQL_DIR = Path(__file__).with_name("sql")


def load_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8").strip()
