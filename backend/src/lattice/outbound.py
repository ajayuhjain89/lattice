"""Outbound target policy: what Lattice may connect to on behalf of an organization
(phase-12-plan C4; security-model §12; threat model T27).

Every request Lattice makes to a URL an administrator entered or a discovery document
returned goes through ``OutboundPolicy`` and ``outbound_client``. Three classes:

* **public endpoint** (the default): ``https`` only, port 443, no userinfo, query or
  fragment in a base URL, and **every** address the host resolves to must be globally
  routable;
* **registered private target**: the exact ``scheme://host:port`` an operator registered
  for one organization and one purpose (``lattice outbound allow``, table
  ``outbound_targets``, audited). Loopback, private and CGNAT addresses are allowed for
  that host and port only -- never link-local (cloud metadata), multicast, unspecified
  or reserved ranges;
* **deployment-local** providers (trust class ``local``) stay operator configuration, as
  before, and are not governed here.

Addresses are judged with ``ipaddress``, never by string matching, after the hostname is
resolved; IPv6 transition forms that embed an IPv4 address (mapped, compatible, 6to4,
Teredo, NAT64) are refused outright, and non-canonical IPv4 literals (``127.1``,
``0x7f.0.0.1``, ``2130706433``) are refused before resolution.

**Connect-time enforcement.** A check before the request proves nothing about the
connection (DNS rebinding: the name can resolve differently a moment later). The client
built by ``outbound_client`` connects through ``GuardedBackend``, an httpcore network
backend that resolves the host itself, refuses unless every resolved address passes,
and then connects to the address it checked -- so the address judged is the address
used. TLS still verifies the certificate against the hostname (httpcore passes the
origin host as SNI). Redirects are never followed (a 3xx is ``redirect_refused``),
environment proxies are ignored, and time and response size are bounded.

Failures carry a fixed code only (``target_invalid``, ``target_not_public``,
``target_unreachable``, ``tls_failed``, ``redirect_refused``, ``response_too_large``,
``timeout``) -- never an address, a response body or timing detail.
"""

import asyncio
import ipaddress
import re
import socket
import ssl
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import urlsplit

import anyio
import httpcore
import httpx

Purpose = Literal["oidc", "generation"]
PURPOSES: Final = ("oidc", "generation")
IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[IpAddress]]]

CONNECT_TIMEOUT_SECONDS: Final = 5.0
TOTAL_TIMEOUT_SECONDS: Final = 10.0
MAX_RESPONSE_BYTES: Final = 1024 * 1024
MAX_URL_LENGTH: Final = 2000

# Never reachable, registered or not.
_ALWAYS_REFUSED: Final = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",  # "this network" / unspecified
        "169.254.0.0/16",  # link-local, incl. cloud metadata 169.254.169.254
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # documentation
        "192.88.99.0/24",  # 6to4 relay anycast
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",  # documentation
        "203.0.113.0/24",  # documentation
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, incl. broadcast
        "::/128",  # unspecified
        "100::/64",  # discard-only
        "2001::/23",  # IETF protocol assignments (Teredo, benchmarking, ORCHID)
        "2001:db8::/32",  # documentation
        "3fff::/20",  # documentation
        "fe80::/10",  # link-local
        "fec0::/10",  # deprecated site-local
        "ff00::/8",  # multicast
    )
)
# Reachable only as a registered private target (an operator's explicit decision).
_PRIVATE: Final = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",  # unique local
    )
)
# IPv6 forms that carry an IPv4 address: refused, whatever they embed.
_EMBEDDING_V4: Final = tuple(
    ipaddress.ip_network(n)
    for n in (
        "::ffff:0:0/96",  # IPv4-mapped
        "::/96",  # IPv4-compatible (deprecated)
        "64:ff9b::/96",  # NAT64 well-known prefix
        "64:ff9b:1::/48",  # local-use NAT64
        "2002::/16",  # 6to4
    )
)
_HOST_LABEL: Final = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_NUMERIC_LABEL: Final = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")


