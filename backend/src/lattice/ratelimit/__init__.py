"""Rate limits and resource protection (Phase 10; threat model T19, T24).

Three limiters, all enforced on the server and all shared in PostgreSQL:

* :class:`RateLimiter` -- fixed-window counters in PostgreSQL (``rate_limit_counters``,
  migration 0013), for authenticated subjects: a membership (Asks, access writes) or a
  SCIM token. One row per (organization, bucket, subject) holds the current window and
  its count; a conditional upsert increments it only while under the limit, so parallel
  requests never exceed it (PostgreSQL locks the conflicting row and re-checks the
  condition on its latest version), and several API processes share the counts. All
  limits of one request are taken in one transaction: if any is exhausted nothing is
  counted. Row-level security confines the counters to the request's organization.
* :class:`AskSlots` -- in-flight Asks per membership: leased rows in ``ask_slots``
  (Phase 11, P11.4, D-24), shared by every API process; a crashed process's lease
  expires.
* :class:`PreAuthLimiter` -- pre-authentication requests (development sign-in, OIDC
  start and callback, failed SCIM authentication) per client, in ``pre_auth_rate_limits``
  through a ``lattice_auth`` function (no organization to bind yet), keyed by an HMAC of
  the client address under ``LATTICE_CLIENT_KEY_SECRET``; the address is never stored,
  logged or used as a metric label.

No limit is process-local (deployment.md §12): every API instance enforces the same
counts. Only caches (OIDC discovery and keys) and metrics are per process.

What a 429 can reveal: nothing about documents, accounts or other organizations. The
callers take a limit after authentication and the capability check and before anything
that depends on the request's target, with the subject derived from the session or
token (never from the request body), so the result is the same whether a target exists
or not. Limits apply to every role, administrators included; there is no exemption and
no setting turns a limit off (each must be at least 1).
"""

import hashlib
import hmac
import ipaddress
import logging
import math
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from lattice import metrics
from lattice.db.engine import Database
from lattice.errors import ErrorCode, LatticeError
from lattice.log import log_event

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
# An Ask's slot lease: the generation deadline plus this much for retrieval and the rest.
ASK_SLOT_RETRIEVAL_MARGIN_SECONDS: Final = 60.0

_log = logging.getLogger("lattice.ratelimit")


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


# Take a free slot, or one whose lease expired (a crashed process's); no row returned means
# the slot is held. The conflicting row is locked and re-checked on its latest version, so
# two requests never hold one slot.
_TAKE_SLOT_SQL = text(
    """
    INSERT INTO ask_slots AS a (organization_id, membership_id, slot, lease_id, expires_at)
    VALUES (:o, :m, :slot, :lease, now() + make_interval(secs => :ttl))
    ON CONFLICT (organization_id, membership_id, slot) DO UPDATE SET
      lease_id = EXCLUDED.lease_id, expires_at = EXCLUDED.expires_at
    WHERE a.expires_at <= now()
    RETURNING a.slot
    """
)
_RELEASE_SLOT_SQL = text(
    "DELETE FROM ask_slots WHERE organization_id = :o AND membership_id = :m "
    "AND slot = :slot AND lease_id = :lease"
)


