"""
src/db.py — asyncpg connection pool + all SQL queries.

Design:
  - One pool is created at startup and reused for the entire run.
  - All public functions accept the pool as first argument (dependency
    injection → easy to test / mock).
  - Schema is applied on first connection via db/schema.sql so the tool
    is self-bootstrapping (no manual psql step needed).
"""
import os
import logging
from pathlib import Path
from typing import Optional
from uuid import UUID

import asyncpg

log = logging.getLogger("backup_teams.db")

_SCHEMA_PATH = Path(__file__).parent.parent / "db" / "schema.sql"


# ─── Pool lifecycle ────────────────────────────────────────────────────────────

async def init_pool() -> asyncpg.Pool:
    """
    Create a connection pool and apply the schema (idempotent).
    Reads DB_* variables from the environment.
    """
    dsn = (
        f"postgresql://{os.environ['DB_USER']}"
        f"{(':' + os.environ['DB_PASSWORD']) if os.environ.get('DB_PASSWORD') else ''}"
        f"@{os.environ.get('DB_HOST', 'localhost')}:{os.environ.get('DB_PORT', '5432')}"
        f"/{os.environ['DB_NAME']}"
    )
    pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)

    # Apply schema (CREATE TABLE IF NOT EXISTS — safe to run every time)
    schema_sql = _SCHEMA_PATH.read_text()
    async with pool.acquire() as conn:
        await conn.execute(schema_sql)

    log.info("Database pool initialised and schema applied.")
    return pool


# ─── Professor ─────────────────────────────────────────────────────────────────

async def upsert_professor(
    pool: asyncpg.Pool,
    *,
    name: str,
    email: str,
) -> UUID:
    """Insert a professor if not already present; return their UUID."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO professor (name, email)
            VALUES ($1, $2)
            ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            name, email,
        )
    return row["id"]


# ─── Curso ─────────────────────────────────────────────────────────────────────

async def upsert_curso(
    pool: asyncpg.Pool,
    *,
    name: str,
    teams_id: str,
) -> UUID:
    """Insert a curso (Teams Team) if not already present; return its UUID."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO curso (name, teams_id)
            VALUES ($1, $2)
            ON CONFLICT (teams_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            name, teams_id,
        )
    return row["id"]


# ─── Class ─────────────────────────────────────────────────────────────────────

async def upsert_class(
    pool: asyncpg.Pool,
    *,
    name: str,
    curso_id: UUID,
    professor_id: Optional[UUID],
    semester: str,
    class_year: int,
    teams_channel_id: str,
) -> UUID:
    """Insert or update a class (Teams Channel); return its UUID."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO class
                (name, curso_id, professor_id, semester, class_year, teams_channel_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (teams_channel_id) DO UPDATE
                SET name         = EXCLUDED.name,
                    professor_id = EXCLUDED.professor_id,
                    semester     = EXCLUDED.semester,
                    class_year   = EXCLUDED.class_year
            RETURNING id
            """,
            name, curso_id, professor_id, semester, class_year, teams_channel_id,
        )
    return row["id"]


# ─── Archive ───────────────────────────────────────────────────────────────────

async def get_archive_etag(
    pool: asyncpg.Pool,
    drive_item_id: str,
) -> Optional[str]:
    """
    Return the stored ETag for a drive item, or None if it has never been
    downloaded before.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT etag FROM archive WHERE drive_item_id = $1",
            drive_item_id,
        )
    return row["etag"] if row else None


async def is_file_current(
    pool: asyncpg.Pool,
    drive_item_id: str,
    current_etag: str,
) -> bool:
    """
    Return True when the file is already on disk AND its etag hasn't changed
    — i.e. we can safely skip it.
    """
    stored = await get_archive_etag(pool, drive_item_id)
    return stored is not None and stored == current_etag


async def upsert_archive(
    pool: asyncpg.Pool,
    *,
    class_id: UUID,
    file_name: str,
    file_extension: str,
    local_path: str,
    drive_item_id: str,
    etag: str,
) -> UUID:
    """Insert or update an archive record after a successful download."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO archive
                (class_id, file_name, file_extension, local_path, drive_item_id, etag)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (drive_item_id) DO UPDATE
                SET file_name      = EXCLUDED.file_name,
                    file_extension = EXCLUDED.file_extension,
                    local_path     = EXCLUDED.local_path,
                    etag           = EXCLUDED.etag,
                    updated_at     = NOW()
            RETURNING id
            """,
            class_id, file_name, file_extension, local_path, drive_item_id, etag,
        )
    return row["id"]
