"""
api/services/s3_stats.py — S3 bucket storage statistics.

Provides a single async function, get_bucket_stats(), that paginates
list_objects_v2 to compute total object count and total size for the
backup bucket.

⚠️ Production note: For very large buckets (millions of objects) the
paginator can be slow (1-2 s per 1 000 objects). A production-grade
swap would read from CloudWatch `BucketSizeBytes` and
`NumberOfObjects` metrics (updated daily by AWS) — the calling router
would not need to change because the interface is identical.
"""
import asyncio
import os
from functools import lru_cache

import boto3

# ── Singleton boto3 client ────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _s3_client():
    """Return a cached synchronous boto3 S3 client."""
    return boto3.client(
        "s3",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )


# ── Core sync implementation (runs in a thread) ───────────────────────────────


def _paginate_bucket(bucket: str) -> dict:
    """
    Paginate list_objects_v2 to sum total objects and total bytes.

    Returns a dict with:
      - object_count  (int)
      - total_bytes   (int)
    """
    paginator = _s3_client().get_paginator("list_objects_v2")
    object_count = 0
    total_bytes = 0

    try:
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                object_count += 1
                total_bytes += obj.get("Size", 0)
    except Exception as e:
        print(f"S3 pagination error: {e}")
        object_count = -1

    return {"object_count": object_count, "total_bytes": total_bytes}


def _format_bytes(num_bytes: int) -> str:
    """Human-readable file size string (e.g. '4.2 GB')."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:,.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:,.1f} PB"


# ── Public async API ──────────────────────────────────────────────────────────


async def get_bucket_stats() -> dict:
    """
    Async wrapper: runs the synchronous S3 paginator in a thread pool
    so FastAPI's event loop is not blocked.

    Returns:
    {
        "storage_bytes": int,
        "storage_human": str,   # e.g. "4.2 GB"
        "s3_object_count": int,
    }
    """
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        # Gracefully degrade — bucket not configured in this environment
        return {
            "storage_bytes": 0,
            "storage_human": "N/A",
            "s3_object_count": 0,
        }

    result = await asyncio.to_thread(_paginate_bucket, bucket)
    return {
        "storage_bytes": result["total_bytes"],
        "storage_human": _format_bytes(result["total_bytes"]),
        "s3_object_count": result["object_count"],
    }
