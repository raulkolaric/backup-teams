"""
api/routers/files.py — Endpoints for querying archived files.

These are stubs ready to be filled in tomorrow when the DB pool
dependency is wired up.
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_files():
    """
    List all archived files.

    TODO: accept query params (class_id, extension, contributed_by)
    and paginate results.
    """
    return {"message": "list_files — not yet implemented"}


@router.get("/{file_id}")
async def get_file(file_id: str):
    """
    Get metadata for a single archived file by its DB UUID.

    TODO: fetch from archive table and return full record.
    """
    return {"message": f"get_file({file_id}) — not yet implemented"}
