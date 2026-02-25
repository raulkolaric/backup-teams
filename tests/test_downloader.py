"""
tests/test_downloader.py — Unit tests for the skip-already-downloaded logic.

All external I/O (DB pool, Graph API, filesystem) is mocked so these tests
run in-memory with zero network/database dependencies.

Test matrix:
  1. test_skip_when_etag_matches     — same eTag in DB  → download never called
  2. test_download_when_new          — file not in DB   → downloaded & recorded
  3. test_conflict_renames_old_file  — eTag changed     → old file renamed, new version downloaded
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ────────────────────────────────────────────────────────────────────

DRIVE_ID  = "drive-abc"
ITEM_ID   = "item-001"
ETAG      = "v1.0"
NEW_ETAG  = "v2.0"
FILE_NAME = "lecture_notes.pdf"
FILE_BYTES = b"%PDF-1.4 fake content"


def _make_item(etag: str = ETAG) -> dict:
    return {"id": ITEM_ID, "name": FILE_NAME, "eTag": etag, "file": {}}


def _make_graph(file_bytes: bytes = FILE_BYTES) -> MagicMock:
    graph = MagicMock()
    graph.download_file = AsyncMock(return_value=file_bytes)
    return graph


def _make_pool() -> MagicMock:
    """Return a minimal pool mock (the real calls go through patched db_mod)."""
    return MagicMock()


# ── Test 1: Skip when eTag matches ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skip_when_etag_matches(tmp_path: Path):
    """
    GIVEN  a file already in the DB with the same eTag
    WHEN   download_item is called
    THEN   graph.download_file is NEVER invoked  (pure skip)
    """
    from src import downloader

    graph = _make_graph()
    pool  = _make_pool()
    local_path = tmp_path / FILE_NAME

    with (
        patch("src.downloader.db_mod.is_file_current",  new=AsyncMock(return_value=True)),
        patch("src.downloader.db_mod.get_archive_etag", new=AsyncMock(return_value=ETAG)),
        patch("src.downloader.db_mod.upsert_archive",   new=AsyncMock()) as mock_upsert,
    ):
        await downloader.download_item(
            graph, pool,
            drive_id=DRIVE_ID,
            item=_make_item(ETAG),
            class_id=None,
            local_path=local_path,
        )

    graph.download_file.assert_not_called()    # ← key assertion: skip happened
    mock_upsert.assert_not_called()            # DB write skipped too
    assert not local_path.exists()             # no file written to disk


# ── Test 2: Download when file is brand-new ───────────────────────────────────

@pytest.mark.asyncio
async def test_download_when_new(tmp_path: Path):
    """
    GIVEN  a file NOT yet in the DB (first run)
    WHEN   download_item is called
    THEN   the file is downloaded and stored, and the archive record is created
    """
    from src import downloader

    graph = _make_graph()
    pool  = _make_pool()
    local_path = tmp_path / FILE_NAME

    with (
        patch("src.downloader.db_mod.is_file_current",  new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.get_archive_etag", new=AsyncMock(return_value=None)),
        patch("src.downloader.db_mod.upsert_archive",   new=AsyncMock()) as mock_upsert,
    ):
        await downloader.download_item(
            graph, pool,
            drive_id=DRIVE_ID,
            item=_make_item(ETAG),
            class_id=None,
            local_path=local_path,
        )

    graph.download_file.assert_called_once_with(DRIVE_ID, ITEM_ID)   # downloaded
    assert local_path.read_bytes() == FILE_BYTES                      # saved to disk
    mock_upsert.assert_called_once()                                  # recorded in DB


# ── Test 3: Conflict — eTag changed, rename old file ─────────────────────────

@pytest.mark.asyncio
async def test_conflict_renames_old_file(tmp_path: Path):
    """
    GIVEN  a file already in DB with an OLD eTag, AND the file exists on disk
    WHEN   download_item is called with a NEW eTag
    THEN   the old file is renamed to a _backup_* copy and the new version is
           downloaded to the original path
    """
    from src import downloader

    graph = _make_graph()
    pool  = _make_pool()
    local_path = tmp_path / FILE_NAME

    # Pre-create the "old" file on disk
    old_content = b"old PDF content"
    local_path.write_bytes(old_content)

    with (
        patch("src.downloader.db_mod.is_file_current",  new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.get_archive_etag", new=AsyncMock(return_value=ETAG)),  # old etag
        patch("src.downloader.db_mod.upsert_archive",   new=AsyncMock()) as mock_upsert,
    ):
        await downloader.download_item(
            graph, pool,
            drive_id=DRIVE_ID,
            item=_make_item(NEW_ETAG),          # ← new eTag (file changed upstream)
            class_id=None,
            local_path=local_path,
        )

    # New version downloaded to the original path
    graph.download_file.assert_called_once_with(DRIVE_ID, ITEM_ID)
    assert local_path.read_bytes() == FILE_BYTES

    # A backup copy of the old file must exist somewhere in tmp_path
    backups = [
        f for f in tmp_path.iterdir()
        if f.name != FILE_NAME and "backup" in f.name
    ]
    assert len(backups) == 1, f"Expected 1 backup file, found: {[f.name for f in tmp_path.iterdir()]}"
    assert backups[0].read_bytes() == old_content   # backup contains old content

    mock_upsert.assert_called_once()   # DB updated with new etag
