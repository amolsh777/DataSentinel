"""Request-ID injection, per-endpoint timing and structured logging.

Every inbound HTTP request gets a ``X-Request-ID`` header (generated if absent).
Timing and structured context are written to the application logger as JSON
(or text in development) via a context-variable log record factory so every log
line emitted during request processing automatically carries the request-id.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Context variable (not thread-local): the request id follows the request across awaits and into the
# threadpool that runs sync endpoints, and concurrent requests on one event-loop thread cannot overwrite each other.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def _make_record_factory(original):
    """Wrap the default log record factory to inject the current request_id."""
    def factory(name, level, fn, lno, msg, args, exc_info, func=None, sinfo=None):
        record = original(name, level, fn, lno, msg, args, exc_info, func, sinfo)
        # Apply thread-local request_id if not already set by explicit extra=.
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return record
    return factory


# Install once at module import.
logging.setLogRecordFactory(_make_record_factory(logging.getLogRecordFactory()))


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request-id, log timing, and propagate the id in the response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        # Make the request-id available to all log calls on this thread.
        token = request_id_var.set(request_id)
        t0 = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
        except Exception:
            raise
        finally:
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.info(
                "%s %s → %s (%.1f ms)",
                request.method, request.url.path,
                getattr(response, "status_code", 0) if response else 500, duration_ms,
                extra={
                    # request_id is already set by the log record factory from thread-local;
                    # omit it here to avoid the "overwrite" KeyError.
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": getattr(response, "status_code", 0) if response else 500,
                    "duration_ms": duration_ms,
                },
            )
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        return response
