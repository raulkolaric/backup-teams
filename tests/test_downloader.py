"""
tests/test_downloader.py — Unit tests for the skip/download/conflict logic
including the S3 upload step introduced in Phase B.

All external I/O (DB pool, Graph API, S3, filesystem) is mocked so these
tests run in-memory with zero network/database/AWS dependencies.

Test matrix:
  1. test_skip_when_etag_matches       — same eTag in DB  → nothing called
  2. test_download_when_new            — file not in DB   → downloaded, S3 upload, DB record
  3. test_conflict_renames_old_file    — eTag changed     → backup rename, new download, S3 upload
  4. test_s3_failure_is_nonfatal       — S3 upload throws → local file kept, DB record still written
  5. test_skip_s3_when_bucket_not_set  — S3_BUCKET=""     → storage.upload_file never called
"""
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

DRIVE_ID   = "drive-abc"
ITEM_ID    = "item-001"
ETAG       = "v1.0"
NEW_ETAG   = "v2.0"
FILE_NAME  = "lecture_notes.pdf"
FILE_BYTES = b"%PDF-1.4 fake content"
S3_BUCKET  = "backup-teams-files-rk"
S3_KEY     = f"backup_teams/{FILE_NAME}"


def _make_item(etag: str = ETAG) -> dict:
    return {"id": ITEM_ID, "name": FILE_NAME, "eTag": etag, "file": {}}


def _make_graph(file_bytes: bytes = FILE_BYTES) -> MagicMock:
    graph = MagicMock()
    graph.download_file = AsyncMock(return_value=file_bytes)
    return graph


def _make_pool() -> MagicMock:
    return MagicMock()


# ── Test 1: Skip when eTag matches ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skip_when_etag_matches(tmp_path: Path):
    """Same eTag in DB → zero calls to Graph or S3."""
    from src import downloader

    graph = _make_graph()
    pool  = _make_pool()

    with (
        patch("src.downloader.db_mod.is_file_current",  new=AsyncMock(return_value=True)),
        patch("src.downloader.db_mod.get_archive_etag", new=AsyncMock(return_value=ETAG)),
        patch("src.downloader.db_mod.upsert_archive",   new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file") as mock_s3,
    ):
        await downloader.download_item(
            graph, pool,
            drive_id=DRIVE_ID,
            item=_make_item(ETAG),
            class_id=None,
            local_path=tmp_path / FILE_NAME,
        )

    graph.download_file.assert_not_called()
    mock_s3.assert_not_called()
    mock_upsert.assert_not_called()


# ── Test 2: Download and upload when brand-new ────────────────────────────────

@pytest.mark.asyncio
async def test_download_when_new(tmp_path: Path):
    """File not in DB → downloaded, uploaded to S3, DB record created."""
    from src import downloader

    graph      = _make_graph()
    pool       = _make_pool()
    local_path = tmp_path / FILE_NAME

    with (
        patch("src.downloader.db_mod.is_file_current",  new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.get_archive_etag", new=AsyncMock(return_value=None)),
        patch("src.downloader.db_mod.upsert_archive",   new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file", return_value=S3_KEY) as mock_s3,
        patch.dict(os.environ, {"S3_BUCKET": S3_BUCKET}),
    ):
        # Reset the cached bucket value so the env patch takes effect
        downloader._S3_BUCKET = S3_BUCKET
        await downloader.download_item(
            graph, pool,
            drive_id=DRIVE_ID,
            item=_make_item(ETAG),
            class_id=None,
            local_path=local_path,
        )

    graph.download_file.assert_called_once_with(DRIVE_ID, ITEM_ID)
    assert local_path.read_bytes() == FILE_BYTES
    mock_s3.assert_called_once_with(S3_BUCKET, mock_s3.call_args[0][1], FILE_BYTES)
    mock_upsert.assert_called_once()
    _, kwargs = mock_upsert.call_args
    assert kwargs["s3_key"] == S3_KEY


# ── Test 3: Conflict — eTag changed, rename old file ─────────────────────────

@pytest.mark.asyncio
async def test_conflict_renames_old_file(tmp_path: Path):
    """eTag changed → old file renamed to _backup_*, new version downloaded and uploaded."""
    from src import downloader

    graph      = _make_graph()
    pool       = _make_pool()
    local_path = tmp_path / FILE_NAME
    local_path.write_bytes(b"old PDF content")

    with (
        patch("src.downloader.db_mod.is_file_current",  new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.get_archive_etag", new=AsyncMock(return_value=ETAG)),
        patch("src.downloader.db_mod.upsert_archive",   new=AsyncMock()),
        patch("src.downloader.storage.upload_file", return_value=S3_KEY),
        patch.dict(os.environ, {"S3_BUCKET": S3_BUCKET}),
    ):
        downloader._S3_BUCKET = S3_BUCKET
        await downloader.download_item(
            graph, pool,
            drive_id=DRIVE_ID,
            item=_make_item(NEW_ETAG),
            class_id=None,
            local_path=local_path,
        )

    assert local_path.read_bytes() == FILE_BYTES
    backups = [f for f in tmp_path.iterdir() if f.name != FILE_NAME and "backup" in f.name]
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"old PDF content"


# ── Test 4: S3 upload failure is non-fatal ────────────────────────────────────

@pytest.mark.asyncio
async def test_s3_failure_is_nonfatal(tmp_path: Path):
    """S3 upload throws → local file is kept, DB record is still written with s3_key=None."""
    from src import downloader

    graph      = _make_graph()
    pool       = _make_pool()
    local_path = tmp_path / FILE_NAME

    with (
        patch("src.downloader.db_mod.is_file_current",  new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.get_archive_etag", new=AsyncMock(return_value=None)),
        patch("src.downloader.db_mod.upsert_archive",   new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file", side_effect=Exception("AWS error")),
        patch.dict(os.environ, {"S3_BUCKET": S3_BUCKET}),
    ):
        downloader._S3_BUCKET = S3_BUCKET
        await downloader.download_item(
            graph, pool,
            drive_id=DRIVE_ID,
            item=_make_item(ETAG),
            class_id=None,
            local_path=local_path,
        )

    assert local_path.read_bytes() == FILE_BYTES   # local copy preserved
    mock_upsert.assert_called_once()
    _, kwargs = mock_upsert.call_args
    assert kwargs["s3_key"] is None                # recorded without S3 key


# ── Test 5: S3 skipped when bucket not configured ────────────────────────────

@pytest.mark.asyncio
async def test_skip_s3_when_bucket_not_set(tmp_path: Path):
    """S3_BUCKET="" → storage.upload_file never called, DB record written without s3_key."""
    from src import downloader

    graph      = _make_graph()
    pool       = _make_pool()
    local_path = tmp_path / FILE_NAME

    with (
        patch("src.downloader.db_mod.is_file_current",  new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.get_archive_etag", new=AsyncMock(return_value=None)),
        patch("src.downloader.db_mod.upsert_archive",   new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file") as mock_s3,
    ):
        downloader._S3_BUCKET = ""   # simulate unconfigured
        await downloader.download_item(
            graph, pool,
            drive_id=DRIVE_ID,
            item=_make_item(ETAG),
            class_id=None,
            local_path=local_path,
        )

    mock_s3.assert_not_called()
    mock_upsert.assert_called_once()
    _, kwargs = mock_upsert.call_args
    assert kwargs["s3_key"] is None
