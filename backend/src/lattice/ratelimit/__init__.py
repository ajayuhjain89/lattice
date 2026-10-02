"""Rate limits and resource protection (Phase 10; threat model T19, T24).

Three limiters, all enforced on the server:

* :class:`RateLimiter` -- fixed-window counters in PostgreSQL (``rate_limit_counters``,
  migration 0013), for authenticated subjects: a membership (Asks, access writes) or a
  SCIM token. One row per (organization, bucket, subject) holds the current window and
  its count; a conditional upsert increments it only while under the limit, so parallel
  requests never exceed it (PostgreSQL locks the conflicting row and re-checks the
  condition on its latest version), and several API processes share the counts. All
  limits of one request are taken in one transaction: if any is exhausted nothing is
  counted. Row-level security confines the counters to the request's organization.
* :class:`ConcurrencyLimiter` -- in-flight requests per membership (Asks), in process.
* :class:`ClientLimiter` -- pre-authentication requests (development sign-in, OIDC
  start and callback, failed SCIM authentication) per client address, in process: there
  is no organization to bind yet. Keys are keyed hashes (a per-process random key), the
  table is bounded, and the address is never stored, logged or used as a metric label.

What a 429 can reveal: nothing about documents, accounts or other organizations. The
callers take a limit after authentication and the capability check and before anything
that depends on the request's target, with the subject derived from the session or
token (never from the request body), so the result is the same whether a target exists
or not. Limits apply to every role, administrators included; there is no exemption and
no setting turns a limit off (each must be at least 1).
"""

import asyncio
import hashlib
import hmac
import math
import secrets
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from lattice import metrics
from lattice.db.engine import Database
from lattice.errors import ErrorCode, LatticeError

if TYPE_CHECKING:
    from lattice.config import Settings


class Bucket(StrEnum):
    """Closed set: the only metric label of the limiters, and the stored bucket name."""

    ASK_MINUTE = "ask_minute"
    ASK_DAY = "ask_day"
    ASK_CONCURRENT = "ask_concurrent"
    ACCESS_WRITE = "access_write"
    SCIM = "scim"
    SIGN_IN = "sign_in"


BUCKETS: Final = frozenset(b.value for b in Bucket)


class RateLimited(LatticeError):
    """429 ``rate_limited``; ``retry_after`` whole seconds go in ``Retry-After``."""

    def __init__(self, bucket: Bucket, retry_after: int) -> None:
        super().__init__(ErrorCode.RATE_LIMITED)
        self.bucket = bucket
        self.retry_after = max(1, retry_after)
        metrics.RATE_LIMITED.labels(bucket=bucket.value).inc()


@dataclass(frozen=True)
class Limit:
    bucket: Bucket
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1 or self.window_seconds < 1:
            raise ValueError("a rate limit must allow at least one request per window")


def window_start(now: datetime, window_seconds: int) -> datetime:
    epoch = math.floor(now.timestamp() / window_seconds) * window_seconds
    return datetime.fromtimestamp(epoch, UTC)


def retry_after(now: datetime, started: datetime, window_seconds: int) -> int:
    end = started + timedelta(seconds=window_seconds)
    return max(1, math.ceil((end - now).total_seconds()))


