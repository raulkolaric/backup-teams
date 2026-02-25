"""
api/routers/classes.py — Endpoints for querying classes and their files.
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_classes():
    """
    List all classes (Teams channels).

    TODO: return class + curso + professor joined data, paginated.
    """
    return {"message": "list_classes — not yet implemented"}


@router.get("/{class_id}/files")
async def list_class_files(class_id: str):
    """
    List all files belonging to a specific class.

    TODO: query archive table filtered by class_id with pagination.
    """
    return {"message": f"list_class_files({class_id}) — not yet implemented"}
