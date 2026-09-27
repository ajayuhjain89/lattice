"""Structured JSON logging with redaction built in.

Logging discipline (docs/security-model.md SEC-8):

* Code logs *events* with structured fields: ``log_event(logger, "auth.login", ...)``.
  Fields should be IDs, codes, counts and durations -- never user or document content.
* As a safety net, the formatter redacts fields whose *names* look sensitive and
  scrubs *values* that look like credentials (URL passwords, bearer tokens, session
  cookies), including inside third-party log messages.
* Exception text is not logged outside development: database and driver exceptions
  embed SQL parameters. Production and test logs carry the exception type and stack
  frames (file, line, function) only.
"""

import json
import logging
import re
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"

request_id_var: ContextVar[str | None] = ContextVar("lattice_request_id", default=None)

_SENSITIVE_KEY = re.compile(
    r"token|secret|passw|cookie|authorization|credential|api[_-]?key|csrf|dsn|database_url"
    r"|set-cookie",
    re.IGNORECASE,
)
_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # user:password@ in any URL
    (
        re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*://[^:/@\s]+):[^@\s]+@", re.IGNORECASE),
        r"\g<scheme>:" + REDACTED + "@",
    ),
    # Authorization: Bearer <token>
    (re.compile(r"(?P<p>bearer\s+)[A-Za-z0-9._~+/=\-]+", re.IGNORECASE), r"\g<p>" + REDACTED),
    # lattice session cookie values (with or without the __Host- prefix)
    (re.compile(r"(?P<p>lattice_session=)[^;\s\"']+"), r"\g<p>" + REDACTED),
)

# uvicorn attaches an ANSI-coloured duplicate of its message as "color_message".
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {
    "message",
    "color_message",
}


def scrub_text(text: str) -> str:
    for pattern, replacement in _VALUE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any, key: str | None = None) -> Any:
    """Recursively redact sensitive keys and scrub credential-looking strings."""
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub_text(str(value))


class JsonFormatter(logging.Formatter):
    def __init__(self, *, include_exception_text: bool) -> None:
        super().__init__()
        self._include_exception_text = include_exception_text

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": scrub_text(record.getMessage()),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id

        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload["fields"] = redact(fields)
        # Anything else passed via `extra=` is treated like fields.
        extra = {
            k: v
            for k, v in vars(record).items()
            if k not in _STANDARD_RECORD_ATTRS and k != "fields" and not k.startswith("_")
        }
        if extra:
            payload.setdefault("fields", {}).update(redact(extra))

        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = self._format_exception(record.exc_info[1])
        return json.dumps(payload, default=str, ensure_ascii=False)

    def _format_exception(self, exc: BaseException) -> dict[str, Any]:
        info: dict[str, Any] = {"type": f"{type(exc).__module__}.{type(exc).__qualname__}"}
        frames = traceback.extract_tb(exc.__traceback__)
        info["frames"] = [f"{f.filename}:{f.lineno} in {f.name}" for f in frames[-15:]]
        if self._include_exception_text:
            info["text"] = scrub_text("".join(traceback.format_exception(exc)))
        return info


def configure_logging(*, level: str, include_exception_text: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(include_exception_text=include_exception_text))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Route uvicorn's own loggers through the JSON formatter. Its access log is
    # disabled: it records raw paths and query strings. Lattice logs requests itself.
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.disabled = True
    # SQLAlchemy statement logging would print SQL parameters.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event, extra={"fields": fields})
