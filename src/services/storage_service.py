"""
Storage helpers for file uploads.
Supports S3-compatible backends with a local fallback for development.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger

from ..config import (
    ALLOWED_IMAGE_TYPES,
    S3_ACCESS_KEY,
    S3_BUCKET,
    S3_ENDPOINT,
    S3_FORCE_PATH_STYLE,
    S3_PUBLIC_URL,
    S3_SECRET_KEY,
    UPLOAD_DIR,
)

IMAGE_PREFIX = "uploads/"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}


class StorageError(Exception):
    """Raised when an object storage operation fails."""


@dataclass(slots=True)
class StoredImage:
    filename: str
    url: str
    size: int
    modified: int


_S3_CLIENT = None


def _s3_enabled() -> bool:
    return bool(S3_ENDPOINT and S3_ACCESS_KEY and S3_SECRET_KEY)


def is_s3_configured() -> bool:
    """Expose S3 configuration check for callers."""
    return _s3_enabled()


def _normalise_endpoint(endpoint: str) -> str:
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint.rstrip("/")
    return f"https://{endpoint.rstrip('/')}"


def _get_s3_client():
    global _S3_CLIENT
    if _S3_CLIENT is not None:
        return _S3_CLIENT
    if not _s3_enabled():
        raise StorageError("S3 client requested but storage is not configured.")
    session = boto3.session.Session()
    config_kwargs: Dict[str, Any] = {"signature_version": "s3v4"}
    if S3_FORCE_PATH_STYLE:
        config_kwargs["s3"] = {"addressing_style": "path"}
    _S3_CLIENT = session.client(
        "s3",
        endpoint_url=_normalise_endpoint(S3_ENDPOINT),
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(**config_kwargs),
    )
    return _S3_CLIENT


def _ensure_local_dir() -> Path:
    upload_path = Path(UPLOAD_DIR)
    upload_path.mkdir(parents=True, exist_ok=True)
    return upload_path


def _object_key(filename: str) -> str:
    return f"{IMAGE_PREFIX}{filename}"


def build_public_url(filename: str) -> str:
    """Return a public URL for an uploaded file."""
    if _s3_enabled():
        key = _object_key(filename)
        if S3_PUBLIC_URL:
            return f"{S3_PUBLIC_URL.rstrip('/')}/{key}"
        return f"/media/uploads/{filename}"
    return f"/static/uploads/{filename}"


def upload_image_bytes(
    data: bytes,
    filename: str,
    content_type: str | None = None,
) -> StoredImage:
    """Persist an uploaded image to the configured backend."""
    now = int(time.time())
    if _s3_enabled():
        client = _get_s3_client()
        extra_args: Dict[str, Any] = {}
        if content_type and content_type in ALLOWED_IMAGE_TYPES:
            extra_args["ContentType"] = content_type
        try:
            client.put_object(
                Bucket=S3_BUCKET,
                Key=_object_key(filename),
                Body=data,
                **extra_args,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception("Failed to upload image '%s' to S3: %s", filename, exc)
            raise StorageError("Upload to object storage failed.") from exc
        return StoredImage(
            filename=filename,
            url=build_public_url(filename),
            size=len(data),
            modified=now,
        )

    upload_path = _ensure_local_dir()
    file_path = upload_path / filename
    try:
        with open(file_path, "wb") as file_obj:
            file_obj.write(data)
        stat = file_path.stat()
    except OSError as exc:  # pragma: no cover
        logger.exception("Failed to write image '%s' locally: %s", filename, exc)
        raise StorageError("Local upload failed.") from exc
    return StoredImage(
        filename=filename,
        url=f"/static/uploads/{filename}",
        size=stat.st_size,
        modified=int(stat.st_mtime),
    )


def list_images() -> List[Dict[str, Any]]:
    """Return metadata for uploaded images."""
    if _s3_enabled():
        client = _get_s3_client()
        paginator = client.get_paginator("list_objects_v2")
        items: List[Dict[str, Any]] = []
        try:
            for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=IMAGE_PREFIX):
                for obj in page.get("Contents", []):
                    key = obj.get("Key", "")
                    suffix = Path(key).suffix.lower()
                    if suffix not in IMAGE_EXTENSIONS:
                        continue
                    filename = key.split("/")[-1]
                    last_modified = obj.get("LastModified")
                    modified_ts = (
                        int(last_modified.timestamp()) if last_modified else int(time.time())
                    )
                    items.append(
                        {
                            "filename": filename,
                            "url": build_public_url(filename),
                            "size": int(obj.get("Size", 0)),
                            "modified": modified_ts,
                        }
                    )
        except (BotoCoreError, ClientError) as exc:
            logger.exception("Failed to list images from S3: %s", exc)
            raise StorageError("Listing images from object storage failed.") from exc
        items.sort(key=lambda item: item["modified"], reverse=True)
        return items

    upload_path = Path(UPLOAD_DIR)
    if not upload_path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for entry in upload_path.iterdir():
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS:
            try:
                stat = entry.stat()
            except OSError as exc:  # pragma: no cover
                logger.warning("Failed to stat image %s: %s", entry, exc)
                continue
            items.append(
                {
                    "filename": entry.name,
                    "url": f"/static/uploads/{entry.name}",
                    "size": stat.st_size,
                    "modified": int(stat.st_mtime),
                }
            )
    items.sort(key=lambda item: item["modified"], reverse=True)
    return items


def download_image_bytes(filename: str) -> bytes:
    """Retrieve the raw bytes for an uploaded image."""
    if _s3_enabled():
        client = _get_s3_client()
        try:
            response = client.get_object(Bucket=S3_BUCKET, Key=_object_key(filename))
            body = response.get("Body")
            if body is None:
                raise StorageError("Empty response body from object storage.")
            data = body.read()
        except (BotoCoreError, ClientError, StorageError) as exc:
            logger.exception("Failed to download image '%s' from S3: %s", filename, exc)
            raise StorageError("Download from object storage failed.") from exc
        return data

    file_path = Path(UPLOAD_DIR) / filename
    try:
        with open(file_path, "rb") as file_obj:
            return file_obj.read()
    except OSError as exc:  # pragma: no cover
        logger.exception("Failed to read local image '%s': %s", filename, exc)
        raise StorageError("Local image read failed.") from exc


def delete_image(filename: str) -> None:
    """Delete an uploaded image."""
    if _s3_enabled():
        client = _get_s3_client()
        try:
            client.delete_object(Bucket=S3_BUCKET, Key=_object_key(filename))
        except (BotoCoreError, ClientError) as exc:
            logger.exception("Failed to delete image '%s' from S3: %s", filename, exc)
            raise StorageError("Delete from object storage failed.") from exc
        return

    file_path = Path(UPLOAD_DIR) / filename
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError as exc:  # pragma: no cover
        logger.exception("Failed to delete local image '%s': %s", filename, exc)
        raise StorageError("Local delete failed.") from exc


def image_exists(filename: str) -> bool:
    """Efficiently check whether an image exists in storage."""
    if _s3_enabled():
        client = _get_s3_client()
        try:
            client.head_object(Bucket=S3_BUCKET, Key=_object_key(filename))
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                return False
            logger.exception(
                "Failed to check existence for image '%s' in S3: %s", filename, exc
            )
            raise StorageError("Image existence check failed.") from exc
        except BotoCoreError as exc:
            logger.exception(
                "BotoCore error while checking image '%s' existence: %s",
                filename,
                exc,
            )
            raise StorageError("Image existence check failed.") from exc

    file_path = Path(UPLOAD_DIR) / filename
    return file_path.exists()
