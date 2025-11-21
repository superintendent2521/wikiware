"""
Edit presence routes (REST + WebSocket).

Provides lightweight collaboration signals that show who else is editing or
watching a page/branch. All endpoints are guarded by the edit_presence feature
flag and require a valid user session (via cookie).
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from loguru import logger

from ..config import SESSION_COOKIE_NAME
from ..services.edit_presence_service import EditPresenceService
from ..services.settings_service import SettingsService
from ..services.user_service import UserService

router = APIRouter()

SESSION_COOKIE_CANDIDATES = (
    SESSION_COOKIE_NAME,
    "__Host-user_session",
    "user_session",
)

_ROOMS: Dict[str, set[WebSocket]] = {}
_ROOM_LOCK = asyncio.Lock()
_HOUSEKEEPERS: Dict[str, asyncio.Task] = {}


async def _get_feature_flags() -> object:
    try:
        return await SettingsService.get_feature_flags()
    except Exception as exc:  # IGNORE W0718
        logger.warning("Failed to load feature flags for edit presence: {}", exc)
        return SettingsService._feature_flags_cache


def _room_key(page: str, branch: str) -> str:
    return f"{page}|{branch}"


def _get_session_cookie_from_request(request: Request) -> Optional[str]:
    for name in SESSION_COOKIE_CANDIDATES:
        cookie = request.cookies.get(name)
        if cookie:
            return cookie
    return None


def _get_session_cookie_from_websocket(websocket: WebSocket) -> Optional[str]:
    for name in SESSION_COOKIE_CANDIDATES:
        cookie = websocket.cookies.get(name)
        if cookie:
            return cookie
    return None


async def _resolve_user_from_cookie(session_id: Optional[str]) -> Optional[Dict[str, str]]:
    if not session_id:
        return None
    try:
        user = await UserService.get_user_by_session(session_id)
    except Exception as exc:  # IGNORE W0718
        logger.warning("Failed to validate session for edit presence: {}", exc)
        return None
    if not user or not user.get("is_active", True):
        return None

    user_id = user.get("_id") or user.get("username")
    if user_id is None:
        return None

    return {
        "user_id": str(user_id),
        "username": str(user.get("username", "")),
    }


async def _broadcast_roster(page: str, branch: str) -> None:
    """Send the current roster to all active sockets in the room."""
    room_id = _room_key(page, branch)
    async with _ROOM_LOCK:
        sockets = _ROOMS.get(room_id, set())
    roster = await EditPresenceService.get_roster(page=page, branch=branch)
    if roster is None:
        return

    payload = {"type": "presence", "editors": roster.get("editors", [])}
    stale: set[WebSocket] = set()
    for ws in list(sockets):
        try:
            await ws.send_json(payload)
        except WebSocketDisconnect:
            stale.add(ws)
        except Exception as exc:  # IGNORE W0718
            logger.warning("Failed to send presence update: {}", exc)
            stale.add(ws)

    if stale:
        async with _ROOM_LOCK:
            active = _ROOMS.get(room_id, set())
            for ws in stale:
                active.discard(ws)
            if not active:
                _ROOMS.pop(room_id, None)


async def _start_housekeeper(page: str, branch: str) -> None:
    """Periodically refresh rosters to capture TTL expirations."""
    room_id = _room_key(page, branch)
    if room_id in _HOUSEKEEPERS:
        return

    async def _run():
        try:
            while True:
                await asyncio.sleep(30)
                async with _ROOM_LOCK:
                    if not _ROOMS.get(room_id):
                        break
                await _broadcast_roster(page, branch)
        except asyncio.CancelledError:
            return
        finally:
            _HOUSEKEEPERS.pop(room_id, None)

    task = asyncio.create_task(_run())
    _HOUSEKEEPERS[room_id] = task


@router.post("/api/pages/{title}/edit-session")
async def create_edit_session(title: str, request: Request):
    feature_flags = getattr(request.state, "feature_flags", None) or await _get_feature_flags()
    if not getattr(feature_flags, "edit_presence_enabled", False):
        raise HTTPException(status_code=404, detail="Not found")

    session_cookie = _get_session_cookie_from_request(request)
    user = await _resolve_user_from_cookie(session_cookie)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    branch = (payload.get("branch") or "main").strip() or "main"
    branch_normalized = EditPresenceService._normalize_branch(branch)
    mode = (payload.get("mode") or "edit").strip().lower()
    client_id = (payload.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(status_code=422, detail="client_id is required")

    session_id, lease_expires_at, roster, error = await EditPresenceService.create_session(
        page=title,
        branch=branch_normalized,
        mode=mode,
        client_id=client_id,
        user_id=user["user_id"],
        username=user["username"],
    )

    if error == "duplicate":
        raise HTTPException(status_code=409, detail="Session already active for client")
    if error == "offline":
        raise HTTPException(status_code=503, detail="Database unavailable")
    if error:
        raise HTTPException(status_code=500, detail="Could not create session")

    await _broadcast_roster(title, branch_normalized)

    return {
        "status": "ok",
        "session_id": session_id,
        "lease_expires_at": lease_expires_at.isoformat() if lease_expires_at else None,
        "active_editors": (roster or {}).get("editors", []),
    }


@router.delete("/api/pages/{title}/edit-session/{session_id}")
async def release_edit_session(title: str, session_id: str, request: Request):
    feature_flags = getattr(request.state, "feature_flags", None) or await _get_feature_flags()
    if not getattr(feature_flags, "edit_presence_enabled", False):
        raise HTTPException(status_code=404, detail="Not found")

    session_cookie = _get_session_cookie_from_request(request)
    user = await _resolve_user_from_cookie(session_cookie)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    lease = await EditPresenceService.get_session(
        session_id=session_id, user_id=user["user_id"]
    )
    branch = (
        lease.get("branch")
        if lease
        else EditPresenceService._normalize_branch(request.query_params.get("branch"))
    )

    await EditPresenceService.release_session(
        session_id=session_id, user_id=user["user_id"]
    )
    await _broadcast_roster(title, branch or "main")

    return {"status": "released"}


@router.websocket("/ws/edit-presence")
async def edit_presence_ws(websocket: WebSocket):
    feature_flags = await _get_feature_flags()
    if not getattr(feature_flags, "edit_presence_enabled", False):
        await websocket.close(code=4404, reason="Presence disabled")
        return

    params = websocket.query_params
    page = params.get("page")
    branch = EditPresenceService._normalize_branch(params.get("branch"))
    session_id = params.get("session_id")
    mode = params.get("mode") or "edit"

    if not page or not session_id:
        await websocket.close(
            code=status.WS_1002_PROTOCOL_ERROR, reason="Missing required params"
        )
        return

    user = await _resolve_user_from_cookie(_get_session_cookie_from_websocket(websocket))
    if not user:
        await websocket.close(code=4401, reason="Authentication required")
        return

    lease = await EditPresenceService.validate_session(
        session_id=session_id,
        user_id=user["user_id"],
        page=page,
        branch=branch,
        mode=mode,
    )
    if not lease:
        await websocket.close(code=4409, reason="Invalid or expired session")
        return

    await websocket.accept()

    room_id = _room_key(page, branch)
    async with _ROOM_LOCK:
        room = _ROOMS.setdefault(room_id, set())
        room.add(websocket)
    await _start_housekeeper(page, branch)
    await _broadcast_roster(page, branch)

    try:
        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")
            if msg_type == "ping":
                status_label, _ = await EditPresenceService.touch_heartbeat(
                    session_id=session_id,
                    user_id=user["user_id"],
                    page=page,
                    branch=branch,
                )
                if status_label in {"missing", "expired"}:
                    await websocket.send_json(
                        {"type": "goodbye", "reason": "expired"}
                    )
                    await websocket.close(code=4409, reason="Session expired")
                    break
            elif msg_type == "release":
                await EditPresenceService.release_session(
                    session_id=session_id, user_id=user["user_id"]
                )
                await websocket.send_json({"type": "goodbye", "reason": "released"})
                await websocket.close(code=1000)
                await _broadcast_roster(page, branch)
                break
    except WebSocketDisconnect:
        await EditPresenceService.release_session(
            session_id=session_id, user_id=user["user_id"]
        )
    except Exception as exc:  # IGNORE W0718
        logger.warning("Edit presence socket error: {}", exc)
    finally:
        async with _ROOM_LOCK:
            room = _ROOMS.get(room_id, set())
            room.discard(websocket)
            if not room:
                _ROOMS.pop(room_id, None)
                keeper = _HOUSEKEEPERS.pop(room_id, None)
                if keeper:
                    keeper.cancel()
        await _broadcast_roster(page, branch)
