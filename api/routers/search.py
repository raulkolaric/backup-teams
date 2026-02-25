"""
api/routers/search.py — GET /search?q=...

Full-text search over indexed PDF content using PostgreSQL tsvector.

Features:
- Portuguese stemming (to_tsquery 'portuguese') — "integrais" matches "integral"
- ts_rank_cd for relevance ordering
- ts_headline for excerpt snippets with matched terms highlighted
- Optional curso_id scoping
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
import asyncpg

from api.dependencies.db import get_pool

router = APIRouter()


@router.get("/")
async def search_files(
    q: str = Query(..., min_length=2, description="Search terms"),
    curso_id: Optional[str] = Query(None, description="Filter to a specific team UUID"),
    limit: int = Query(20, ge=1, le=100),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list:
    """
    Search across the full text of all indexed PDFs.

    Returns files ranked by relevance with a highlighted excerpt showing
    where the query terms appear in the document.

    - Uses PostgreSQL `tsvector` / `to_tsquery` with portuguese stemming
    - `ts_rank_cd` scores by term density + position
    - `ts_headline` returns a snippet with matched terms wrapped in `<b>` tags
    """
    # Sanitize query — replace spaces with & for AND semantics
    # Users can use natural language; we convert to tsquery format
    terms = " & ".join(q.strip().split())

    try:
        rows = await pool.fetch(
            """
            SELECT
                a.id,
                a.file_name AS name,
                a.file_extension AS extension,
                a.s3_key,
                cl.name   AS class_name,

                cr.name   AS curso_name,
                ts_rank_cd(a.content_tsv, query)                            AS rank,
                ts_headline(
                    'portuguese',
                    left(a.content_text, 50000),  -- headline limit
                    query,
                    'MaxWords=35, MinWords=15, StartSel=<b>, StopSel=</b>'
                )                                                            AS excerpt
            FROM archive a
            JOIN class cl  ON cl.id  = a.class_id
            JOIN curso cr  ON cr.id  = cl.curso_id,
            to_tsquery('portuguese', $1) query
            WHERE a.content_tsv @@ query
              AND ($2::uuid IS NULL OR cl.curso_id = $2::uuid)
            ORDER BY rank DESC
            LIMIT $3
            """,
            terms, curso_id, limit,
        )
    except asyncpg.exceptions.InvalidTextRepresentationError:
        raise HTTPException(status_code=400, detail="Invalid search query format.")
    except Exception as exc:
        # tsquery parse errors come back as DataError
        if "syntax error" in str(exc).lower():
            raise HTTPException(status_code=400, detail=f"Invalid query syntax: {exc}")
        raise

    return [dict(r) for r in rows]
