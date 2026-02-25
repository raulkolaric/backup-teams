"""
api/routers/classes.py — class (channel) listing and file retrieval.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
import asyncpg

from api.dependencies.db import get_pool

router = APIRouter()


@router.get("/")
async def list_classes(
    curso_id:  Optional[str] = Query(None),
    semester:  Optional[str] = Query(None, description="e.g. '2025/2'"),
    year:      Optional[int] = Query(None),
    limit:  int = Query(50,  ge=1, le=200),
    offset: int = Query(0,   ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list:
    """
    List all classes (channels), optionally filtered by team, semester, or year.
    """
    rows = await pool.fetch(
        """
        SELECT
            cl.id, cl.name, cl.semester, cl.class_year,
            cr.name AS curso_name,
            COUNT(a.id) AS file_count
        FROM class cl
        JOIN curso cr ON cr.id = cl.curso_id
        LEFT JOIN archive a ON a.class_id = cl.id
        WHERE ($1::uuid IS NULL OR cl.curso_id  = $1::uuid)
          AND ($2::text IS NULL OR cl.semester   = $2)
          AND ($3::int  IS NULL OR cl.class_year = $3)
        GROUP BY cl.id, cl.name, cl.semester, cl.class_year, cr.name
        ORDER BY cr.name, cl.name
        LIMIT $4 OFFSET $5
        """,
        curso_id, semester, year, limit, offset,
    )
    return [dict(r) for r in rows]


@router.get("/{class_id}/files")
async def list_class_files(
    class_id: str,
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0,  ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list:
    """
    All files belonging to a specific class (channel), paginated.
    """
    rows = await pool.fetch(
        """
        SELECT id, file_name AS name, file_extension AS extension, s3_key, etag
        FROM archive
        WHERE class_id = $1::uuid
        ORDER BY file_name
        LIMIT $2 OFFSET $3
        """,
        class_id, limit, offset,
    )
    return [dict(r) for r in rows]
