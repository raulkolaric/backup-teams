"""
api/main.py — FastAPI application entry point.

Run locally:
    uvicorn api.main:app --reload

The app is intentionally minimal right now. As the project evolves:
  - Authentication (Bearer token / API key) goes in api/dependencies/auth.py
  - DB pool lifecycle goes in api/dependencies/db.py
  - Business logic stays out of routers (goes in services/ or src/)
"""
from fastapi import FastAPI

from api.routers import files, classes

app = FastAPI(
    title="Backup Teams API",
    description="REST API for querying the Teams file backup database.",
    version="0.1.0",
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(files.router,   prefix="/files",   tags=["Files"])
app.include_router(classes.router, prefix="/classes", tags=["Classes"])


# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["Meta"])
async def health() -> dict:
    """Liveness probe — returns 200 if the API process is alive."""
    return {"status": "ok"}
