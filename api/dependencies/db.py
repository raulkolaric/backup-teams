"""
api/dependencies/db.py

FastAPI lifespan: opens asyncpg pool on startup, closes on shutdown.
All routers receive the pool via Depends(get_pool).

Usage in a router:
    from api.dependencies.db import get_pool

    @router.get("/")
    async def list_files(pool=Depends(get_pool)):
        rows = await pool.fetch("SELECT ...")
"""
import os
from contextlib import asynccontextmanager

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, Request

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open pool on startup, close on shutdown. Pool stored on app.state."""
    dsn = (
        f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
    )
    app.state.pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    yield
    await app.state.pool.close()


async def get_pool(request: Request) -> asyncpg.Pool:
    """FastAPI dependency — injects the shared connection pool into any route."""
    return request.app.state.pool
