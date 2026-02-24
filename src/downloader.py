"""
src/downloader.py — File download + conflict/version management.

Conflict Resolution (when etag changes)
----------------------------------------
Old file is renamed to:  {stem}_backup_{UTC timestamp}{suffix}
New file is downloaded to the original path.

The Archive table is then updated to reflect the new etag and path.
The old backup file is NOT tracked in the DB (it's just a safety copy).
"""
import logging
from pathlib import Path
from uuid import UUID

import asyncpg

from src.graph_client import GraphClient
from src import db as db_mod
from src.utils import versioned_backup_path

log = logging.getLogger("backup_teams.downloader")


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
    Download a single file from the Graph API drive.

    Steps:
    1. Check DB — if the stored etag matches the current etag, skip.
    2. If etag differs (file changed), rename existing local file to a
       backup copy, then download the new version.
    3. If file is brand new, download directly.
    4. Update the archive table with the new record.
    """
    item_id    = item["id"]
    etag       = item.get("eTag", item.get("id"))   # eTag may be absent on some tenants
    file_name  = item["name"]
    extension  = Path(file_name).suffix.lstrip(".").lower() or "bin"

    # ── Step 1: Check if up-to-date ──────────────────────────────────────────
    if await db_mod.is_file_current(pool, item_id, etag):
        log.info("[SKIP] %s (already downloaded, same version)", file_name)
        return

    # ── Step 2: Conflict — file exists but changed ───────────────────────────
    stored_etag = await db_mod.get_archive_etag(pool, item_id)
    if stored_etag is not None and local_path.exists():
        backup = versioned_backup_path(local_path)
        local_path.rename(backup)
        log.info("[CONFLICT] %-50s → backed up as %s", file_name, backup.name)

    # ── Step 3: Download ──────────────────────────────────────────────────────
    log.info("[DL] %s …", file_name)
    content = await graph.download_file(drive_id, item_id)
    local_path.write_bytes(content)
    log.info("[OK] %-50s  (%d KB)", file_name, len(content) // 1024)

    # ── Step 4: Persist record ───────────────────────────────────────────────
    await db_mod.upsert_archive(
        pool,
        class_id=class_id,
        file_name=file_name,
        file_extension=extension,
        local_path=str(local_path),
        drive_item_id=item_id,
        etag=etag,
    )
