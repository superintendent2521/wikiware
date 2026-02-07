"""
Upload routes for WikiWare.
Handles file upload operations.
"""

import hashlib
from datetime import datetime, timezone
from typing import ByteString
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from loguru import logger

from ...config import ALLOWED_IMAGE_TYPES, MAX_FILE_SIZE
from ...database import get_image_hashes_collection
from ...middleware.auth_middleware import AuthMiddleware
from ...services.storage_service import (
    StorageError,
    build_public_url,
    upload_image_bytes,
)
from ...utils.validation import sanitize_filename
from ...types import get_state


router = APIRouter()



def _matches_magic_signature(content_type: str, data: bytes) -> bool:
    if content_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if content_type == "image/gif"
        return data.startswith(b"GIF87a") or data.startswith(b"GIF98a")

    magic_prefixes = {
        "image/jpeg": b"\xFF\xD8\xFF",
        "image/png": b"\x89PNG\r\n\x1A\n",
    }

    expected = magic_prefixes.get(content_type)
    return expected is not None and data.startswith(expected)


# old but should still work
def _content_type_to_ext(content_type: str, original_ext: str) -> Optional[str]:
    extension_map = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }
    if content_type in extension_map:
        return extension_map[content_type]
    if original_ext:
        ext = original_ext.lower()
        if ext in {"jpg", "jpeg", "png", "gif", "webp"}:
            return "jpg" if ext == "jpeg" else ext
    return None

def _to_utc_datetime(value) -> datetime:
    """
    Normalize storage timestamps to a timezone-aware datetime in UTC.

    Accepts:
      - datetime (naive assumed UTC, aware converted to UTC)
      - int/float epoch seconds or milliseconds
      - ISO string (best-effort)
    """
    if value is None:
        return datetime.now(timezone.utc)

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, (int, float)):
        # Heuristic: if it's too large, treat as milliseconds.
        # 1e12 ms ~= 2001-09-09; current epoch ms is ~1.7e12+
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    # Fallback: don't fail writes because of unexpected type
    return datetime.now(timezone.utc)



@router.post("/upload-image")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    csrf_protect=depends() # type: ignore
):
    """
     Upload an image file to object storage, dedupe by sha256, and store metadata.
     Fixes the 'datetime expected but got int' issue by normalizing timestamps.
     """
     try:
         # Ensure form is parsed so csrf lib can read from request body (if it relies on request._form)
         try:
             form = await request.form()
             _ = list(form.keys())
         except Exception:
             pass

         await csrf_protect.validate_csrf(request)

         user = await AuthMiddleware.require_auth(request)

         feature_flags = get_state(request).feature_flags
         if not feature_flags.image_upload_enabled and not user.get("is_admin", False):
             return JSONResponse(
                 status_code=403,
                 content={"error": "Image uploading is currently disabled by an administrator."},
             )

         # Content-type allowlist
         if file.content_type not in ALLOWED_IMAGE_TYPES:
             return JSONResponse(
                 status_code=400,
                 content={"error": "Invalid file type. Only JPEG, PNG, GIF, and WebP images are allowed."},
             )

         # Magic bytes check
         header = await file.read(256)
         await file.seek(0)
         if not _matches_magic_signature(file.content_type, header):
             return JSONResponse(
                 status_code=400,
                 content={"error": "Invalid file type. File does not match expected image signature."},
             )

         # Basic filename/extension sanity
         original_ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else ""
         sanitized = sanitize_filename(file.filename or "")
         if (not sanitized) or (".." in sanitized) or ("\0" in sanitized):
             return JSONResponse(status_code=400, content={"error": "Invalid filename."})

         file_extension = _content_type_to_ext(file.content_type, original_ext)
         if not file_extension:
             return JSONResponse(
                 status_code=400,
                 content={"error": "Invalid file extension. Only .jpg, .jpeg, .png, .gif, and .webp are allowed."},
             )

         # Read the file once (simple + reliable).
         # If you want true streaming, we can rewrite for chunked hashing + upload.
         file_content = await file.read()
         await file.seek(0)

         # Size enforcement (works even if UploadFile.size is missing)
         if len(file_content) > MAX_FILE_SIZE:
             return JSONResponse(
                 status_code=400,
                 content={"error": f"File too large. Maximum file size is {MAX_FILE_SIZE // (1024 * 1024)}MB."},
             )

         sha256_hash = hashlib.sha256(file_content).hexdigest()

         # Dedupe
         collection = get_image_hashes_collection()
         if collection is not None:
             existing = await collection.find_one({"sha256": sha256_hash})
             if existing:
                 duplicate_url = existing.get("url") or build_public_url(existing["filename"])
                 if not existing.get("url"):
                     await collection.update_one(
                         {"filename": existing["filename"]},
                         {"$set": {"url": duplicate_url}},
                     )
                 return JSONResponse(status_code=200, content={"url": duplicate_url, "filename": existing["filename"]})

         unique_filename = f"{uuid.uuid4()}.{file_extension}"

         # Upload to storage
         try:
             stored_image = await upload_image_bytes(file_content, unique_filename, file.content_type)
         except StorageError as exc:
             logger.error(f"Image upload failed for '{unique_filename}': {exc}")
             return JSONResponse(
                 status_code=500,
                 content={"error": "Failed to store the image. Please try again later."},
             )

         # Normalize metadata (fix: stored_image.modified might be int)
         modified_dt = _to_utc_datetime(getattr(stored_image, "modified", None))
         size_val = int(getattr(stored_image, "size", len(file_content)) or len(file_content))
         url_val = getattr(stored_image, "url", None) or build_public_url(unique_filename)

         # Store hash + metadata
         if collection is not None:
             await collection.update_one(
                 {"filename": unique_filename},
                 {
                     "$set": {
                         "filename": unique_filename,
                         "sha256": sha256_hash,
                         "size": size_val,
                         "modified": modified_dt,
                         "url": url_val,
                     }
                 },
                 upsert=True,
             )

         logger.info(f"Image uploaded: {unique_filename}")
         return JSONResponse(status_code=200, content={"url": url_val, "filename": unique_filename})

     except CsrfProtectError as e:
         logger.error(f"CSRF error uploading image: {e.message}")
         return JSONResponse(status_code=e.status_code, content={"error": e.message})
     except HTTPException as e:
         logger.error(f"HTTP error uploading image: {e.detail}")
         return JSONResponse(status_code=e.status_code, content={"error": e.detail})
     except Exception as e:
         logger.exception(f"Error uploading image: {e}")
         return JSONResponse(status_code=500, content={"error": "Failed to upload image"})
