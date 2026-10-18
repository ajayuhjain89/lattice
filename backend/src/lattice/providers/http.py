"""The HTTP client a generation adapter calls its provider with (P12.5, phase-12-plan C4).

A provider call is an outbound request on the organization's behalf, so it goes through
``lattice.outbound`` exactly like an OIDC request: the client can reach the provider's
own origin only, every address is checked **at connect time** under the organization's
``generation`` policy (a public endpoint: https on 443 resolving to globally routable
addresses; a private or self-hosted server: only the exact origin an operator registered
for this organization), redirects are refused and environment proxies ignored. Validating
a URL when it is saved and then calling it with an unguarded client would prove nothing:
the guard is on the call itself.

Deployment-local servers (trust class ``local``: a loopback Ollama the operator runs beside
Lattice) are operator configuration and are not governed by the outbound policy (C4);
they get a plain client that still follows no redirects and ignores proxies.

``transport`` replaces the network in tests, after the same policy decision when a policy
is given (shape and resolution through its resolver).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import httpx

from lattice.outbound import OutboundPolicy, OutboundRefused, outbound_client

# The guard's refusals that are the policy's, not the provider's.
BLOCKED: Final = frozenset({"target_invalid", "target_not_public"})


def refused_code(exc: BaseException) -> str | None:
    """The outbound policy's code when ``exc`` is its refusal (directly, or as the
    connection error the guarded backend raised), else None."""
    if isinstance(exc, OutboundRefused):
        return exc.code if exc.code in BLOCKED else None
    if isinstance(exc, httpx.ConnectError) and str(exc) in BLOCKED:
        return str(exc)
    return None


@asynccontextmanager
async def provider_client(
    base_url: str,
    *,
    timeout_seconds: float,
    outbound: OutboundPolicy | None,
    transport: httpx.AsyncBaseTransport | None = None,
    local: bool = False,
) -> AsyncIterator[httpx.AsyncClient]:
    """A client for ``base_url``. Raises ``OutboundRefused`` before anything is sent when
    the policy refuses the endpoint's shape (or, with a test transport, its addresses)."""
    if transport is not None:
        if outbound is not None and not local:
            await outbound.check_target(base_url, base=True)
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            yield client
        return
    if local:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=timeout_seconds, follow_redirects=False, trust_env=False
        ) as client:
            yield client
        return
    # No policy given: public endpoints only (nothing private is ever reachable by default).
    policy = outbound if outbound is not None else OutboundPolicy()
    target = policy.check_url(base_url)
    async with outbound_client(
        policy, target, timeout=timeout_seconds, base_url=base_url
    ) as client:
        yield client
