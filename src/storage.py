"""
src/storage.py — AWS S3 operations.

Wraps boto3 with three operations used by the downloader:
  - upload_file   : put bytes into S3, return the s3_key
  - file_exists   : HEAD check — skip re-upload if already there
  - generate_presigned_url : time-limited download link for the API

All functions are synchronous (boto3 is sync). They run inside
asyncio.to_thread() in the downloader so they don't block the event loop.

Credentials are read from the environment:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET
"""
import logging
import os

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger("backup_teams.storage")

# ── Lazy singleton client ──────────────────────────────────────────────────────

_s3_client = None


def _client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )
    return _s3_client


# ── Public API ─────────────────────────────────────────────────────────────────

def upload_file(bucket: str, key: str, data: bytes) -> str:
    """
    Upload raw bytes to S3.

    Returns the s3_key on success so callers can store it in the DB.
    Raises on any AWS error — let the caller decide whether to retry.
    """
    _client().put_object(Bucket=bucket, Key=key, Body=data)
    log.info("[S3] uploaded s3://%s/%s (%d KB)", bucket, key, len(data) // 1024)
    return key


def file_exists(bucket: str, key: str) -> bool:
    """
    Return True if the object already exists in S3 (cheap HEAD request).

    Used to skip re-uploading files that haven't changed.
    """
    try:
        _client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def generate_presigned_url(bucket: str, key: str, expires: int = 3600) -> str:
    """
    Generate a time-limited pre-signed download URL.

    Default TTL: 1 hour.  Used by the REST API so clients can download
    files directly from S3 without the API proxying the bytes.
    """
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )
