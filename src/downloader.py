"""
src/downloader.py — File download + S3 upload + conflict/version management.

Flow
----
1. Check DB — if etag matches, skip entirely.
2. If etag differs (file updated), rename existing local file to a backup copy.
3. Download the file bytes from the Graph API.
4. Write bytes to local disk.
5. Upload bytes to S3 (asyncio.to_thread — boto3 is synchronous).
6. Update the archive table with new etag + s3_key.

Conflict Resolution (when etag changes)
----------------------------------------
Old file is renamed to:  {stem}_backup_{UTC timestamp}{suffix}
New file is downloaded to the original path.
The old backup file is NOT tracked in the DB (it is a safety copy only).

S3 Key Scheme
-------------
  backup_teams/{sanitized_team_name}/{sanitized_channel_name}/{file_name}

The team and channel names are passed in via the item metadata stored on
local_path; we derive them from the path components relative to DOWNLOAD_ROOT.
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
from src.utils import versioned_backup_path

log = logging.getLogger("backup_teams.downloader")

_S3_BUCKET = os.environ.get("S3_BUCKET", "")


def _build_s3_key(local_path: Path) -> str:
    """
    Derive a namespaced S3 key from the local file path.

    local_path example:
      /data/downloads/Calculus/General/notes.pdf

    The DOWNLOAD_ROOT prefix is stripped, giving:
      Calculus/General/notes.pdf

    Which becomes the S3 key:
      backup_teams/Calculus/General/notes.pdf
    """
    download_root = Path(os.environ.get("DOWNLOAD_ROOT", "./downloads")).resolve()
    try:
        relative = local_path.resolve().relative_to(download_root)
    except ValueError:
        # Fallback: just use the filename
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
) -> None:
    """
    Download a single file from the Graph API drive and upload it to S3.

    Steps:
    1. Check DB — if the stored etag matches the current etag, skip.
    2. If etag differs (file changed), rename existing local file to a
       backup copy, then download the new version.
    3. If file is brand new, download directly.
    4. Write to local disk.
    5. Upload to S3 (skipped gracefully if S3_BUCKET is not configured).
    6. Update the archive table with the new record.
    """
    item_id   = item["id"]
    etag      = item.get("eTag", item.get("id"))   # eTag may be absent on some tenants
    file_name = item["name"]
    extension = Path(file_name).suffix.lstrip(".").lower() or "bin"

    # ── Step 1: Check if up-to-date ───────────────────────────────────────────
    if await db_mod.is_file_current(pool, item_id, etag):
        log.info("[SKIP] %s (already downloaded, same version)", file_name)
        return

    # ── Step 2: Conflict — file exists but changed ────────────────────────────
    stored_etag = await db_mod.get_archive_etag(pool, item_id)
    if stored_etag is not None and local_path.exists():
        backup = versioned_backup_path(local_path)
        local_path.rename(backup)
        log.info("[CONFLICT] %-50s → backed up as %s", file_name, backup.name)

    # ── Step 3 + 4: Download and write to disk ────────────────────────────────
    log.info("[DL] %s …", file_name)
    content = await graph.download_file(drive_id, item_id)
    local_path.write_bytes(content)
    log.info("[OK] %-50s  (%d KB)", file_name, len(content) // 1024)

    # ── Step 5: Upload to S3 ──────────────────────────────────────────────────
    s3_key = None
    if _S3_BUCKET:
        key = _build_s3_key(local_path)
        try:
            s3_key = await asyncio.to_thread(
                storage.upload_file, _S3_BUCKET, key, content
            )
            log.info("[S3] %s → s3://%s/%s", file_name, _S3_BUCKET, s3_key)
        except Exception as exc:
            log.warning("[S3] Upload failed for %s: %s (local copy kept)", file_name, exc)
    else:
        log.debug("[S3] S3_BUCKET not set — skipping upload for %s", file_name)

    # ── Step 6: Persist record ────────────────────────────────────────────────
    await db_mod.upsert_archive(
        pool,
        class_id=class_id,
        file_name=file_name,
        file_extension=extension,
        local_path=str(local_path),
        drive_item_id=item_id,
        etag=etag,
        s3_key=s3_key,
    )
