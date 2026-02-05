import asyncio
import time
from datetime import datetime, timedelta
from loguru import logger
from .database import db_instance

# Caching variables for total character count
last_character_count = 0
last_character_count_time = None  # Start as None to force first calculation
character_count_cache_duration = timedelta(minutes=30)  # Cache for 30 Minutes
# Caching Images count
last_image_count = 0
last_image_count_time = None  # Start as None to force first calculation
image_count_cache_duration = timedelta(minutes=30)  # Cache for 30 Minutes


async def get_total_edits():
    """
    Count total number of edits in the wiki (history collection entries).

    Returns:
        int: Total number of edits
    """
    try:
        if not db_instance.is_connected:
            return 0

        row = await db_instance.fetchrow("SELECT COUNT(*) AS count FROM history")
        return int(row["count"]) if row else 0
    except Exception as e:
        logger.error(f"Error counting total edits: {str(e)}")
        return 0


async def get_user_edit_stats():
    """
    Get total edit statistics for all users.

    Returns:
        dict: Dictionary containing total edits per user
    """
    try:
        if not db_instance.is_connected:
            return {}

        rows = await db_instance.fetch(
            "SELECT username, total_edits, page_edits FROM users"
        )

        user_stats = {}
        for row in rows:
            page_edits = row["page_edits"] or {}
            user_stats[row["username"]] = {
                "total_edits": row["total_edits"] or 0,
                "page_edits": page_edits,
            }

        return user_stats
    except Exception as e:
        logger.error(f"Error getting user edit stats: {str(e)}")
        return {}


async def get_total_characters():
    """
    Calculate total number of characters across all wiki pages with caching.

    Returns:
        int: Total number of characters
    """
    global last_character_count, last_character_count_time

    if (
        last_character_count_time is not None
        and datetime.now() - last_character_count_time < character_count_cache_duration
    ):
        return last_character_count

    time_delta = (
        datetime.now() - last_character_count_time
        if last_character_count_time is not None
        else "never cached"
    )
    logger.info(
        f"CHAR: Cache is old or uninitialized, Updating! Time delta is {time_delta}"
    )

    try:
        start = time.perf_counter()
        if db_instance.is_connected:
            row = await db_instance.fetchrow(
                "SELECT COALESCE(SUM(LENGTH(content)), 0) AS total_characters FROM pages"
            )
            total_characters = int(row["total_characters"]) if row else 0
            last_character_count = total_characters
            last_character_count_time = datetime.now()
            end = time.perf_counter()
            logger.info(f"Total character count updated in {end - start:.4f} seconds")
            return total_characters
        return 0
    except Exception as e:
        logger.error(f"Error calculating total characters: {str(e)}")
        return 0


async def get_total_pages():
    """
    Count total number of pages in the wiki.

    Returns:
        int: Total number of pages
    """
    try:
        if not db_instance.is_connected:
            return 0

        row = await db_instance.fetchrow("SELECT COUNT(*) AS count FROM pages")
        return int(row["count"]) if row else 0
    except Exception as e:
        logger.error(f"Error counting total pages: {str(e)}")
        return 0


async def get_total_images():
    """
    Count total number of images uploaded.

    Returns:
        int: Total number of images
    """
    global last_image_count, last_image_count_time

    if (
        last_image_count_time is not None
        and datetime.now() - last_image_count_time < image_count_cache_duration
    ):
        return last_image_count

    time_delta = (
        datetime.now() - last_image_count_time
        if last_image_count_time is not None
        else "never cached"
    )
    logger.info(
        f"IMG: Cache is old or uninitialized, Updating! Time delta is {time_delta}"
    )
    try:
        if not db_instance.is_connected:
            return 0

        row = await db_instance.fetchrow(
            "SELECT COUNT(*) AS count FROM image_hashes"
        )
        total_images = int(row["count"]) if row else 0
        last_image_count = total_images
        last_image_count_time = datetime.now()
        return total_images
    except Exception as e:
        logger.error(f"Error counting total images: {str(e)}")
        return 0


async def get_stats():
    """
    Get all statistics for the wiki.

    Returns:
        dict: Dictionary containing all statistics
    """
    user_edit_stats = await get_user_edit_stats()
    total_edits, total_characters, total_pages, total_images = await asyncio.gather(
        get_total_edits(),
        get_total_characters(),
        get_total_pages(),
        get_total_images(),
    )

    return {
        "total_edits": total_edits,
        "total_characters": total_characters,
        "total_pages": total_pages,
        "total_images": total_images,
        "last_updated": (
            last_character_count_time.strftime("%Y-%m-%d %H:%M:%S")
            if last_character_count_time
            else None
        ),
        "user_edit_stats": user_edit_stats,
    }
