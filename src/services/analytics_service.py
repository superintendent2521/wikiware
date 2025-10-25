"""
Analytics service for WikiWare.
Tracks page views and search activity for admin insights.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import Request
from loguru import logger
import asyncio

from ..database import db_instance


def _utcnow() -> datetime:
    """Return the current UTC time with tzinfo."""
    return datetime.now(timezone.utc)


class AnalyticsService:
    """Service responsible for recording and aggregating analytics events."""

    _COLLECTION_NAME = "analytics_events"

    @staticmethod
    def _get_collection():
        """Return the analytics events collection if connected."""
        if not db_instance.is_connected:
            return None
        return db_instance.get_collection(AnalyticsService._COLLECTION_NAME)

    @staticmethod
    async def record_page_view(
        request: Request,
        page_title: str,
        branch: str,
        user: dict | None,
    ) -> None:
        """Persist a page-view analytics event."""
        if request.method == "HEAD":
            return
        collection = AnalyticsService._get_collection()
        if collection is None:
            return
        try:
            event = {
                "event_type": "page_view",
                "timestamp": _utcnow(),
                "page_title": page_title,
                "branch": branch,
                "referrer": request.headers.get("referer"),
            }
            await collection.insert_one(event)
        except Exception as exc:  # IGNORE W0718
            logger.warning(f"Failed to record page view for {page_title}: {exc}")

    @staticmethod
    async def record_search(
        request: Request,
        query: str,
        branch: str,
        result_count: int,
        user: dict | None,
    ) -> None:
        """Persist a search analytics event."""
        if not query:
            return
        collection = AnalyticsService._get_collection()
        if collection is None:
            return
        try:
            normalized_query = " ".join(query.lower().split())
            event = {
                "event_type": "search",
                "timestamp": _utcnow(),
                "query": query,
                "query_normalized": normalized_query,
                "branch": branch,
                "results": result_count,
            }
            await collection.insert_one(event)
        except Exception as exc:  # IGNORE W0718
            logger.warning(f"Failed to record search query {query!r}: {exc}")

    @staticmethod
    async def record_favorite_added(
        request: Request,
        page_title: str,
        branch: str,
        user: dict | None,
    ) -> None:
        """Persist a favorite-added analytics event."""
        collection = AnalyticsService._get_collection()
        if collection is None:
            return
        try:
            event = {
                "event_type": "favorite_added",
                "timestamp": _utcnow(),
                "page_title": page_title,
                "branch": branch,
            }
            await collection.insert_one(event)
        except Exception as exc:  # IGNORE W0718
            logger.warning(
                f"Failed to record favorite for {page_title!r} on {branch}: {exc}"
            )

    @staticmethod
    async def record_favorite_removed(
        request: Request,
        page_title: str,
        branch: str,
        user: dict | None,
    ) -> None:
        """Persist a favorite-removed analytics event."""
        collection = AnalyticsService._get_collection()
        if collection is None:
            return
        try:
            event = {
                "event_type": "favorite_removed",
                "timestamp": _utcnow(),
                "page_title": page_title,
                "branch": branch,
            }
            await collection.insert_one(event)
        except Exception as exc:  # IGNORE W0718
            logger.warning(
                f"Failed to record favorite removal for {page_title!r} on {branch}: {exc}"
            )

    @staticmethod
    async def get_admin_dashboard_metrics() -> Dict[str, Any]:
        """
        Return aggregated analytics for the admin dashboard.

        Provides totals for today, the trailing 7-day window, per-day series,
        and recent popular searches.
        """
        collection = AnalyticsService._get_collection()
        if collection is None:
            return AnalyticsService._empty_metrics()

        now = _utcnow()
        today_start = datetime(
            now.year, now.month, now.day, tzinfo=timezone.utc
        )
        window_start = today_start - timedelta(days=6)

        metrics = AnalyticsService._empty_metrics()
        # Populate per-day data for page views and searches
        page_pipeline = [
            {
                "$match": {
                    "event_type": "page_view",
                    "timestamp": {"$gte": window_start},
                }
            },
            {
                "$group": {
                    "_id": {
                        "date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$timestamp",
                            }
                        }
                    },
                    "count": {"$sum": 1},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$_id.date",
                    "count": 1,
                }
            },
            {"$sort": {"date": 1}},
        ]

        search_pipeline = [
            {
                "$match": {
                    "event_type": "search",
                    "timestamp": {"$gte": window_start},
                }
            },
            {
                "$group": {
                    "_id": {
                        "date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$timestamp",
                            }
                        }
                    },
                    "count": {"$sum": 1},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$_id.date",
                    "count": 1,
                }
            },
            {"$sort": {"date": 1}},
        ]

        top_search_pipeline = [
            {
                "$match": {
                    "event_type": "search",
                    "timestamp": {"$gte": now - timedelta(days=30)},
                }
            },
            {
                "$group": {
                    "_id": "$query_normalized",
                    "display": {"$first": "$query"},
                    "count": {"$sum": 1},
                    "last_used": {"$max": "$timestamp"},
                }
            },
            {"$sort": {"count": -1, "last_used": -1}},
            {"$limit": 5},
        ]

        try:
            page_results, search_results, top_search_results = await asyncio.gather(
                collection.aggregate(page_pipeline).to_list(None),
                collection.aggregate(search_pipeline).to_list(None),
                collection.aggregate(top_search_pipeline).to_list(None),
            )
        except Exception as exc:  # IGNORE W0718
            logger.warning(f"Failed to aggregate analytics metrics: {exc}")
            return metrics

        # Build date buckets for the trailing 7 days
        daily_index: Dict[str, Dict[str, Any]] = {}
        for day_offset in range(7):
            day = (window_start + timedelta(days=day_offset)).date()
            key = day.isoformat()
            daily_index[key] = {
                "date": key,
                "label": day.strftime("%a %d"),
                "page_views": 0,
                "searches": 0,
            }

        for record in page_results:
            day = record["date"]
            if day in daily_index:
                daily_index[day]["page_views"] = record.get("count", 0)

        for record in search_results:
            day = record["date"]
            if day in daily_index:
                daily_index[day]["searches"] = record.get("count", 0)

        metrics["daily"] = list(daily_index.values())

        # Aggregate totals
        for day_stats in metrics["daily"]:
            metrics["last_7_days"]["page_views"] += day_stats["page_views"]
            metrics["last_7_days"]["searches"] += day_stats["searches"]

        today_key = today_start.date().isoformat()
        today_stats = daily_index.get(today_key)
        if today_stats:
            metrics["today"]["page_views"] = today_stats["page_views"]
            metrics["today"]["searches"] = today_stats["searches"]

        metrics["top_searches"] = [
            {
                "query": record.get("display") or record["_id"],
                "count": record.get("count", 0),
            }
            for record in top_search_results
        ]

        return metrics

    @staticmethod
    def _empty_metrics() -> Dict[str, Any]:
        """Return a baseline metrics payload."""
        return {
            "today": {
                "page_views": 0,
                "searches": 0,
            },
            "last_7_days": {
                "page_views": 0,
                "searches": 0,
            },
            "daily": [],
            "top_searches": [],
        }
