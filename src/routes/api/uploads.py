"""
Upload routes for WikiWare.
Handles file upload operations.
"""

import hashlib
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
