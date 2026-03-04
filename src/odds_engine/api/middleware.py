"""FastAPI middleware: authentication and request ID injection."""

import uuid

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_SKIP_AUTH_PATHS = frozenset({"/api/v1/health", "/docs", "/openapi.json", "/redoc"})


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates X-API-Key header on all non-health requests."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _SKIP_AUTH_PATHS:
            return await call_next(request)

        # WebSocket auth uses query param — handled at the WS endpoint level
        if request.url.path.startswith("/api/v1/ws"):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        expected = request.app.state.settings.api_secret_key
        if api_key != expected:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Injects a unique request ID into request state and response headers."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        structlog.contextvars.clear_contextvars()
        return response
