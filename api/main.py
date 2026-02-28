"""
api/main.py — FastAPI application entry point.

Run locally:
    uvicorn api.main:app --reload

Architecture:
  - lifespan: opens asyncpg pool on startup, closes on shutdown
  - All routes receive pool via Depends(get_pool)
  - Business logic stays in routers and api/services/ (not here)
"""
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from api.dependencies.db import lifespan
from api.routers import files, classes, stats, cursos, search

app = FastAPI(
    title="Backup Teams API",
    description=(
        "REST API for querying Teams file backups stored in S3 and RDS. "
        "Includes full-text search over indexed PDFs."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Trust headers like X-Forwarded-Proto and X-Forwarded-For injected by Nginx
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(stats.router,   prefix="/stats",   tags=["Stats"])
app.include_router(cursos.router,  prefix="/cursos",  tags=["Cursos"])
app.include_router(classes.router, prefix="/classes", tags=["Classes"])
app.include_router(files.router,   prefix="/files",   tags=["Files"])
app.include_router(search.router,  prefix="/search",  tags=["Search"])


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["Meta"])
async def health() -> dict:
    """Liveness probe — returns 200 if the API process is alive."""
    return {"status": "ok"}
