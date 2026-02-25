"""
api/routers/files.py — file listing and individual file download.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
import asyncpg

from api.dependencies.db import get_pool
from api.services.s3 import generate_presigned_url

router = APIRouter()


@router.get("/")
async def list_files(
    class_id:  Optional[str] = Query(None),
    curso_id:  Optional[str] = Query(None),
    extension: Optional[str] = Query(None, description="e.g. 'pdf', 'zip'"),
    limit:  int = Query(50,  ge=1, le=200),
    offset: int = Query(0,   ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list:
    """
    Paginated file listing, filterable by class, team, or extension.
    """
    rows = await pool.fetch(
        """
        SELECT
            a.id, a.file_name AS name, a.file_extension AS extension, a.s3_key, a.etag,
            cl.name  AS class_name,
            cr.name  AS curso_name
        FROM archive a
        JOIN class cl ON cl.id = a.class_id
        JOIN curso cr ON cr.id = cl.curso_id
        WHERE ($1::uuid IS NULL OR a.class_id  = $1::uuid)
          AND ($2::uuid IS NULL OR cl.curso_id = $2::uuid)
          AND ($3::text IS NULL OR a.file_extension = $3)
        ORDER BY cr.name, cl.name, a.file_name
        LIMIT $4 OFFSET $5
        """,
        class_id, curso_id, extension, limit, offset,
    )
    return [dict(r) for r in rows]


@router.get("/{file_id}")
async def get_file(
    file_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """
    Get metadata for a single file plus a presigned S3 download URL.
    The URL expires in 1 hour — the client should download immediately.
    """
    row = await pool.fetchrow(
        """
        SELECT
            a.id, a.file_name AS name, a.file_extension AS extension, a.s3_key, a.etag,
            cl.name  AS class_name,
            cr.name  AS curso_name
        FROM archive a
        JOIN class cl ON cl.id = a.class_id
        JOIN curso cr ON cr.id = cl.curso_id
        WHERE a.id = $1::uuid

        """,
        file_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="File not found.")

    result = dict(row)

    if row["s3_key"]:
        try:
            result["download_url"] = generate_presigned_url(row["s3_key"])
        except Exception:
            result["download_url"] = None
    else:
        result["download_url"] = None

    return result
