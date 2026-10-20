"""Administrator provider tests (Phase 12, P12.5; phase-12-plan §11).

A test proves one thing: that the credential (or the endpoint) works for this provider's
operation. It changes no approval, no enablement, no ceiling and no permission to send
anything -- the egress lattice does not read it -- and it never sends organization
content: no question, no evidence, no document, no prompt at all.

* ``gemini`` -- ``GET /v1beta/models/{model}`` with the key: the model's metadata. It
  authenticates the key and checks the model name without generating anything, so it is
  not a billable generation request.
* ``ollama`` -- ``GET {endpoint}/api/tags``: the server's pulled models (no credential,
  no generation); ``configuration_invalid`` when the model is not among them.
* ``fake`` and embedding records -- ``unsupported`` (nothing to test, or a deployment
  provider the operator tests).

Both go through ``lattice.providers.http`` under the organization's ``generation``
outbound policy, exactly like a model call: the guard decides at connect time. Results
are fixed codes (``valid``, ``invalid_credential``, ``configuration_invalid``,
``provider_unavailable``, ``outbound_blocked``, ``credential_missing``, ``unsupported``);
nothing from the provider's response is kept or shown. Tests are logged and counted, not
audited (C11: they change no trust state).
"""

from dataclasses import dataclass
from typing import Any, Final

import httpx
from pydantic import SecretStr

from lattice.outbound import OutboundPolicy, OutboundRefused, Resolver
from lattice.providers.gemini_generation import DEFAULT_BASE_URL
from lattice.providers.http import provider_client, refused_code
from lattice.providers.ollama_generation import endpoint_deployment, validate_endpoint

TEST_TIMEOUT_SECONDS: Final = 10.0


@dataclass(frozen=True)
class ProviderTester:
    """``transport`` and ``resolver`` (tests only) replace the network and DNS after the
    same policy decision."""

    transport: httpx.AsyncBaseTransport | None = None
    resolver: Resolver | None = None
    gemini_base_url: str = DEFAULT_BASE_URL

    def _policy(self, policy: OutboundPolicy) -> OutboundPolicy:
        if self.resolver is None:
            return policy
        return OutboundPolicy(policy.registered, resolver=self.resolver)

    async def gemini(self, policy: OutboundPolicy, model: str, key: SecretStr) -> str:
        policy = self._policy(policy)
        try:
            async with provider_client(
                self.gemini_base_url,
                timeout_seconds=TEST_TIMEOUT_SECONDS,
                outbound=policy,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    f"/models/{model}", headers={"x-goog-api-key": key.get_secret_value()}
                )
        except (OutboundRefused, httpx.HTTPError) as exc:
            return "outbound_blocked" if refused_code(exc) else "provider_unavailable"
        status = response.status_code
        if status == 200:
            return "valid"
        if status in (400, 401, 403):
            # Gemini answers an unknown or disabled key with 400 API_KEY_INVALID or 403.
            return "invalid_credential"
        if status == 404:
            return "configuration_invalid"
        return "provider_unavailable"

    async def ollama(self, policy: OutboundPolicy, endpoint: str, model: str) -> str:
        policy = self._policy(policy)
        try:
            base = validate_endpoint(endpoint)
        except ValueError:
            return "configuration_invalid"
        try:
            async with provider_client(
                base,
                timeout_seconds=TEST_TIMEOUT_SECONDS,
                outbound=policy,
                transport=self.transport,
                local=endpoint_deployment(base) == "local",
            ) as client:
                response = await client.get("/api/tags")
        except (OutboundRefused, httpx.HTTPError) as exc:
            return "outbound_blocked" if refused_code(exc) else "provider_unavailable"
        if response.status_code != 200:
            return "provider_unavailable"
        try:
            body: Any = response.json()
            names = {str(m.get("name", "")) for m in body.get("models", [])}
        except (ValueError, AttributeError, TypeError):
            return "provider_unavailable"
        found = model in names or (":" not in model and f"{model}:latest" in names)
        return "valid" if found else "configuration_invalid"