class AskSlots:
    """At most ``limit`` Asks in flight per membership, across every API process (D-24).

    A slot is a leased row in ``ask_slots`` (migration 0015), taken in its own short
    transaction before the Ask and released by its lease ID afterwards. The lease lasts
    ``lease_seconds`` (the generation deadline plus a retrieval margin): if a process dies
    mid-Ask, its slot frees itself then, and if releasing fails (the database is
    briefly unreachable) the same expiry applies. Expiry uses the database's clock.
    """

    def __init__(self, db: Database, bucket: Bucket, limit: int, lease_seconds: float) -> None:
        if limit < 1 or lease_seconds <= 0:
            raise ValueError("a concurrency limit must allow at least one request")
        self._db = db
        self._bucket = bucket
        self._limit = limit
        self._ttl = lease_seconds

    @asynccontextmanager
    async def slot(
        self, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> AsyncIterator[None]:
        lease = uuid.uuid4()
        params = {"o": organization_id, "m": membership_id, "lease": lease, "ttl": self._ttl}
        taken: int | None = None
        async with self._db.tenant_transaction(organization_id) as session:
            for slot in range(self._limit):
                if (await session.execute(_TAKE_SLOT_SQL, {**params, "slot": slot})).first():
                    taken = slot
                    break
        if taken is None:
            raise RateLimited(self._bucket, 1)
        try:
            yield
        finally:
            try:
                async with self._db.tenant_transaction(organization_id) as session:
                    await session.execute(_RELEASE_SLOT_SQL, {**params, "slot": taken})
            except (SQLAlchemyError, OSError) as exc:  # the lease expires on its own
                log_event(
                    _log,
                    logging.WARNING,
                    "ratelimit.slot_release_failed",
                    error_type=type(exc).__name__,
                )


def client_identity(address: str | None) -> str:
    """The unit a pre-authentication limit counts: an IPv4 address, or an IPv6 /64 (one
    subscriber's allocation, so rotating addresses inside it gains nothing). IPv4-mapped
    IPv6 counts as IPv4. Anything unparsable shares one bucket."""
    if not address:
        return "unknown"
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return "unparsable"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network(f"{ip}/64", strict=False))
    return str(ip)


_PRE_AUTH_SQL = text("SELECT lattice_pre_auth_hit(:b, :k, :l, :w, :c)")


class PreAuthLimiter:
    """Pre-authentication requests per client per window, shared by every API process
    (D-24 amended; ``pre_auth_rate_limits``, migration 0015) through the
    ``lattice_pre_auth_hit`` function: there is no organization to bind yet.

    The key is an HMAC-SHA-256 of :func:`client_identity` under the deployment secret
    ``LATTICE_CLIENT_KEY_SECRET``: no address is stored, and a database copy cannot be
    reversed by enumerating the address space. Rotating the secret only resets counts.
    """

    def __init__(self, db: Database, key: bytes, limit: int, window_seconds: int = 60) -> None:
        if limit < 1 or window_seconds < 1 or len(key) < 32:
            raise ValueError("a client limit needs a 32-byte key and at least one request")
        self._db = db
        self._key = key
        self._limit = limit
        self._window = window_seconds

    def client_key(self, address: str | None) -> bytes:
        return hmac.new(self._key, client_identity(address).encode(), hashlib.sha256).digest()

    async def _call(self, address: str | None, *, count: bool) -> None:
        params = {
            "b": Bucket.SIGN_IN.value,
            "k": self.client_key(address),
            "l": self._limit,
            "w": self._window,
            "c": count,
        }
        async with self._db.unscoped_transaction() as session:
            wait: int = (await session.execute(_PRE_AUTH_SQL, params)).scalar_one()
        if wait:
            raise RateLimited(Bucket.SIGN_IN, wait)

    async def check(self, address: str | None) -> None:
        """Raise :class:`RateLimited` if ``address`` is over the limit, counting nothing:
        callers that count failures only refuse an exhausted client *before* checking
        its credentials, so guessing cannot continue behind the 429."""
        await self._call(address, count=False)

    async def hit(self, address: str | None) -> None:
        """Count one request from ``address`` or raise :class:`RateLimited`."""
        await self._call(address, count=True)


@dataclass(frozen=True)
class ApiLimits:
    """The API's limits, built once per process from its settings."""

    limiter: RateLimiter
    ask: tuple[Limit, ...]
    access_write: tuple[Limit, ...]
    scim: tuple[Limit, ...]
    ask_concurrency: AskSlots
    sign_in: PreAuthLimiter

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
            ask_concurrency=AskSlots(
                db,
                Bucket.ASK_CONCURRENT,
                settings.rate_limit_ask_concurrent,
                settings.generation_deadline_seconds + ASK_SLOT_RETRIEVAL_MARGIN_SECONDS,
            ),
            sign_in=PreAuthLimiter(db, settings.client_key, settings.rate_limit_sign_in_per_minute),
        )
