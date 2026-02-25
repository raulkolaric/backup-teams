"""
api/routers/stats.py — GET /stats
Returns aggregate counts across the whole database.
"""
from fastapi import APIRouter, Depends
import asyncpg

from api.dependencies.db import get_pool

router = APIRouter()


@router.get("/")
async def get_stats(pool: asyncpg.Pool = Depends(get_pool)) -> dict:
    """
    High-level statistics about the backup corpus.

    Returns:
    - total_files: all archived files
    - total_classes: distinct channels
    - total_cursos: distinct teams
    - indexed_pdfs: PDFs with extracted text (searchable)
    - files_by_extension: breakdown by file type
    """
    totals = await pool.fetchrow(
        """
        SELECT
            COUNT(*)                                            AS total_files,
            COUNT(*) FILTER (WHERE file_extension = 'pdf'
                             AND content_text IS NOT NULL)      AS indexed_pdfs,
            (SELECT COUNT(*) FROM class)                        AS total_classes,
            (SELECT COUNT(*) FROM curso)                        AS total_cursos
        FROM archive
        """
    )

    by_ext = await pool.fetch(
        """
        SELECT file_extension AS extension, COUNT(*) AS cnt
        FROM archive
        WHERE file_extension IS NOT NULL
        GROUP BY file_extension
        ORDER BY cnt DESC
        """
    )


    return {
        "total_files":      totals["total_files"],
        "total_classes":    totals["total_classes"],
        "total_cursos":     totals["total_cursos"],
        "indexed_pdfs":     totals["indexed_pdfs"],
        "files_by_extension": {r["extension"]: r["cnt"] for r in by_ext},
    }
