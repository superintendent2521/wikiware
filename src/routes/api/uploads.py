"""
Upload routes for WikiWare.
Handles file upload operations (API).
"""

import shutil
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from loguru import logger

from ...config import ALLOWED_IMAGE_TYPES, MAX_FILE_SIZE, UPLOAD_DIR
from ...middleware.auth_middleware import AuthMiddleware
from ...utils.validation import sanitize_filename

router = APIRouter()


@router.post("/upload-image")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    csrf_protect: CsrfProtect = Depends(),
):
    """Upload an image file."""
    try:
        # Ensure form is parsed so fastapi-csrf-protect can read csrf_token from body
        try:
            form = await request.form()
            # No-op; calling form() populates request._form for the CSRF lib
            _ = list(form.keys())
        except Exception:
            pass

        # Validate CSRF token
        await csrf_protect.validate_csrf(request)

        # Check if user is authenticated
        await AuthMiddleware.require_auth(request)

        # Create uploads directory if it doesn't exist
        upload_path = Path(UPLOAD_DIR)
        upload_path.mkdir(parents=True, exist_ok=True)

        # Validate file type by content type and magic bytes
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Invalid file type. Only JPEG, PNG, GIF, and WebP images are allowed."
                },
            )

        # Read first 256 bytes to check magic numbers
        header = await file.read(256)
        await file.seek(0)

        # Check magic numbers for image types
        def _matches_magic_signature(content_type: str, data: bytes) -> bool:
            if content_type == "image/webp":
                return (
                    len(data) >= 12
                    and data.startswith(b"RIFF")
                    and data[8:12] == b"WEBP"
                )
            if content_type == "image/gif":
                return data.startswith(b"GIF87a") or data.startswith(b"GIF89a")
            magic_prefixes = {
                "image/jpeg": b"\xff\xd8\xff",
                "image/png": b"\x89PNG\r\n\x1a\n",
            }
            expected = magic_prefixes.get(content_type)
            return expected is not None and data.startswith(expected)

        if not _matches_magic_signature(file.content_type, header):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Invalid file type. File does not match expected image signature."
                },
            )

        # Validate file size without loading entire file into memory
        if (
            hasattr(file, "size")
            and file.size is not None
            and file.size > MAX_FILE_SIZE
        ):
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"File too large. Maximum file size is {MAX_FILE_SIZE // (1024 * 1024)}MB."
                },
            )

        # If file.size is not available, stream and count bytes
        if not hasattr(file, "size") or file.size is None:
            content_length = 0
            while True:
                chunk = await file.read(8192)
                if not chunk:
                    break
                content_length += len(chunk)
                if content_length > MAX_FILE_SIZE:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": f"File too large. Maximum file size is {MAX_FILE_SIZE // (1024 * 1024)}MB."
                        },
                    )
            await file.seek(0)

        # Generate unique filename
        original_extension = (
            file.filename.split(".")[-1] if "." in file.filename else ""
        )
        sanitized_filename = sanitize_filename(file.filename)

        # Validate extension after sanitization
        if original_extension and original_extension.lower() not in [
            "jpg",
            "jpeg",
            "png",
            "gif",
            "webp",
        ]:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Invalid file extension. Only .jpg, .jpeg, .png, .gif, and .webp are allowed."
                },
            )

        # Map content type to extension for consistency
        extension_map = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/gif": "gif",
            "image/webp": "webp",
        }
        file_extension = extension_map.get(
            file.content_type, original_extension.lower()
        )

        # Ensure sanitized filename doesn't contain dangerous patterns
        if (
            not sanitized_filename
            or ".." in sanitized_filename
            or "\0" in sanitized_filename
        ):
            return JSONResponse(status_code=400, content={"error": "Invalid filename."})

        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        file_path = upload_path / unique_filename

        # Save file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Return success response with image URL
        image_url = f"/static/uploads/{unique_filename}"
        logger.info(f"Image uploaded: {unique_filename}")
        return JSONResponse(
            status_code=200, content={"url": image_url, "filename": unique_filename}
        )
    except CsrfProtectError as e:
        logger.error(f"CSRF error uploading image: {e.message}")
        return JSONResponse(status_code=e.status_code, content={"error": e.message})
    except HTTPException as e:
        # Authentication errors and similar
        logger.error(f"HTTP error uploading image: {e.detail}")
        return JSONResponse(status_code=e.status_code, content={"error": e.detail})
    except Exception as e:
        logger.error(f"Error uploading image: {str(e)}")
        return JSONResponse(
            status_code=500, content={"error": "Failed to upload image"}
        )
