"""
alembic/env.py — Alembic migration environment.

Reads DB connection details from environment variables (loaded from .env
via python-dotenv if running locally). Works identically in Docker where
env vars are injected directly.

We use the synchronous SQLAlchemy URL (postgresql+psycopg2) because Alembic's
default runner is synchronous. The app itself uses asyncpg at runtime — that's
fine, these are separate concerns.
"""
import os
from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context

# ── Load .env (no-op if vars already set by Docker / shell) ──────────────────
load_dotenv(Path(__file__).parent.parent / ".env")


def _build_url() -> str:
    user     = os.environ["DB_USER"]
    password = os.environ.get("DB_PASSWORD", "")
    host     = os.environ.get("DB_HOST", "localhost")
    port     = os.environ.get("DB_PORT", "5432")
    name     = os.environ["DB_NAME"]
    auth     = f"{user}:{password}@" if password else f"{user}@"
    return f"postgresql+psycopg2://{auth}{host}:{port}/{name}"


# ── Alembic Config object ─────────────────────────────────────────────────────
config = context.config
config.set_main_option("sqlalchemy.url", _build_url())

# ── Logging ───────────────────────────────────────────────────────────────────
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── No declarative metadata — we write plain SQL migrations ──────────────────
target_metadata = None


# ── Migration runners ─────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (outputs SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the live DB."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