class OutboundRefused(Exception):
    """An outbound request that was not made, or failed. ``code`` is fixed and safe to
    show; nothing else about the target is."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Target:
    """A normalised ``scheme://host:port``. ``host`` is lower-case ASCII (IDNA), an IP
    literal without brackets."""

    scheme: str
    host: str
    port: int

    @property
    def origin(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


def _normalise_host(raw: str) -> str:
    host = raw.rstrip(".").lower() if not raw.startswith("[") else raw
    if not host:
        raise OutboundRefused("target_invalid")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        return str(literal)
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise OutboundRefused("target_invalid") from None
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise OutboundRefused("target_invalid")
    # A DNS name never ends in a numeric label: this is an IPv4 literal in a form
    # (127.1, 0x7f.0.0.1, 2130706433, 0177.0.0.1) that resolvers accept and
    # ipaddress does not -- refused rather than guessed.
    if _NUMERIC_LABEL.fullmatch(labels[-1]):
        raise OutboundRefused("target_invalid")
    return ascii_host


def parse_target(url: str, *, base: bool = True) -> Target:
    """The target of ``url``, or ``OutboundRefused("target_invalid")``. ``base``: an
    issuer or endpoint entered by a person (no query); otherwise a URL from a discovery
    document (a query is allowed). Never userinfo or a fragment."""
    if (
        not isinstance(url, str)
        or not url
        or len(url) > MAX_URL_LENGTH
        or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url)
    ):
        raise OutboundRefused("target_invalid")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise OutboundRefused("target_invalid") from None
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.hostname is None:
        raise OutboundRefused("target_invalid")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise OutboundRefused("target_invalid")
    if parts.fragment or "#" in url or (base and (parts.query or "?" in url)):
        raise OutboundRefused("target_invalid")
    bracketed = parts.netloc.startswith("[")
    host = _normalise_host(f"[{parts.hostname}]" if bracketed else parts.hostname)
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    if not 1 <= port <= 65535:
        raise OutboundRefused("target_invalid")
    return Target(parts.scheme, host, port)


def _embedded_v4(address: ipaddress.IPv6Address) -> bool:
    if address in (ipaddress.IPv6Address("::"), ipaddress.IPv6Address("::1")):
        return False  # unspecified and loopback, not IPv4-compatible forms
    return (
        address.ipv4_mapped is not None
        or address.sixtofour is not None
        or address.teredo is not None
        or any(address in network for network in _EMBEDDING_V4)
    )


def classify(address: IpAddress) -> Literal["public", "private", "refused"]:
    """``public`` (globally routable), ``private`` (allowed for a registered target
    only) or ``refused`` (never)."""
    if isinstance(address, ipaddress.IPv6Address) and (
        address.scope_id is not None or _embedded_v4(address)
    ):
        return "refused"
    if any(address in network for network in _ALWAYS_REFUSED):
        return "refused"
    if any(address in network for network in _PRIVATE):
        return "private"
    if not address.is_global or address.is_multicast or address.is_reserved:
        return "refused"
    return "public"


async def system_resolver(host: str, port: int) -> list[IpAddress]:
    """Every address the system resolver returns for ``host``."""
    try:
        infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise OutboundRefused("target_unreachable") from None
    found: list[IpAddress] = []
    for _family, _type, _proto, _name, sockaddr in infos:
        address = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])
        if address not in found:
            found.append(address)
    if not found:
        raise OutboundRefused("target_unreachable")
    return found


@dataclass(frozen=True)
class OutboundPolicy:
    """The rules for one organization and purpose. ``registered``: the exact targets an
    operator registered for them (``outbound_targets``); empty means public only."""

    registered: frozenset[Target] = frozenset()
    resolver: Resolver = field(default=system_resolver, compare=False)

    def is_registered(self, target: Target) -> bool:
        return target in self.registered

    def check_url(self, url: str, *, base: bool = True) -> Target:
        """Shape rules (no network): scheme, port, userinfo, query, fragment."""
        target = parse_target(url, base=base)
        if self.is_registered(target):
            return target
        if target.scheme != "https" or target.port != 443:
            raise OutboundRefused("target_invalid")
        return target

    def check_addresses(self, target: Target, addresses: Iterable[IpAddress]) -> list[IpAddress]:
        """Every address must pass; one that does not refuses the whole target."""
        allowed_private = self.is_registered(target)
        checked = list(addresses)
        if not checked:
            raise OutboundRefused("target_unreachable")
        for address in checked:
            kind = classify(address)
            if kind == "refused" or (kind == "private" and not allowed_private):
                raise OutboundRefused("target_not_public")
        return checked

    async def check_target(self, url: str, *, base: bool = True) -> Target:
        """Shape plus resolution, without connecting: for validating input (an issuer
        typed by an operator or administrator). The connection is checked again when
        it is made (``GuardedBackend``)."""
        target = self.check_url(url, base=base)
        literal = _literal(target.host)
        found = [literal] if literal is not None else await self.resolver(target.host, target.port)
        self.check_addresses(target, found)
        return target

    async def check_discovery_endpoints(
        self, issuer: str, endpoints: Iterable[object]
    ) -> list[Target]:
        """URLs a discovery document returned (authorization, token, JWKS): the same
        class as the issuer -- a public issuer cannot point Lattice at a private
        address, and an endpoint is never a different registered target unless the
        issuer is itself registered."""
        issuer_target = self.check_url(issuer)
        private_issuer = self.is_registered(issuer_target)
        checked: list[Target] = []
        for endpoint in endpoints:
            if not isinstance(endpoint, str):
                raise OutboundRefused("target_invalid")
            target = parse_target(endpoint, base=False)
            if self.is_registered(target) and not private_issuer:
                raise OutboundRefused("target_not_public")
            checked.append(await self.check_target(endpoint, base=False))
        return checked


def _literal(host: str) -> IpAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves, checks and connects in one step, to the address it checked: the
    connect-time half of the policy (DNS rebinding cannot pass between a check and a
    connect). Only the request's own target may be connected to."""

    def __init__(self, policy: OutboundPolicy, target: Target) -> None:
        self._policy = policy
        self._target = target
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            requested = _normalise_host(host)
        except OutboundRefused:
            raise httpcore.ConnectError("target_invalid") from None
        if (requested, port) != (self._target.host, self._target.port):
            raise httpcore.ConnectError("target_invalid")
        literal = _literal(requested)
        try:
            found = (
                [literal] if literal is not None else await self._policy.resolver(requested, port)
            )
            addresses = self._policy.check_addresses(self._target, found)
        except OutboundRefused as refused:
            raise httpcore.ConnectError(refused.code) from None
        last: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise httpcore.ConnectError("target_unreachable") from last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's backend interface
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("target_invalid")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def outbound_client(
    policy: OutboundPolicy,
    target: Target,
    *,
    ssl_context: ssl.SSLContext | None = None,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    timeout: float = TOTAL_TIMEOUT_SECONDS,
    base_url: str = "",
) -> httpx.AsyncClient:
    """An HTTP client that can reach ``target`` only, through ``GuardedBackend``: no
    redirects, no environment proxies, no retries. ``timeout``: per read/write/pool (a
    generation call may take minutes; discovery and keys keep the 10-second default)."""
    transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    # httpx 0.28 builds its httpcore pool privately; replace it with one whose network
    # backend is the guarded one (tests/test_outbound.py pins this attribute).
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=ssl_context or httpx.create_ssl_context(),
        max_connections=4,
        retries=0,
        network_backend=GuardedBackend(policy, target),
    )
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
        base_url=base_url,
    )


