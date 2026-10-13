"""Checking an organization's OIDC connection without anyone signing in (Phase 12, P12.4;
phase-12-plan C4, C5).

Four steps, each through the organization's outbound policy, stopping at the first that
fails; the result is one fixed code:

1. **issuer** -- the policy's verdict on the issuer (shape, then every address it
   resolves to): ``outbound_blocked`` when refused (a private or non-standard address
   that no operator registered for this organization), ``provider_unavailable`` when the
   name does not resolve.
2. **discovery** -- the discovery document, fetched now (not from the cache): it must
   name exactly this issuer, offer S256 and point its endpoints at addresses of the same
   class (re-checked). Unreachable: ``provider_unavailable``; malformed:
   ``invalid_configuration``.
3. **keys** -- the JWKS: at least one signing key, else ``invalid_configuration``.
4. **client** -- a token request with a random, unusable authorization code and the
   configured client credentials. The provider's answer tells whether it accepts the
   *client*: ``invalid_client`` (or 401) is ``credentials_rejected``; refusing the *code*
   (``invalid_grant``, 400) is the expected answer and means the client is valid. A
   bogus code is never the provider's fault, so it never reads as ``provider_unavailable``.
   No person signs in and no session is created.

Nothing from the provider is returned or logged: only the step and the code.
"""

import secrets
from dataclasses import dataclass
from typing import Final, Literal

from lattice.identity.oidc import Connection, OidcFailure, OidcSignIn
from lattice.outbound import OutboundRefused

Step = Literal["issuer", "discovery", "keys", "client"]
_BLOCKED: Final = frozenset({"target_invalid", "target_not_public"})


@dataclass(frozen=True)
class Checked:
    result: str
    step: Step


def _failed(failure: OidcFailure, step: Step) -> Checked:
    if failure.outcome == "outbound_blocked":
        return Checked("outbound_blocked", step)
    if failure.outcome == "credentials_rejected":
        return Checked("credentials_rejected", step)
    if failure.outcome == "invalid_configuration":
        return Checked("invalid_configuration", step)
    return Checked("provider_unavailable", step)


async def check_connection(
    sign_in: OidcSignIn, connection: Connection, *, redirect_uri: str
) -> Checked:
    client = sign_in.client
    policy = await sign_in.policy(connection.organization_id)
    try:
        await policy.check_target(connection.issuer)
    except OutboundRefused as refused:
        blocked = refused.code in _BLOCKED
        return Checked("outbound_blocked" if blocked else "provider_unavailable", "issuer")
    try:
        metadata = await client.metadata(policy, connection.issuer, fresh=True)
    except OidcFailure as failure:
        return _failed(failure, "discovery")
    try:
        keys = await client.keys(policy, metadata.jwks_uri, fresh=True)
    except OidcFailure as failure:
        return _failed(failure, "keys")
    if not keys:
        return Checked("invalid_configuration", "keys")
    try:
        secret = await sign_in.client_secret(connection)
    except OidcFailure:
        # The configured secret cannot be presented (an unset environment reference, a
        # ciphertext this process cannot open): the client cannot authenticate.
        return Checked("credentials_rejected", "client")
    try:
        await client.exchange(
            policy,
            metadata,
            connection,
            code=secrets.token_urlsafe(32),
            verifier=secrets.token_urlsafe(64),
            redirect_uri=redirect_uri,
            client_secret=secret,
        )
    except OidcFailure as failure:
        if failure.rejected_code:
            return Checked("valid", "client")
        return _failed(failure, "client")
    # A provider that accepts a random code is not one Lattice can trust.
    return Checked("invalid_configuration", "client")
