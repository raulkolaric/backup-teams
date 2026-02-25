"""
api/services/s3.py

Presigned URL generation for S3 file downloads.
The browser hits S3 directly — no bandwidth cost on the API server.
"""
import os

import boto3
from botocore.exceptions import ClientError

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
    return _s3


def generate_presigned_url(s3_key: str, ttl: int = 3600) -> str:
    """
    Return a presigned GET URL for an S3 object.

    ttl: seconds until the URL expires (default 1 hour).
    Raises ClientError if the key doesn't exist or permissions fail.
    """
    bucket = os.environ["S3_BUCKET"]
    url = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=ttl,
    )
    return url
