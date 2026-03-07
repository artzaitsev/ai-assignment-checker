from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from typing import Any

import pytest

try:
    asyncpg_module = importlib.import_module("asyncpg")
except ModuleNotFoundError:  # pragma: no cover
    asyncpg_module = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"


def _migration_paths(*, direction: str) -> list[Path]:
    paths = sorted(MIGRATIONS_DIR.glob(f"*.{direction}.sql"))
    if direction == "down":
        paths.reverse()
    return paths


def postgres_dsn() -> str:
    return os.getenv("TEST_DATABASE_URL", os.getenv("DATABASE_URL", "postgres://app:app@localhost:5432/app"))


def require_postgres() -> str:
    if asyncpg_module is None:
        pytest.skip("asyncpg dependency is not available")

    dsn = postgres_dsn()

    async def _probe() -> None:
        conn = await _asyncpg().connect(dsn=dsn)
        await conn.close()

    try:
        asyncio.run(_probe())
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"postgres is not reachable at {dsn}: {exc}")

    return dsn


async def reset_public_schema(*, dsn: str) -> None:
    conn = await _asyncpg().connect(dsn=dsn)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE;")
        await conn.execute("CREATE SCHEMA public;")
        await conn.execute("GRANT ALL ON SCHEMA public TO public;")
    finally:
        await conn.close()


async def apply_up(*, dsn: str) -> None:
    conn = await _asyncpg().connect(dsn=dsn)
    try:
        for path in _migration_paths(direction="up"):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def apply_down(*, dsn: str) -> None:
    conn = await _asyncpg().connect(dsn=dsn)
    try:
        for path in _migration_paths(direction="down"):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


def _asyncpg() -> Any:
    if asyncpg_module is None:  # pragma: no cover
        raise RuntimeError("asyncpg is unavailable")
    return asyncpg_module
