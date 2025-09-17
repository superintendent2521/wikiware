"""
Logs API routes for WikiWare.
Provides paginated access to system actions (edits, branch creations).
"""

from json import JSONDecodeError
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi_csrf_protect import CsrfProtect
from loguru import logger

from ..middleware.auth_middleware import AuthMiddleware
from ..utils.logs import LogUtils

router = APIRouter()


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


@router.get("/api/logs", response_model=Dict[str, Any])
async def get_logs(
    request: Request,
    page: int = 1,
    limit: int = 50,
    bypass: bool = False,
    action_type: Optional[str] = None,
    csrf_protect: CsrfProtect = Depends(),
):
    """
    Get paginated system logs with optional filtering by action type.
    """
    try:
        incoming_page = page
        incoming_limit = limit
        incoming_action = action_type
        bypass_flag = bypass

        payload: Optional[Dict[str, Any]] = None
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            try:
                body = await request.json()
                if isinstance(body, dict):
                    payload = body
            except (JSONDecodeError, ValueError):
                payload = None
            except Exception:
                payload = None

        if payload:
            if "page" in payload:
                try:
                    incoming_page = int(payload["page"])
                except (TypeError, ValueError):
                    pass
            if "limit" in payload:
                try:
                    incoming_limit = int(payload["limit"])
                except (TypeError, ValueError):
                    pass
            if "action_type" in payload:
                incoming_action = payload.get("action_type")
            if "bypass" in payload:
                bypass_flag = _coerce_bool(payload["bypass"])

        if bypass_flag:
            await AuthMiddleware.require_auth(request)
            csrf_protect.validate_csrf_in_cookies(request)
            logger.info(
                "Fetching logs (bypass): page={} limit={} action_type={}",
                incoming_page,
                incoming_limit,
                incoming_action,
            )
            return await LogUtils.get_paginated_logs(
                incoming_page,
                incoming_limit,
                bypass=True,
                action_type=incoming_action,
            )

        logger.info(
            "Fetching logs: page={} limit={} action_type={}",
            incoming_page,
            incoming_limit,
            incoming_action,
        )
        return await LogUtils.get_paginated_logs(
            incoming_page,
            incoming_limit,
            bypass=False,
            action_type=incoming_action,
        )

    except HTTPException as exc:
        raise exc
    except Exception as e:
        logger.error(f"Error fetching logs: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