# Increment only while under the limit (or when a newer window starts); no row returned
# means the limit is exhausted. GREATEST keeps a slow process from rewinding a window.
_HIT_SQL = text(
    """
    INSERT INTO rate_limit_counters AS c (organization_id, bucket, subject, window_start, count)
    VALUES (:o, :b, :s, :w, 1)
    ON CONFLICT (organization_id, bucket, subject) DO UPDATE SET
      count = CASE WHEN c.window_start >= EXCLUDED.window_start THEN c.count + 1 ELSE 1 END,
      window_start = GREATEST(c.window_start, EXCLUDED.window_start)
    WHERE c.window_start < EXCLUDED.window_start OR c.count < :limit
    RETURNING c.count
    """
)
_WINDOW_SQL = text(
    "SELECT window_start FROM rate_limit_counters "
    "WHERE organization_id = :o AND bucket = :b AND subject = :s"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RateLimiter:
    def __init__(self, db: Database, clock: Callable[[], datetime] = _utc_now) -> None:
        self._db = db
        self._clock = clock

    async def hit(
        self, organization_id: uuid.UUID, subject: uuid.UUID, limits: Sequence[Limit]
    ) -> None:
        """Count one request against every limit, or raise :class:`RateLimited` (and
        count nothing) if any is exhausted."""
        now = self._clock()
        async with self._db.tenant_transaction(organization_id) as session:
            for limit in limits:
                started = window_start(now, limit.window_seconds)
                params = {"o": organization_id, "b": limit.bucket.value, "s": subject}
                counted = (
                    await session.execute(_HIT_SQL, {**params, "w": started, "limit": limit.limit})
                ).first()
                if counted is None:
                    current: datetime = (await session.execute(_WINDOW_SQL, params)).scalar_one()
                    # Raising rolls back the counts already taken in this transaction.
                    raise RateLimited(limit.bucket, retry_after(now, current, limit.window_seconds))


class ConcurrencyLimiter:
    """At most ``limit`` requests in flight per key, in this process. A single event
    loop serves the process, so the counts need no lock."""

    def __init__(self, bucket: Bucket, limit: int) -> None:
        if limit < 1:
            raise ValueError("a concurrency limit must allow at least one request")
        self._bucket = bucket
        self._limit = limit
        self._in_flight: dict[uuid.UUID, int] = {}

    @asynccontextmanager
    async def slot(self, key: uuid.UUID) -> AsyncIterator[None]:
        held = self._in_flight.get(key, 0)
        if held >= self._limit:
            raise RateLimited(self._bucket, 1)
        self._in_flight[key] = held + 1
        try:
            yield
        finally:
            remaining = self._in_flight[key] - 1
            if remaining:
                self._in_flight[key] = remaining
            else:
                del self._in_flight[key]

    def in_flight(self, key: uuid.UUID) -> int:
        return self._in_flight.get(key, 0)


class ClientLimiter:
    """Fixed-window counts per client address, in this process, before authentication.

    ``max_clients`` bounds memory: when the table is full the least recently counted
    client is forgotten. Addresses are reduced to a keyed hash with a random key that
    never leaves the process, so the table holds no address.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: int = 60,
        *,
        max_clients: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window_seconds < 1 or max_clients < 1:
            raise ValueError("a client limit must allow at least one request")
        self._limit = limit
        self._window = window_seconds
        self._max = max_clients
        self._clock = clock
        self._key = secrets.token_bytes(32)
        self._counts: OrderedDict[bytes, tuple[float, int]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _client(self, address: str | None) -> bytes:
        return hmac.new(self._key, (address or "unknown").encode(), hashlib.sha256).digest()[:16]

    async def check(self, address: str | None) -> None:
        """Raise :class:`RateLimited` if ``address`` is over the limit, counting nothing:
        callers that count failures only refuse an exhausted client *before* checking
        its credentials, so guessing cannot continue behind the 429."""
        key = self._client(address)
        now = self._clock()
        started = math.floor(now / self._window) * self._window
        async with self._lock:
            found = self._counts.get(key)
            if found is not None and found[0] >= started and found[1] >= self._limit:
                raise RateLimited(Bucket.SIGN_IN, max(1, math.ceil(found[0] + self._window - now)))

    async def hit(self, address: str | None) -> None:
        """Count one request from ``address`` or raise :class:`RateLimited`."""
        key = self._client(address)
        now = self._clock()
        started = math.floor(now / self._window) * self._window
        async with self._lock:
            current_start, count = self._counts.pop(key, (started, 0))
            if current_start < started:
                current_start, count = started, 0
            if count >= self._limit:
                self._counts[key] = (current_start, count)
                raise RateLimited(
                    Bucket.SIGN_IN, max(1, math.ceil(current_start + self._window - now))
                )
            self._counts[key] = (current_start, count + 1)
            while len(self._counts) > self._max:
                self._counts.popitem(last=False)

    def __len__(self) -> int:
        return len(self._counts)


@dataclass(frozen=True)
class ApiLimits:
    """The API's limits, built once per process from its settings."""

    limiter: RateLimiter
    ask: tuple[Limit, ...]
    access_write: tuple[Limit, ...]
    scim: tuple[Limit, ...]
    ask_concurrency: ConcurrencyLimiter
    sign_in: ClientLimiter

    @classmethod
    def from_settings(cls, db: Database, settings: "Settings") -> "ApiLimits":
        return cls(
            limiter=RateLimiter(db),
            ask=(
                Limit(Bucket.ASK_MINUTE, settings.rate_limit_ask_per_minute, 60),
                Limit(Bucket.ASK_DAY, settings.rate_limit_ask_per_day, 86_400),
            ),
            access_write=(
                Limit(Bucket.ACCESS_WRITE, settings.rate_limit_access_writes_per_minute, 60),
            ),
            scim=(Limit(Bucket.SCIM, settings.rate_limit_scim_per_minute, 60),),
            ask_concurrency=ConcurrencyLimiter(
                Bucket.ASK_CONCURRENT, settings.rate_limit_ask_concurrent
            ),
            sign_in=ClientLimiter(settings.rate_limit_sign_in_per_minute),
        )
