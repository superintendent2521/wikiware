"""
Storage helpers for file uploads.
Supports S3-compatible backends with a local fallback for development.
"""

from __future__ import annotations

import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import aiofiles
import asyncio
import os
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from ..config import (
    ALLOWED_IMAGE_TYPES,
    MONGODB_URL,
    MONGODB_DB_NAME,
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

def _safe_local_image_path(filename: str) -> Path:
    """
    Validate and return a safe local path for an image filename.
    Raises StorageError on path traversal or if not within UPLOAD_DIR.
    """
    # Normalize and join the path
    raw_path = os.path.normpath(os.path.join(UPLOAD_DIR, filename))
    # Ensure absolute path
    abs_upload_dir = os.path.abspath(UPLOAD_DIR)
    abs_path = os.path.abspath(raw_path)
    # Compare common path prefix
    if not abs_path.startswith(abs_upload_dir + os.path.sep):
        logger.warning(f"Attempted access outside upload dir: {filename}")
        raise StorageError("Invalid image path.")
    return Path(abs_path)


class StorageError(Exception):
    """Raised when an object storage operation fails."""


@dataclass(slots=True)
class StoredImage:
    filename: str
    url: str
    size: int
    modified: int


# Global S3 client and lock for thread-safe initialization
_S3_CLIENT = None
_S3_EXIT_STACK: AsyncExitStack | None = None
_S3_LOCK = asyncio.Lock()


def _s3_enabled() -> bool:
    return bool(S3_ENDPOINT and S3_ACCESS_KEY and S3_SECRET_KEY)


def is_s3_configured() -> bool:
    """Expose S3 configuration check for callers."""
    return _s3_enabled()


def _normalise_endpoint(endpoint: str) -> str:
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint.rstrip("/")
    return f"https://{endpoint.rstrip('/')}"


async def _reset_s3_client() -> None:
    """Close and clear the cached S3 client."""
    global _S3_CLIENT, _S3_EXIT_STACK
    stack = _S3_EXIT_STACK
    _S3_CLIENT = None
    _S3_EXIT_STACK = None
    if stack is not None:
        try:
            await stack.aclose()
        except Exception as exc:
            logger.warning(f"Failed to close S3 client cleanly: {exc}")


async def _handle_client_attribute_error(operation: str, exc: AttributeError) -> None:
    """Translate attribute errors into storage errors and reset the client."""
    await _reset_s3_client()
    logger.exception(
        f"S3 client missing expected attribute during {operation}: {exc}"
    )
    raise StorageError("Object storage client is not usable.") from exc


async def _get_s3_client():
    """Get or create async S3 client with connection pooling."""
    global _S3_CLIENT, _S3_EXIT_STACK
    if _S3_CLIENT is not None:
        if hasattr(_S3_CLIENT, "get_object"):
            return _S3_CLIENT
        logger.warning("Cached S3 client missing methods; resetting cached client.")
        await _reset_s3_client()
    
    if not _s3_enabled():
        raise StorageError("S3 client requested but storage is not configured.")
    
    async with _S3_LOCK:
        if _S3_CLIENT is not None:
            if hasattr(_S3_CLIENT, "get_object"):
                return _S3_CLIENT
            logger.warning("Cached S3 client missing methods after lock; resetting.")
            await _reset_s3_client()
        
        # Configure S3 client with connection pooling
        config_kwargs: Dict[str, Any] = {
            "signature_version": "s3v4",
            "max_pool_connections": 50,  # Configure connection pool
            "retries": {
                "max_attempts": 3,
                "mode": "adaptive"
            }
        }
        
        if S3_FORCE_PATH_STYLE:
            config_kwargs["s3"] = {"addressing_style": "path"}
        
        from aiobotocore.session import get_session
        session = get_session()
        
        stack = AsyncExitStack()
        try:
            client_cm = session.create_client(
                "s3",
                endpoint_url=_normalise_endpoint(S3_ENDPOINT),
                aws_access_key_id=S3_ACCESS_KEY,
                aws_secret_access_key=S3_SECRET_KEY,
                config=Config(**config_kwargs),
            )
            client = await stack.enter_async_context(client_cm)
        except Exception:
            await stack.aclose()
            raise
        
        if not hasattr(client, "get_object"):
            await stack.aclose()
            logger.error("Created S3 client missing required methods; aborting.")
            raise StorageError("Failed to initialise object storage client.")
        
        _S3_CLIENT = client
        _S3_EXIT_STACK = stack
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


async def upload_image_bytes(
    data: bytes,
    filename: str,
    content_type: str | None = None,
) -> StoredImage:
    """Persist an uploaded image to the configured backend."""
    now = int(time.time())
    if _s3_enabled():
        client = await _get_s3_client()
        extra_args: Dict[str, Any] = {}
        if content_type and content_type in ALLOWED_IMAGE_TYPES:
            extra_args["ContentType"] = content_type
        
        try:
            await client.put_object(
                Bucket=S3_BUCKET,
                Key=_object_key(filename),
                Body=data,
                **extra_args,
            )
        except AttributeError as exc:
            await _handle_client_attribute_error("upload", exc)
        except (BotoCoreError, ClientError) as exc:
            logger.exception(f"Failed to upload image '{filename}' to S3: {exc}")
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
        async with aiofiles.open(file_path, "wb") as file_obj:
            await file_obj.write(data)
        stat = file_path.stat()
    except OSError as exc:
        logger.exception(f"Failed to write image '{filename}' locally: {exc}")
        raise StorageError("Local upload failed.") from exc
    return StoredImage(
        filename=filename,
        url=f"/static/uploads/{filename}",
        size=stat.st_size,
        modified=int(stat.st_mtime),
    )


async def list_images() -> List[Dict[str, Any]]:
    """Return metadata for uploaded images."""
    if _s3_enabled():
        client = await _get_s3_client()
        items: List[Dict[str, Any]] = []
        
        try:
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=IMAGE_PREFIX):
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
        except AttributeError as exc:
            await _handle_client_attribute_error("list images", exc)
        except (BotoCoreError, ClientError) as exc:
            logger.exception(f"Failed to list images from S3: {exc}")
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
            except OSError as exc:
                logger.warning(f"Failed to stat image {entry}: {exc}")
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


