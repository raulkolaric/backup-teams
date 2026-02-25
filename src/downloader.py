"""
src/downloader.py — S3-direct file pipeline.

Flow (S3-direct mode, no local disk writes)
--------------------------------------------
1. Check DB — if etag matches, skip entirely (S3 already has the file).
2. Download file bytes from the Graph API.
3. Upload bytes directly to S3.
4. Write the s3_key to the archive table (local_path = NULL).

Returns
-------
"skip"  — file was already current in S3 and DB, nothing done
"ok"    — file was downloaded and uploaded to S3 successfully
"error" — something failed (logged); no DB record written

S3 Key Scheme
-------------
  backup_teams/{team_name}/{channel_name}/{file_name}

Derived from the local_path parameter, which is still passed in by the
caller for key construction even though the file is never written to disk.
"""
import asyncio
import logging
import os
from pathlib import Path
from uuid import UUID

import asyncpg

from src.graph_client import GraphClient
from src import db as db_mod
from src import storage

log = logging.getLogger("backup_teams.downloader")

_S3_BUCKET = os.environ.get("S3_BUCKET", "")


def _build_s3_key(local_path: Path) -> str:
    """
    Derive an S3 key from the intended local path.

    The local path is never written to — it is used only as a structured
    reference to carry team/channel/filename information from the scraper.
    """
    download_root = Path(os.environ.get("DOWNLOAD_ROOT", "./downloads")).resolve()
    try:
        relative = local_path.resolve().relative_to(download_root)
    except ValueError:
        relative = Path(local_path.name)
    return f"backup_teams/{relative}"


async def download_item(
    graph: GraphClient,
    pool: asyncpg.Pool,
    *,
    drive_id: str,
    item: dict,
    class_id: UUID,
    local_path: Path,
) -> str:
    """
    Download a file from the Graph API and store it in S3.

    Returns "skip", "ok", or "error".
    local_path is used ONLY for S3 key derivation, never written to.
    """
    item_id   = item["id"]
    etag      = item.get("eTag", item.get("id"))
    file_name = item["name"]
    extension = Path(file_name).suffix.lstrip(".").lower() or "bin"

    # ── Step 1: Skip if up-to-date ────────────────────────────────────────────
    if await db_mod.is_file_current(pool, item_id, etag):
        log.info("[SKIP] %s (etag matches — already in S3)", file_name)
        return "skip"

    # ── Step 2: Download bytes from Graph API ─────────────────────────────────
    log.info("[DL] %s …", file_name)
    content = await graph.download_file(drive_id, item_id)
    log.info("[OK] %-50s  (%d KB)", file_name, len(content) // 1024)

    # ── Step 3: Upload directly to S3 ─────────────────────────────────────────
    s3_key = None
    if _S3_BUCKET:
        key = _build_s3_key(local_path)
        try:
            s3_key = await asyncio.to_thread(
                storage.upload_file, _S3_BUCKET, key, content
            )
            log.info("[S3] %-50s → s3://%s/%s", file_name, _S3_BUCKET, s3_key)
        except Exception as exc:
            log.warning("[S3] Upload failed for %s: %s", file_name, exc)
            return "error"
    else:
        log.warning("[S3] S3_BUCKET not configured — file %s not stored", file_name)
        return "error"

    # ── Step 4: Persist record to DB ──────────────────────────────────────────
    await db_mod.upsert_archive(
        pool,
        class_id=class_id,
        file_name=file_name,
        file_extension=extension,
        local_path=None,
        drive_item_id=item_id,
        etag=etag,
        s3_key=s3_key,
    )
    return "ok"
