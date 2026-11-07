"""Wiki-wide statistics backed by PostgreSQL aggregates."""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import func, select

from .database import db_instance
from .schema import (
    image_hashes,
    page_branches,
    pages,
    revisions,
    user_page_edit_counts,
    users,
)

last_character_count = 0
last_character_count_time = None
character_count_cache_duration = timedelta(minutes=30)
last_image_count = 0
last_image_count_time = None
image_count_cache_duration = timedelta(minutes=30)


def _engine() -> Any:
    return getattr(db_instance, "engine", None)


def _ready() -> bool:
    return bool(getattr(db_instance, "is_connected", False) and _engine())


async def _scalar(statement: Any) -> Any:
    if not _ready():
        return 0
    async with _engine().connect() as conn:
        return (await conn.execute(statement)).scalar() or 0


async def get_total_edits():
    try:
        return await _scalar(select(func.count()).select_from(revisions))
    except Exception as exc:
        logger.error(f"Error counting total edits: {exc}")
        return 0


async def get_user_edit_stats():
    try:
        if not _ready():
            return {}
        statement = (
            select(
                users.c.username,
                users.c.total_edits,
                pages.c.title,
                user_page_edit_counts.c.edit_count,
            )
            .outerjoin(
                user_page_edit_counts,
                user_page_edit_counts.c.user_id == users.c.id,
            )
            .outerjoin(pages, pages.c.id == user_page_edit_counts.c.page_id)
            .order_by(users.c.username, pages.c.title)
        )
        async with _engine().connect() as conn:
            rows = (await conn.execute(statement)).mappings().all()
        stats = {}
        for row in rows:
            entry = stats.setdefault(
                row["username"],
                {"total_edits": row.get("total_edits", 0) or 0, "page_edits": {}},
            )
            if row.get("title"):
                entry["page_edits"][row["title"]] = row.get("edit_count", 0) or 0
        return stats
    except Exception as exc:
        logger.error(f"Error getting user edit stats: {exc}")
        return {}


async def get_total_characters():
    global last_character_count, last_character_count_time
    now = datetime.now()
    if last_character_count_time and now - last_character_count_time < character_count_cache_duration:
        return last_character_count
    try:
        start = time.perf_counter()
        statement = select(func.coalesce(func.sum(func.char_length(page_branches.c.content)), 0))
        last_character_count = int(await _scalar(statement))
        last_character_count_time = now
        logger.info(f"Total character count updated in {time.perf_counter() - start:.4f} seconds")
        return last_character_count
    except Exception as exc:
        logger.error(f"Error calculating total characters: {exc}")
        return 0


async def get_total_pages():
    try:
        # Preserve the old metric: one row per page branch.
        return await _scalar(select(func.count()).select_from(page_branches))
    except Exception as exc:
        logger.error(f"Error counting total pages: {exc}")
        return 0


async def get_total_images():
    global last_image_count, last_image_count_time
    now = datetime.now()
    if last_image_count_time and now - last_image_count_time < image_count_cache_duration:
        return last_image_count
    try:
        last_image_count = int(await _scalar(select(func.count()).select_from(image_hashes)))
        last_image_count_time = now
        return last_image_count
    except Exception as exc:
        logger.error(f"Error counting total images: {exc}")
        return 0


async def get_public_stats():
    """Read public totals without loading every user's page edit breakdown."""
    total_edits, total_characters, total_pages, total_images = await asyncio.gather(
        get_total_edits(), get_total_characters(), get_total_pages(), get_total_images()
    )
    return {
        "total_edits": total_edits,
        "total_characters": total_characters,
        "total_pages": total_pages,
        "total_images": total_images,
        "last_updated": last_character_count_time.strftime("%Y-%m-%d %H:%M:%S") if last_character_count_time else None,
    }


async def get_stats():
    stats, user_edit_stats = await asyncio.gather(get_public_stats(), get_user_edit_stats())
    return {**stats, "user_edit_stats": user_edit_stats}
