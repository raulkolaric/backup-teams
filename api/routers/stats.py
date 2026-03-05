"""
api/routers/stats.py — GET /stats
Returns aggregate counts from the database AND storage stats from S3.
"""
import asyncio
from fastapi import APIRouter, Depends
import asyncpg

from api.dependencies.db import get_pool
from api.services.s3_stats import get_bucket_stats

router = APIRouter()


@router.get("/")
async def get_stats(
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """
    High-level statistics about the backup corpus.

    DB stats and S3 bucket stats are fetched concurrently via
    asyncio.gather to minimise total response latency.

    Returns:
    - total_files        : all archived files in the database
    - total_classes      : distinct channels
    - total_cursos       : distinct teams / subject areas
    - indexed_pdfs       : PDFs with extracted, searchable text
    - files_by_extension : breakdown by file type  { "pdf": 120, ... }
    - storage_bytes      : raw byte size of the S3 bucket
    - storage_human      : human-readable size  (e.g. "4.2 GB")
    - s3_object_count    : total objects in S3  (may differ from DB
                           count if any uploads bypassed the scraper)
    """
    # Run DB queries and S3 paginator concurrently
    (totals, by_ext, s3) = await asyncio.gather(
        pool.fetchrow(
            """
            SELECT
                COUNT(*)                                            AS total_files,
                COUNT(*) FILTER (WHERE file_extension = 'pdf'
                                 AND content_text IS NOT NULL)      AS indexed_pdfs,
                (SELECT COUNT(*) FROM class)                        AS total_classes,
                (SELECT COUNT(*) FROM curso)                        AS total_cursos
            FROM archive
            """
        ),
        pool.fetch(
            """
            SELECT file_extension AS extension, COUNT(*) AS cnt
            FROM archive
            WHERE file_extension IS NOT NULL
            GROUP BY file_extension
            ORDER BY cnt DESC
            """
        ),
        get_bucket_stats(),
    )

    return {
        # ── Database stats ──────────────────────────────────
        "total_files":        totals["total_files"],
        "total_classes":      totals["total_classes"],
        "total_cursos":       totals["total_cursos"],
        "indexed_pdfs":       totals["indexed_pdfs"],
        "files_by_extension": {r["extension"]: r["cnt"] for r in by_ext},
        # ── S3 storage stats ────────────────────────────────
        "storage_bytes":      s3["storage_bytes"],
        "storage_human":      s3["storage_human"],
        "s3_object_count":    s3["s3_object_count"],
    }
