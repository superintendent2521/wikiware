"""
User Agent Middleware for WikiWare.
Logs user agent information for all requests to help track usage patterns.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from loguru import logger

from .. import config


class UserAgentMiddleware(BaseHTTPMiddleware):
    """Middleware to log user agent information for all requests."""

    async def dispatch(self, request: Request, call_next):
        """Log user agent and process the request."""
        # Get user agent
        user_agent = request.headers.get("user-agent", "unknown")
        
        # Get client IP
        client_ip = "unknown"
        if request.client:
            client_ip = request.client.host
        
        # Get request method and path
        method = request.method
        path = request.url.path
        
        if config.REQUEST_LOGGING_ENABLED:
            # Log the request with user agent
            logger.info(
                f"Request: {method} {path} | "
                f"Client: {client_ip} | "
                f"User-Agent: {user_agent}"
            )
        
        # Process the request
        response = await call_next(request)
        
        # Log response status if it's an error
        if 400 <= response.status_code < 600:
            logger.warning(
                f"Response: {response.status_code} {method} {path} | "
                f"Client: {client_ip} | "
                f"User-Agent: {user_agent}"
            )
        
        return response
