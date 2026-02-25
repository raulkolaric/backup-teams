"""
tests/test_downloader.py — Unit tests for S3-direct pipeline.

S3-direct mode: files go Graph API → S3, never touching local disk.
download_item() returns "skip", "ok", or "error".

Test matrix:
  1. test_skip_when_etag_matches    — etag in DB → returns "skip", nothing called
  2. test_download_and_upload       — new file → returns "ok", local_path=None in DB
  3. test_s3_failure_returns_error  — S3 upload throws → returns "error", no DB write
  4. test_no_bucket_returns_error   — S3_BUCKET="" → returns "error", no DB write
  5. test_etag_changed_overwrites   — etag differs → downloads again, returns "ok"
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


# ── Test 1: Skip ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skip_when_etag_matches(tmp_path: Path):
    from src import downloader

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=True)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file") as mock_s3,
    ):
        result = await downloader.download_item(
            _make_graph(), MagicMock(),
            drive_id=DRIVE_ID, item=_make_item(ETAG),
            class_id=None, local_path=tmp_path / FILE_NAME,
        )

    assert result == "skip"
    mock_s3.assert_not_called()
    mock_upsert.assert_not_called()


# ── Test 2: New file → "ok" ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_and_upload(tmp_path: Path):
    from src import downloader

    local_path = tmp_path / FILE_NAME

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file", return_value=S3_KEY),
        patch.dict(os.environ, {"S3_BUCKET": S3_BUCKET}),
    ):
        downloader._S3_BUCKET = S3_BUCKET
        result = await downloader.download_item(
            _make_graph(), MagicMock(),
            drive_id=DRIVE_ID, item=_make_item(ETAG),
            class_id=None, local_path=local_path,
        )

    assert result == "ok"
    assert not local_path.exists(), "S3-direct mode must not write to disk"
    mock_upsert.assert_called_once()
    _, kwargs = mock_upsert.call_args
    assert kwargs["s3_key"] == S3_KEY
    assert kwargs["local_path"] is None


# ── Test 3: S3 failure → "error", no DB write ────────────────────────────────

@pytest.mark.asyncio
async def test_s3_failure_returns_error(tmp_path: Path):
    from src import downloader

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file", side_effect=Exception("AWS error")),
        patch.dict(os.environ, {"S3_BUCKET": S3_BUCKET}),
    ):
        downloader._S3_BUCKET = S3_BUCKET
        result = await downloader.download_item(
            _make_graph(), MagicMock(),
            drive_id=DRIVE_ID, item=_make_item(ETAG),
            class_id=None, local_path=tmp_path / FILE_NAME,
        )

    assert result == "error"
    mock_upsert.assert_not_called()


# ── Test 4: No bucket → "error", no DB write ─────────────────────────────────

@pytest.mark.asyncio
async def test_no_bucket_returns_error(tmp_path: Path):
    from src import downloader

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file") as mock_s3,
    ):
        downloader._S3_BUCKET = ""
        result = await downloader.download_item(
            _make_graph(), MagicMock(),
            drive_id=DRIVE_ID, item=_make_item(ETAG),
            class_id=None, local_path=tmp_path / FILE_NAME,
        )

    assert result == "error"
    mock_s3.assert_not_called()
    mock_upsert.assert_not_called()


# ── Test 5: etag changed → "ok", new etag in DB ──────────────────────────────

@pytest.mark.asyncio
async def test_etag_changed_overwrites(tmp_path: Path):
    from src import downloader

    with (
        patch("src.downloader.db_mod.is_file_current", new=AsyncMock(return_value=False)),
        patch("src.downloader.db_mod.upsert_archive",  new=AsyncMock()) as mock_upsert,
        patch("src.downloader.storage.upload_file", return_value=S3_KEY),
        patch.dict(os.environ, {"S3_BUCKET": S3_BUCKET}),
    ):
        downloader._S3_BUCKET = S3_BUCKET
        result = await downloader.download_item(
            _make_graph(), MagicMock(),
            drive_id=DRIVE_ID, item=_make_item(NEW_ETAG),
            class_id=None, local_path=tmp_path / FILE_NAME,
        )

    assert result == "ok"
    mock_upsert.assert_called_once()
    _, kwargs = mock_upsert.call_args
    assert kwargs["etag"] == NEW_ETAG
