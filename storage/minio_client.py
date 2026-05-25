"""
MinIO client wrapper for storing raw media (audio, images, video frames).
"""
from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Optional

import structlog
from minio import Minio
from minio.error import S3Error

from config.settings import settings

logger = structlog.get_logger(__name__)

_client: Optional[Minio] = None


def get_minio() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def ensure_buckets() -> None:
    """Create required buckets if they don't exist."""
    client = get_minio()
    for bucket in (settings.minio_bucket_media, settings.minio_bucket_raw):
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
                logger.info("minio.bucket_created", bucket=bucket)
        except S3Error as e:
            logger.error("minio.bucket_error", bucket=bucket, error=str(e))


def upload_bytes(
    data: bytes,
    content_type: str,
    bucket: str | None = None,
    object_name: str | None = None,
    extension: str = "",
) -> str:
    """Upload raw bytes and return the MinIO object path."""
    client = get_minio()
    bucket = bucket or settings.minio_bucket_media
    if object_name is None:
        object_name = f"{uuid.uuid4()}{extension}"
    try:
        client.put_object(
            bucket,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        logger.debug("minio.uploaded", bucket=bucket, object=object_name)
        return f"{bucket}/{object_name}"
    except S3Error as e:
        logger.error("minio.upload_failed", error=str(e))
        raise


def upload_file(local_path: str | Path, bucket: str | None = None) -> str:
    """Upload a file from disk and return the MinIO object path."""
    path = Path(local_path)
    bucket = bucket or settings.minio_bucket_media
    object_name = f"{uuid.uuid4()}{path.suffix}"
    client = get_minio()
    try:
        client.fput_object(bucket, object_name, str(path))
        return f"{bucket}/{object_name}"
    except S3Error as e:
        logger.error("minio.fupload_failed", error=str(e))
        raise


def download_bytes(minio_path: str) -> bytes:
    """Download an object given 'bucket/object_name' path."""
    client = get_minio()
    bucket, object_name = minio_path.split("/", 1)
    response = client.get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def get_presigned_url(minio_path: str, expires_seconds: int = 3600) -> str:
    """Generate a presigned GET URL."""
    from datetime import timedelta

    client = get_minio()
    bucket, object_name = minio_path.split("/", 1)
    return client.presigned_get_object(
        bucket, object_name, expires=timedelta(seconds=expires_seconds)
    )
