from __future__ import annotations
import asyncio
from typing import Set, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

router = APIRouter()

# --- WS state ---
_connected: Set[WebSocket] = set()
_queue: "asyncio.Queue[str]" = asyncio.Queue()
_loop: Optional[asyncio.AbstractEventLoop] = None
_dispatcher_task: Optional[asyncio.Task] = None
_installed = False  # guard so we don't double-add sinks

@router.websocket("/ws/logs")
async def logs_ws(websocket: WebSocket):
    await websocket.accept()
    _connected.add(websocket)
    try:
        # Passive endpoint; messages are pushed from the dispatcher.
        while True:
            # Optionally read keep-alives; prevents some proxies from closing idle conns.
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        _connected.discard(websocket)

def setup_log_streaming(app, *, add_file_sink: bool = False) -> None:
    """
    Call once from your main server to:
      - add a WS sink to Loguru that forwards lines to clients
      - (optionally) add a file sink if your app hasn't already
      - start a background dispatcher to fan-out messages
    """
    def _install_sinks():
        global _installed, _loop, _dispatcher_task
        if _installed:
            return
        _installed = True

        _loop = asyncio.get_running_loop()

        # Optional: only add a file sink if your main app hasn't already configured files.
        if add_file_sink:
            logger.add(
                "logs/app.log",
                rotation="10 MB",
                retention="14 days",
                enqueue=True,
                backtrace=True,
                diagnose=False,
                format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                       "<level>{level: <8}</level> | "
                       "<cyan>{name}</cyan}:{function}:{line} - "
                       "<level>{message}</level>",
            )

        # WS sink: push formatted line into the async queue from Loguru's thread.
        def _ws_sink(message: str):
            if _loop is not None:
                _loop.call_soon_threadsafe(_queue.put_nowait, message)

        logger.add(
            _ws_sink,
            enqueue=True,
            backtrace=False,
            diagnose=False,
            format="{time:HH:mm:ss} | {level: <5} | {message}",
        )

        async def _dispatcher():
            while True:
                msg = await _queue.get()
                for ws in tuple(_connected):
                    try:
                        await ws.send_text(msg.rstrip("\n"))
                    except Exception:
                        _connected.discard(ws)

        _dispatcher_task = asyncio.create_task(_dispatcher())

    async def _on_startup():
        _install_sinks()

    async def _on_shutdown():
        global _dispatcher_task
        if _dispatcher_task:
            _dispatcher_task.cancel()
            try:
                await _dispatcher_task
            except Exception:
                pass

    # Register with the existing app
    app.add_event_handler("startup", _on_startup)
    app.add_event_handler("shutdown", _on_shutdown)

# Public handle: stream_log.loguru.info("...")
class _StreamLogHandle:
    def __init__(self, logger):
        self.loguru = logger

stream_log = _StreamLogHandle(logger)
