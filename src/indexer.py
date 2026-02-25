"""
src/indexer.py — PDF text extraction and FTS indexing pipeline.

Reads PDFs from S3, extracts text with pdfminer.six, and writes it to the
`archive.content_text` column. PostgreSQL trigger auto-updates `content_tsv`.

Run modes:
    python -m src.indexer          # backfill all un-indexed PDFs
    await run_incremental(pool)    # called from main.py after scraping

The pdfminer call is CPU-bound so we use asyncio.to_thread() to avoid
blocking the event loop. Concurrency is capped to avoid OOM on small instances.
"""
import asyncio
import logging
import os
from io import BytesIO

import asyncpg
import boto3
from pdfminer.high_level import extract_text

log = logging.getLogger("backup_teams.indexer")

INDEX_CONCURRENCY = int(os.getenv("INDEX_CONCURRENCY", "4"))


# ─── S3 helper ────────────────────────────────────────────────────────────────

def _fetch_pdf_bytes(bucket: str, s3_key: str) -> bytes:
    """Blocking S3 download — called inside asyncio.to_thread."""
    s3 = boto3.client("s3")
    resp = s3.get_object(Bucket=bucket, Key=s3_key)
    return resp["Body"].read()


def _extract_text_from_bytes(pdf_bytes: bytes) -> str:
    """Blocking pdfminer call — called inside asyncio.to_thread."""
    return extract_text(BytesIO(pdf_bytes)) or ""


# ─── Per-file indexing ────────────────────────────────────────────────────────

async def _index_one(
    pool: asyncpg.Pool,
    bucket: str,
    semaphore: asyncio.Semaphore,
    row: asyncpg.Record,
) -> None:
    file_id = row["id"]
    s3_key  = row["s3_key"]
    name    = row["name"]

    async with semaphore:
        log.info("[INDEX] %s", name)
        try:
            pdf_bytes = await asyncio.to_thread(_fetch_pdf_bytes, bucket, s3_key)
            text      = await asyncio.to_thread(_extract_text_from_bytes, pdf_bytes)
            text      = text.strip()
        except Exception as exc:
            log.warning("[INDEX] Failed to extract %s: %s", name, exc)
            # Write empty string so we don't retry broken PDFs endlessly
            text = ""

        await pool.execute(
            "UPDATE archive SET content_text = $1 WHERE id = $2",
            text, file_id,
        )
        log.info("[INDEX] Done — %d chars extracted from %s", len(text), name)


# ─── Public API ───────────────────────────────────────────────────────────────

async def run_incremental(pool: asyncpg.Pool) -> int:
    """
    Index all PDFs that don't have content_text yet.
    Returns the number of files indexed.
    """
    bucket = os.environ["S3_BUCKET"]
    rows = await pool.fetch(
        """
        SELECT id, file_name AS name, s3_key FROM archive
        WHERE file_extension = 'pdf'
          AND s3_key IS NOT NULL
          AND content_text IS NULL
        ORDER BY created_at DESC
        """
    )

    if not rows:
        log.info("[INDEX] Nothing to index.")
        return 0

    log.info("[INDEX] Indexing %d PDFs…", len(rows))
    semaphore = asyncio.Semaphore(INDEX_CONCURRENCY)
    await asyncio.gather(
        *[_index_one(pool, bucket, semaphore, row) for row in rows],
        return_exceptions=True,
    )
    log.info("[INDEX] Done.")
    return len(rows)


# ─── Standalone backfill ──────────────────────────────────────────────────────

async def _backfill():
    from dotenv import load_dotenv
    load_dotenv()

    dsn = (
        f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
    )
    pool = await asyncpg.create_pool(dsn)
    count = await run_incremental(pool)
    await pool.close()
    print(f"Indexed {count} PDFs.")


if __name__ == "__main__":
    asyncio.run(_backfill())
