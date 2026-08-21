"""ASGI middleware: one request id and one access line per request.

Written against the raw ASGI interface rather than Starlette's
`BaseHTTPMiddleware` on purpose. BaseHTTPMiddleware runs the endpoint in a
separate anyio task, and a ContextVar set in the middleware is therefore *not*
visible to the endpoint or to anything it logs -- which is the entire job of
this file. A plain ASGI callable stays in the caller's context.
"""

import logging
import time
from collections.abc import Awaitable, Callable

from app.logging_config import (
    new_request_id,
    reset_request_id,
    sanitise_request_id,
    set_request_id,
)

logger = logging.getLogger("app.access")

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]

REQUEST_ID_HEADER = b"x-request-id"

# The compose healthcheck polls /api/health every 10 s. Logging it buries the
# traffic that matters at a ratio of roughly 100:1, and a failing healthcheck
# is already visible in `docker compose ps`.
QUIET_PATHS = frozenset({"/api/health"})


class RequestContextMiddleware:
    """Bind a request id to the context, echo it back, log the outcome."""

    def __init__(self, app) -> None:  # noqa: ANN001 -- ASGI app, no useful type
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_id(scope) or new_request_id()
        token = set_request_id(request_id)
        started = time.perf_counter()
        status = 500  # what an exception escaping below will have produced

        async def send_with_id(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                # Echoed so a user reporting "it failed at 14:02" can hand over
                # the id their browser saw and land on the exact lines.
                message.setdefault("headers", [])
                message["headers"].append((REQUEST_ID_HEADER, request_id.encode()))
            await send(message)

        # An exception that reaches here is re-raised, not answered: Starlette
        # puts ServerErrorMiddleware *outside* the user middleware stack, so it
        # -- not us -- writes the 500, and that response therefore carries no
        # X-Request-ID. The log line below still does, which is the half that
        # matters: the id exists so a report can be traced, and a bare "500
        # Internal Server Error" is not a report anyone can act on either way.
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = scope.get("path", "")
            if path not in QUIET_PATHS:
                logger.info(
                    "%s %s -> %d in %.0fms",
                    scope.get("method", "-"),
                    path,
                    status,
                    (time.perf_counter() - started) * 1000,
                    extra={
                        "ctx_method": scope.get("method"),
                        "ctx_path": path,
                        "ctx_status": status,
                        "ctx_duration_ms": round(
                            (time.perf_counter() - started) * 1000, 1
                        ),
                    },
                )
            reset_request_id(token)


def _incoming_id(scope: Scope) -> str | None:
    """The id nginx (or whatever proxy is furthest out) already assigned."""
    for name, value in scope.get("headers", ()):
        if name == REQUEST_ID_HEADER:
            return sanitise_request_id(value.decode("latin-1"))
    return None
