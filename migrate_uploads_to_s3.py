#!/usr/bin/env python3
"""
Migrate previously uploaded images from local storage into the configured S3 bucket.

Run from the project root:
    python migrate_uploads_to_s3.py [--delete-local]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import mimetypes
import sys
from pathlib import Path

from loguru import logger

from src.config import UPLOAD_DIR
from src.database import db_instance, get_image_hashes_collection
from src.services.storage_service import (
    IMAGE_EXTENSIONS,
    StorageError,
    is_s3_configured,
    upload_image_bytes,
)


async def _upload_file(path: Path) -> tuple[str, str, int, int]:
    """Read local file, upload to S3, and return metadata."""
    data = await asyncio.to_thread(path.read_bytes)
    sha256 = hashlib.sha256(data).hexdigest()
    content_type = mimetypes.guess_type(path.name)[0]
    stored_image = await asyncio.to_thread(
        upload_image_bytes,
        data,
        path.name,
        content_type,
    )
    stat = path.stat()
    size = stat.st_size
    modified = int(stat.st_mtime)
    return sha256, stored_image.url, size, modified


async def migrate(delete_local: bool) -> int:
    if not is_s3_configured():
        logger.error("S3 is not configured. Update src/config.py before running this script.")
        return 1

    upload_path = Path(UPLOAD_DIR)
    if not upload_path.exists():
        logger.info("Upload directory %s does not exist; nothing to migrate.", upload_path)
        return 0

    await db_instance.connect()
    collection = get_image_hashes_collection()

    migrated = 0
    skipped = 0
    failures: list[str] = []

    for entry in sorted(upload_path.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        existing_doc = None
        if collection is not None:
            existing_doc = await collection.find_one({"filename": entry.name})
            if existing_doc and existing_doc.get("url") and not existing_doc["url"].startswith(
                "/static/uploads/"
            ):
                logger.info("Skipping %s (already points to remote storage).", entry.name)
                skipped += 1
                continue

        try:
            sha256, url, size, modified = await _upload_file(entry)
        except StorageError as exc:
            logger.error("Failed to upload %s: %s", entry.name, exc)
            failures.append(entry.name)
            continue
        except OSError as exc:
            logger.error("Failed to read %s: %s", entry.name, exc)
            failures.append(entry.name)
            continue

        if collection is not None:
            await collection.update_one(
                {"filename": entry.name},
                {
                    "$set": {
                        "filename": entry.name,
                        "sha256": sha256,
                        "size": size,
                        "modified": modified,
                        "url": url,
                    }
                },
                upsert=True,
            )

        migrated += 1
        logger.info("Migrated %s -> %s", entry.name, url)

        if delete_local:
            try:
                entry.unlink()
                logger.info("Deleted local file %s", entry.name)
            except OSError as exc:
                logger.warning("Uploaded but failed to delete %s: %s", entry.name, exc)

    await db_instance.disconnect()

    logger.info("Migration complete: %s migrated, %s skipped, %s failures.", migrated, skipped, len(failures))
    if failures:
        logger.error("Failures: %s", ", ".join(failures))
        return 2
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate local uploads into S3 storage.")
    parser.add_argument(
        "--delete-local",
        action="store_true",
        help="Delete local files after successful upload.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(migrate(delete_local=args.delete_local))


if __name__ == "__main__":
    sys.exit(main())
