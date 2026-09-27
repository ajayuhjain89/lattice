"""ASGI middleware (pure ASGI, no BaseHTTPMiddleware).

Order, outermost first: RequestContextMiddleware -> CsrfMiddleware -> FastAPI.

RequestContextMiddleware
    * assigns a server-generated request ID (client-supplied IDs are ignored, so they
      cannot be used for log injection) and returns it as ``X-Request-ID``;
    * adds security headers to every response;
    * logs one structured line per request: method, path, status, duration. Never the
      query string, headers, cookies or body;
    * converts any unhandled exception into the ``internal_error`` envelope, logging
      the exception type and frames only (see lattice.log).

CsrfMiddleware (docs/authorization-model.md §2)
    Every state-changing request (anything but GET/HEAD/OPTIONS) must carry
    ``X-Lattice-Request: 1``. Browsers cannot attach a custom header to a cross-site
    form post, and a cross-origin fetch with it needs a CORS preflight, which Lattice
    never grants. Requests the browser marks ``Sec-Fetch-Site: cross-site`` are
    rejected outright. This applies to every route, including sign-in (login CSRF)
    and routes that do not use the session.
"""

import logging
import time
import uuid
from collections.abc import MutableMapping
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from lattice.api.responses import error_json
from lattice.errors import DEFAULT_MESSAGE, ErrorCode
from lattice.log import log_event, request_id_var

logger = logging.getLogger("lattice.http")

CSRF_HEADER = b"x-lattice-request"
CSRF_HEADER_VALUE = b"1"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def _get_header(scope: Scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return bytes(value)
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, production: bool) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        state: MutableMapping[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False
        path: str = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.extend(self._security_headers(request_id, path))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.error(
                "http.unhandled_exception",
                exc_info=True,
                extra={"fields": {"method": scope.get("method"), "path": path}},
            )
            if response_started:
                raise
            response = error_json(
                ErrorCode.INTERNAL_ERROR, DEFAULT_MESSAGE[ErrorCode.INTERNAL_ERROR], request_id
            )
            await response(scope, receive, send_wrapper)
        finally:
            fields: dict[str, Any] = {
                "method": scope.get("method"),
                "path": path,
                "status": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
            principal_fields = state.get("log_fields")
            if isinstance(principal_fields, dict):
                fields.update(principal_fields)
            log_event(logger, logging.INFO, "http.request", **fields)
            request_id_var.reset(token)

    def _security_headers(self, request_id: str, path: str) -> list[tuple[bytes, bytes]]:
        headers = [
            (b"x-request-id", request_id.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"same-origin"),
            (b"cache-control", b"no-store"),
        ]
        # The interactive API docs (development/test only) need scripts and styles.
        if not path.startswith(_DOCS_PATHS):
            headers.append(
                (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
            )
        if self.production:
            headers.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
        return headers


class CsrfMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "GET") in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        fetch_site = _get_header(scope, b"sec-fetch-site")
        if _get_header(scope, CSRF_HEADER) != CSRF_HEADER_VALUE or fetch_site == b"cross-site":
            request_id = str(scope.get("state", {}).get("request_id", ""))
            log_event(
                logger,
                logging.WARNING,
                "http.csrf_rejected",
                method=scope.get("method"),
                path=scope.get("path"),
                cross_site=fetch_site == b"cross-site",
            )
            response = error_json(
                ErrorCode.CSRF_REJECTED, DEFAULT_MESSAGE[ErrorCode.CSRF_REJECTED], request_id
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