async def download_image_bytes(filename: str) -> bytes:
    """Retrieve the raw bytes for an uploaded image."""
    s3_missing = False
    if _s3_enabled():
        client = await _get_s3_client()
        try:
            response = await client.get_object(Bucket=S3_BUCKET, Key=_object_key(filename))
            body = response.get("Body")
            if body is None:
                raise StorageError("Empty response body from object storage.")
            data = await body.read()
        except AttributeError as exc:
            await _handle_client_attribute_error("download", exc)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                s3_missing = True
                logger.warning(
                    f"Image '{filename}' missing in S3 (code={error_code or 'unknown'}); checking local storage."
                )
            else:
                logger.exception(f"Failed to download image '{filename}' from S3: {exc}")
                raise StorageError("Download from object storage failed.") from exc
        except BotoCoreError as exc:
            logger.exception(f"Failed to download image '{filename}' from S3: {exc}")
            raise StorageError("Download from object storage failed.") from exc
        except StorageError as exc:
            logger.exception(f"Failed to download image '{filename}' from S3: {exc}")
            raise
        else:
            return data

    file_path = _safe_local_image_path(filename)
    if not file_path.exists():
        if s3_missing:
            logger.warning(f"Image '{filename}' not found in S3 or local storage.")
        else:
            logger.warning(f"Image '{filename}' not found in local storage.")
        raise StorageError("Image not found.")
    
    try:
        async with aiofiles.open(file_path, "rb") as file_obj:
            return await file_obj.read()
    except OSError as exc:
        logger.exception(f"Failed to read local image '{filename}': {exc}")
        raise StorageError("Local image read failed.") from exc


async def delete_image(filename: str) -> None:
    """Delete an uploaded image."""
    if _s3_enabled():
        client = await _get_s3_client()
        try:
            await client.delete_object(Bucket=S3_BUCKET, Key=_object_key(filename))
        except AttributeError as exc:
            await _handle_client_attribute_error("delete", exc)
        except (BotoCoreError, ClientError) as exc:
            logger.exception(f"Failed to delete image '{filename}' from S3: {exc}")
            raise StorageError("Delete from object storage failed.") from exc
        return

    file_path = _safe_local_image_path(filename)
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError as exc:
        logger.exception(f"Failed to delete local image '{filename}': {exc}")
        raise StorageError("Local delete failed.") from exc


async def image_exists(filename: str) -> bool:
    """Efficiently check whether an image exists in storage."""
    if _s3_enabled():
        client = await _get_s3_client()
        try:
            await client.head_object(Bucket=S3_BUCKET, Key=_object_key(filename))
            return True
        except AttributeError as exc:
            await _handle_client_attribute_error("existence check", exc)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                return False
            logger.exception(
                f"Failed to check existence for image '{filename}' in S3: {exc}"
            )
            raise StorageError("Image existence check failed.") from exc
        except BotoCoreError as exc:
            logger.exception(
                f"BotoCore error while checking image '{filename}' existence: {exc}"
            )
            raise StorageError("Image existence check failed.") from exc

    file_path = _safe_local_image_path(filename)
    return file_path.exists()
