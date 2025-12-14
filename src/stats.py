import asyncio
from datetime import datetime, timedelta
import os
from loguru import logger
from .database import (
    get_pages_collection,
    get_history_collection,
    get_users_collection,
    get_image_hashes_collection,
    db_instance,
)
import time

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
        history_collection = get_history_collection()
        if history_collection is not None:
            total_edits = await history_collection.count_documents({})
            return total_edits
        return 0
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
        users_collection = get_users_collection()
        if users_collection is None:
            return {}

        # Get all users with their edit statistics
        users = await users_collection.find(
            {}, {"username": 1, "total_edits": 1, "page_edits": 1, "_id": 0}
        ).to_list(None)

        # Convert to dictionary with username as key
        user_stats = {}
        for user in users:
            user_stats[user["username"]] = {
                "total_edits": user.get("total_edits", 0),
                "page_edits": user.get("page_edits", {}),
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

    # Check if we have a cached value that's still valid
    if (
        last_character_count_time is not None
        and datetime.now() - last_character_count_time < character_count_cache_duration
    ):
        return last_character_count
    else:
        # Log time delta safely
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
        pages_collection = get_pages_collection()
        if pages_collection is not None:
            await pages_collection._ensure_table()
            rows = await db_instance.fetch(
                f"SELECT COALESCE(SUM(LENGTH(doc->>'content')), 0) AS total_chars FROM {pages_collection._table_name}"
            )
            total_characters = int(rows[0]["total_chars"]) if rows else 0

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
        pages_collection = get_pages_collection()
        if pages_collection is not None:
            total_pages = await pages_collection.count_documents({})
            return total_pages
        return 0
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

    # Check if we have a cached value that's still valid
    if (
        last_image_count_time is not None
        and datetime.now() - last_image_count_time < image_count_cache_duration
    ):
        return last_image_count
    else:
        # Log time delta safely
        time_delta = (
            datetime.now() - last_image_count_time
            if last_image_count_time is not None
            else "never cached"
        )
        logger.info(
            f"IMG: Cache is old or uninitialized, Updating! Time delta is {time_delta}"
        )
    try: 
        image_hashes_collection = get_image_hashes_collection()
        # only use the db since each image is added to the db when uploaded
        if db_instance.is_connected and image_hashes_collection is not None:
            total_images = await image_hashes_collection.count_documents({})
            
            # CORRECTED CACHE UPDATE
            last_image_count = total_images
            last_image_count_time = datetime.now()
            
            return total_images
        return 0
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
