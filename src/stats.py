from datetime import datetime, timedelta
from database import get_pages_collection, get_history_collection
from loguru import logger

# Caching variables for stats
last_character_count = 0
last_character_count_time = None
character_count_cache_duration = timedelta(minutes=15)  # Cache for 15 minutes

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

async def get_total_characters():
    """
    Calculate total number of characters across all wiki pages with caching.
    
    Returns:
        int: Total number of characters
    """
    global last_character_count, last_character_count_time
    
    # Check if we have a cached value that's still valid
    if (last_character_count_time is not None and 
        datetime.now() - last_character_count_time < character_count_cache_duration):
        return last_character_count
    
    try:
        pages_collection = get_pages_collection()
        if pages_collection is not None:
            # Aggregate to sum the length of all content
            pipeline = [
                {"$project": {"content_length": {"$strLenCP": "$content"}}},
                {"$group": {"_id": None, "total_characters": {"$sum": "$content_length"}}}
            ]
            
            result = await pages_collection.aggregate(pipeline).to_list(1)
            total_characters = result[0]["total_characters"] if result else 0
            
            # Update cache
            last_character_count = total_characters
            last_character_count_time = datetime.now()
            
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

async def get_stats():
    """
    Get all statistics for the wiki.
    
    Returns:
        dict: Dictionary containing all statistics
    """
    return {
        "total_edits": await get_total_edits(),
        "total_characters": await get_total_characters(),
        "total_pages": await get_total_pages(),
        "last_updated": last_character_count_time.strftime("%Y-%m-%d %H:%M:%S") if last_character_count_time else None
    }
