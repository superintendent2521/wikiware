"""
Service for logging page views and edits.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from ..database import get_history_collection, db_instance
from ..models.page import WikiPage
from loguru import logger


class LogService:
    def collect_all():
        history_collection = get_history_collection()
        if history_collection is None:
            logger.error("History collection not available")
            return []
        return list(history_collection.find())
    