"""
Upload routes for WikiWare.
Handles file upload operations.
"""

from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse
import os
import uuid
import shutil
from ..config import UPLOAD_DIR, MAX_FILE_SIZE, ALLOWED_IMAGE_TYPES
from ..utils.validation import sanitize_filename
from loguru import logger

router = APIRouter()


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """Upload an image file."""
    try:
        # Create uploads directory if it doesn't exist
        os.makedirs(UPLOAD_DIR, exist_ok=True)

        # Validate file type
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid file type. Only JPEG, PNG, GIF, and WebP images are allowed."}
            )

        # Validate file size
        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            return JSONResponse(
                status_code=400,
                content={"error": f"File too large. Maximum file size is {MAX_FILE_SIZE // (1024 * 1024)}MB."}
            )

        # Reset file pointer
        await file.seek(0)

        # Generate unique filename
        file_extension = file.filename.split(".")[-1] if "." in file.filename else ""
        sanitized_filename = sanitize_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)

        # Save file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Return success response with image URL
        image_url = f"/static/uploads/{unique_filename}"
        logger.info(f"Image uploaded: {unique_filename}")
        return JSONResponse(
            status_code=200,
            content={"url": image_url, "filename": unique_filename}
        )
    except Exception as e:
        logger.error(f"Error uploading image: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to upload image"}
        )
