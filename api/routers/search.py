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
    limit: int = Query(5, ge=1, le=100),
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
    # Pass the exact raw phrase to phraseto_tsquery to enforce word order and distance
    terms = q.strip()

    try:
        rows = await pool.fetch(
            """
            WITH top_docs AS (
                SELECT
                    a.id,
                    a.file_name AS name,
                    a.file_extension AS extension,
                    a.s3_key,
                    cl.name AS class_name,
                    cr.name AS curso_name,
                    a.content_text,
                    ts_rank_cd(a.content_tsv, query) AS doc_rank,
                    query
                FROM archive a
                JOIN class cl ON cl.id = a.class_id
                JOIN curso cr ON cr.id = cl.curso_id,
                phraseto_tsquery('portuguese', $1) query
                WHERE a.content_tsv @@ query
                  AND ($2::uuid IS NULL OR cl.curso_id = $2::uuid)
                ORDER BY doc_rank DESC
                LIMIT $3
            ),
            paragraphs AS (
                SELECT
                    id, name, extension, s3_key, class_name, curso_name, doc_rank, query,
                    unnest(string_to_array(content_text, E'\n\n')) AS paragraph
                FROM top_docs
            ),
            ranked_paragraphs AS (
                SELECT
                    id, name, extension, s3_key, class_name, curso_name, doc_rank, query, paragraph,
                    ts_rank_cd(to_tsvector('portuguese', paragraph), query) AS para_rank
                FROM paragraphs
                WHERE to_tsvector('portuguese', paragraph) @@ query
            ),
            best_paragraphs AS (
                SELECT DISTINCT ON (id)
                    id, name, extension, s3_key, class_name, curso_name, doc_rank,
                    ts_headline(
                        'portuguese',
                        paragraph,
                        query,
                        'HighlightAll=TRUE, StartSel=<b>, StopSel=</b>'
                    ) AS excerpt
                FROM ranked_paragraphs
                ORDER BY id, para_rank DESC
            )
            SELECT
                id, name, extension, s3_key, class_name, curso_name, doc_rank AS rank, excerpt
            FROM best_paragraphs
            ORDER BY rank DESC;
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
