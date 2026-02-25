"""
api/routers/cursos.py — endpoints for Teams (cursos) and their classes.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
import asyncpg

from api.dependencies.db import get_pool

router = APIRouter()


@router.get("/")
async def list_cursos(
    semester: Optional[str] = Query(None, description="e.g. '2025/2'"),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list:
    """
    List all Teams (cursos) with their file counts.
    Optionally filter by semester.
    """
    rows = await pool.fetch(
        """
        SELECT
            c.id,
            c.name,
            c.teams_id,
            COUNT(a.id) AS file_count
        FROM curso c
        LEFT JOIN class cl  ON cl.curso_id = c.id
        LEFT JOIN archive a ON a.class_id  = cl.id
        WHERE ($1::text IS NULL OR cl.semester = $1)
        GROUP BY c.id, c.name, c.teams_id
        ORDER BY c.name
        """,
        semester,
    )
    return [dict(r) for r in rows]


@router.get("/{curso_id}/classes")
async def list_curso_classes(
    curso_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> list:
    """
    List all classes (channels) within a team, with file counts.
    """
    rows = await pool.fetch(
        """
        SELECT
            cl.id,
            cl.name,
            cl.semester,
            cl.class_year,
            COUNT(a.id) AS file_count
        FROM class cl
        LEFT JOIN archive a ON a.class_id = cl.id
        WHERE cl.curso_id = $1::uuid
        GROUP BY cl.id, cl.name, cl.semester, cl.class_year
        ORDER BY cl.name
        """,
        curso_id,
    )
    return [dict(r) for r in rows]
