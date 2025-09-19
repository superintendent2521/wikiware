"""Log streaming service for WebSocket-based real-time log delivery.

This module provides a WebSocket endpoint for real-time log streaming from Loguru
to connected clients. It sets up a background dispatcher that forwards log messages
to all connected WebSocket clients, with optional file logging. Designed to be
initialized once during application startup via setup_log_streaming().
"""

from __future__ import annotations
import asyncio
from typing import Set, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

router = APIRouter()

# --- WS state ---
_CONNECTED: Set[WebSocket] = set()
_QUEUE: "asyncio.Queue[str]" = asyncio.Queue()
_LOOP: Optional[asyncio.AbstractEventLoop] = None
_DISPATCHER_TASK: Optional[asyncio.Task] = None
_INSTALLED = False  # guard so we don't double-add sinks


@router.websocket("/ws/logs")
async def logs_ws(websocket: WebSocket):
    """Handle WebSocket connections for real-time log streaming.

    Accepts a WebSocket connection and keeps it alive by listening for client
    keep-alive messages. When the connection is closed (either by client or error),
    removes the socket from the connected set.
    """
    await websocket.accept()
    _CONNECTED.add(websocket)
    try:
        # Passive endpoint; messages are pushed from the dispatcher.
        while True:
            # Optionally read keep-alives; prevents some proxies from closing idle conns.
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        _CONNECTED.discard(websocket)


def setup_log_streaming(app, *, add_file_sink: bool = False) -> None:
    """Set up log streaming infrastructure for the application.

    Registers event handlers to initialize and clean up log streaming resources.
    Must be called once during application startup.

    Args:
        app: FastAPI application instance
        add_file_sink: If True, adds a rotating file sink to Loguru (only if not already configured)
    """

    def _install_sinks():
        global _INSTALLED, _LOOP, _DISPATCHER_TASK
        if _INSTALLED:
            return
        _INSTALLED = True

        _LOOP = asyncio.get_running_loop()

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
            if _LOOP is not None:
                _LOOP.call_soon_threadsafe(_QUEUE.put_nowait, message)

        logger.add(
            _ws_sink,
            enqueue=True,
            backtrace=False,
            diagnose=False,
            format="{time:HH:mm:ss} | {level: <5} | {message}",
        )

        async def _dispatcher():
            while True:
                msg = await _QUEUE.get()
                for ws in tuple(_CONNECTED):
                    try:
                        await ws.send_text(msg.rstrip("\n"))
                    except WebSocketDisconnect:
                        logger.debug("Client disconnected during log streaming")
                        _CONNECTED.discard(ws)
                    except Exception as e:
                        # Log other exceptions (e.g., network errors) but avoid catching WebSocketDisconnect twice
                        logger.warning(f"Failed to send log to client: {e}")
                        _CONNECTED.discard(ws)

        _DISPATCHER_TASK = asyncio.create_task(_dispatcher())

    async def _on_startup():
        _install_sinks()

    async def _on_shutdown():
        if _DISPATCHER_TASK:
            _DISPATCHER_TASK.cancel()
            try:
                await _DISPATCHER_TASK
            except asyncio.CancelledError:
                logger.debug("Dispatcher task was successfully cancelled")
            except Exception as e:
                # Only log non-cancel errors; cancellation is expected
                logger.warning(f"Error canceling dispatcher task: {e}")

    # Register with the existing app
    app.add_event_handler("startup", _on_startup)
    app.add_event_handler("shutdown", _on_shutdown)


# Public handle: stream_log.loguru.info("...")
class _StreamLogHandle:
    """Wrapper to expose Loguru logger as a public interface.

    This class provides a minimal interface to allow external code to use
    Loguru's logging methods via stream_log.loguru.*.
    """

    def __init__(self, loguru_logger):
        self.loguru = loguru_logger


# Avoid redefining 'logger' from outer scope by using a different name internally
_stream_log_handle = _StreamLogHandle(logger)
stream_log = _stream_log_handle