@dataclass(frozen=True)
class OutboundResponse:
    status_code: int
    headers: httpx.Headers
    body: bytes


async def fetch(
    policy: OutboundPolicy,
    method: str,
    url: str,
    *,
    base: bool = False,
    max_bytes: int = MAX_RESPONSE_BYTES,
    total_timeout: float = TOTAL_TIMEOUT_SECONDS,
    ssl_context: ssl.SSLContext | None = None,
    **kwargs: Any,
) -> OutboundResponse:
    """One bounded request under the policy. Raises ``OutboundRefused`` with a fixed
    code; a 3xx is ``redirect_refused``; the body is capped at ``max_bytes``."""
    target = policy.check_url(url, base=base)
    body = bytearray()
    try:
        async with asyncio.timeout(total_timeout):
            async with (
                outbound_client(policy, target, ssl_context=ssl_context) as client,
                client.stream(method, url, **kwargs) as response,
            ):
                if 300 <= response.status_code < 400:
                    raise OutboundRefused("redirect_refused")
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise OutboundRefused("response_too_large")
                return OutboundResponse(response.status_code, response.headers, bytes(body))
    except TimeoutError:
        raise OutboundRefused("timeout") from None
    except httpx.ConnectError as exc:
        code = str(exc)
        known = {"target_invalid", "target_not_public", "target_unreachable"}
        if isinstance(exc.__context__, ssl.SSLError) or "SSL" in code or "CERTIFICATE" in code:
            raise OutboundRefused("tls_failed") from None
        raise OutboundRefused(code if code in known else "target_unreachable") from None
    except httpx.TimeoutException:
        raise OutboundRefused("timeout") from None
    except httpx.HTTPError:
        raise OutboundRefused("target_unreachable") from None
