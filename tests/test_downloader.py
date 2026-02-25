"""
tests/test_downloader.py — Unit tests for S3-direct pipeline.

S3-direct mode: files go Graph API → S3, never touching local disk.
local_path is passed in for key derivation only but never written to.

Test matrix:
  1. test_skip_when_etag_matches    — etag in DB matches → nothing called
  2. test_download_and_upload       — new file → downloaded, S3 upload, DB record (local_path=None)
  3. test_s3_failure_aborts_db      — S3 upload throws → DB record NOT written (no dangling record)
  4. test_no_bucket_aborts          — S3_BUCKET="" → no DB record written
  5. test_etag_changed_overwrites   — etag differs → re-downloaded, S3 overwritten, DB updated
"""
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


# ── Test 1: Skip when etag matches ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skip_when_etag_matches(tmp_path: Path):
    """Same etag in DB → no Graph call, no S3 call."""
    from src import downloader

    graph = _make_graph()
    pool  = MagicMock()

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=True)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file") as mock_s3,
    ):
        await downloader.download_item(
            graph, pool,
            drive_id=DRIVE_ID, item=_make_item(ETAG),
            class_id=None, local_path=tmp_path / FILE_NAME,
        )

    graph.download_file.assert_not_called()
    mock_s3.assert_not_called()
    mock_upsert.assert_not_called()


# ── Test 2: New file — download, upload, persist ──────────────────────────────

@pytest.mark.asyncio
async def test_download_and_upload(tmp_path: Path):
    """New file → downloaded, uploaded to S3, DB record with local_path=None."""
    from src import downloader

    local_path = tmp_path / FILE_NAME

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file", return_value=S3_KEY),
        patch.dict(os.environ, {"S3_BUCKET": S3_BUCKET}),
    ):
        downloader._S3_BUCKET = S3_BUCKET
        await downloader.download_item(
            _make_graph(), MagicMock(),
            drive_id=DRIVE_ID, item=_make_item(ETAG),
            class_id=None, local_path=local_path,
        )

    # File must NOT be written to disk
    assert not local_path.exists(), "S3-direct mode must not write to disk"

    mock_upsert.assert_called_once()
    _, kwargs = mock_upsert.call_args
    assert kwargs["s3_key"] == S3_KEY
    assert kwargs["local_path"] is None


# ── Test 3: S3 failure → no DB record ────────────────────────────────────────

@pytest.mark.asyncio
async def test_s3_failure_aborts_db(tmp_path: Path):
    """S3 upload fails → DB record is NOT written. No dangling record without a file."""
    from src import downloader

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file", side_effect=Exception("AWS error")),
        patch.dict(os.environ, {"S3_BUCKET": S3_BUCKET}),
    ):
        downloader._S3_BUCKET = S3_BUCKET
        await downloader.download_item(
            _make_graph(), MagicMock(),
            drive_id=DRIVE_ID, item=_make_item(ETAG),
            class_id=None, local_path=tmp_path / FILE_NAME,
        )

    mock_upsert.assert_not_called()


# ── Test 4: No bucket → no DB record ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_bucket_aborts(tmp_path: Path):
    """S3_BUCKET not configured → download happens but nothing is persisted."""
    from src import downloader

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file") as mock_s3,
    ):
        downloader._S3_BUCKET = ""
        await downloader.download_item(
            _make_graph(), MagicMock(),
            drive_id=DRIVE_ID, item=_make_item(ETAG),
            class_id=None, local_path=tmp_path / FILE_NAME,
        )

    mock_s3.assert_not_called()
    mock_upsert.assert_not_called()


# ── Test 5: etag changed → re-upload overwrites S3 ───────────────────────────

@pytest.mark.asyncio
async def test_etag_changed_overwrites(tmp_path: Path):
    """etag changed → re-downloaded, S3 overwritten with new bytes, DB updated."""
    from src import downloader

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file", return_value=S3_KEY) as mock_s3,
        patch.dict(os.environ, {"S3_BUCKET": S3_BUCKET}),
    ):
        downloader._S3_BUCKET = S3_BUCKET
        await downloader.download_item(
            _make_graph(), MagicMock(),
            drive_id=DRIVE_ID, item=_make_item(NEW_ETAG),
            class_id=None, local_path=tmp_path / FILE_NAME,
        )

    mock_s3.assert_called_once()
    mock_upsert.assert_called_once()
    _, kwargs = mock_upsert.call_args
    assert kwargs["etag"] == NEW_ETAG
