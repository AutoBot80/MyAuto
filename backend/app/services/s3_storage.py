"""AWS S3 helpers for dealer-scoped artifacts (uploaded scans, OCR output)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.config import AWS_REGION, S3_DATA_BUCKET, S3_PRESIGNED_EXPIRES_SEC

logger = logging.getLogger(__name__)

_client: Any | None = None


def _s3():
    global _client
    if _client is None:
        _client = boto3.client("s3", region_name=AWS_REGION)
    return _client


def upload_file(local_path: Path, key: str, bucket: str | None = None) -> None:
    b = bucket or S3_DATA_BUCKET
    if not b:
        raise RuntimeError("S3_DATA_BUCKET is not set")
    _s3().upload_file(str(local_path), b, key)


def upload_bytes(data: bytes, key: str, bucket: str | None = None) -> None:
    b = bucket or S3_DATA_BUCKET
    if not b:
        raise RuntimeError("S3_DATA_BUCKET is not set")
    _s3().put_object(Bucket=b, Key=key, Body=data)


def download_bytes(key: str, bucket: str | None = None) -> bytes:
    b = bucket or S3_DATA_BUCKET
    if not b:
        raise RuntimeError("S3_DATA_BUCKET is not set")
    resp = _s3().get_object(Bucket=b, Key=key)
    return resp["Body"].read()


def download_to_path(key: str, dest: Path, bucket: str | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = download_bytes(key, bucket=bucket)
    dest.write_bytes(data)


def object_exists(key: str, bucket: str | None = None) -> bool:
    b = bucket or S3_DATA_BUCKET
    if not b:
        return False
    try:
        _s3().head_object(Bucket=b, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def generate_presigned_get_url(key: str, bucket: str | None = None, expires_in: int | None = None) -> str:
    b = bucket or S3_DATA_BUCKET
    if not b:
        raise RuntimeError("S3_DATA_BUCKET is not set")
    exp = expires_in if expires_in is not None else S3_PRESIGNED_EXPIRES_SEC
    return _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": b, "Key": key},
        ExpiresIn=exp,
    )


def list_objects_with_prefix(prefix: str, bucket: str | None = None) -> list[dict[str, Any]]:
    """Return S3 object summaries (Key, Size, LastModified) for keys starting with ``prefix``."""
    b = bucket or S3_DATA_BUCKET
    if not b:
        return []
    out: list[dict[str, Any]] = []
    paginator = _s3().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=b, Prefix=prefix):
        for obj in page.get("Contents") or []:
            out.append(
                {
                    "Key": obj["Key"],
                    "Size": obj.get("Size", 0),
                    "LastModified": obj.get("LastModified"),
                }
            )
    return out


def list_one_level_prefix(prefix: str, bucket: str | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    """
    List immediate "subfolders" (CommonPrefixes) and files (Contents) under a prefix ending with /.
    ``prefix`` should end with / for predictable one-level listing.
    """
    b = bucket or S3_DATA_BUCKET
    if not b:
        return [], []
    if not prefix.endswith("/"):
        prefix = prefix + "/"
    resp = _s3().list_objects_v2(Bucket=b, Prefix=prefix, Delimiter="/")
    dirs: list[str] = []
    for cp in resp.get("CommonPrefixes") or []:
        p = cp.get("Prefix", "")
        if p.startswith(prefix) and len(p) > len(prefix):
            name = p[len(prefix) :].rstrip("/")
            if name:
                dirs.append(name)
    files: list[dict[str, Any]] = []
    for obj in resp.get("Contents") or []:
        key = obj["Key"]
        if key == prefix:
            continue
        name = key[len(prefix) :]
        if "/" in name:
            continue
        files.append({"name": name, "Key": key, "Size": obj.get("Size", 0), "LastModified": obj.get("LastModified")})
    return dirs, files


def delete_object(key: str, bucket: str | None = None) -> None:
    b = bucket or S3_DATA_BUCKET
    if not b:
        raise RuntimeError("S3_DATA_BUCKET is not set")
    _s3().delete_object(Bucket=b, Key=key)
