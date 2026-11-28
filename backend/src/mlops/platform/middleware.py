"""Request context: correlation ID, request log line, and a safe 500 for anything unexpected.

Written as a plain ASGI middleware so the correlation ID reliably reaches every log line and
every response, including error responses produced after an exception.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mlops.platform.context import reset_correlation_id, set_correlation_id
from mlops.platform.problem import problem_response

HEADER = "X-Correlation-ID"
# Accept ids from callers only if they look like ids, so a client cannot inject log content.
_VALID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")

logger = structlog.get_logger("mlops.http")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = Headers(scope=scope).get(HEADER)
        correlation_id = supplied if supplied and _VALID_ID.match(supplied) else uuid.uuid4().hex
        context_token = set_correlation_id(correlation_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        status = 500
        response_started = False

        async def send_with_header(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                MutableHeaders(scope=message).append(HEADER, correlation_id)
            await send(message)

        started_at = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_header)
        except Exception:
            logger.error(
                "http.unhandled_error",
                method=scope["method"],
                path=scope["path"],
                correlation_id=correlation_id,
                exc_info=True,
            )
            status = 500
            if not response_started:
                response = problem_response(
                    status=500,
                    code="INTERNAL",
                    detail="an unexpected error occurred; quote the correlation ID when reporting",
                )
                await response(scope, receive, send_with_header)
        finally:
            logger.info(
                "http.request",
                method=scope["method"],
                path=scope["path"],
                status=status,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                correlation_id=correlation_id,
            )
            structlog.contextvars.clear_contextvars()
            reset_correlation_id(context_token)
