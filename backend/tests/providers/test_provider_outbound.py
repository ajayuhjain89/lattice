"""Provider calls go through the outbound policy (Phase 12, P12.5; phase-12-plan C4):
validating an endpoint when it is saved proves nothing if the call is made with another
client, so the guard is on the call itself.

Proves: the adapters connect through ``GuardedBackend`` under the organization's
generation policy (no environment proxies, no redirects); a private address -- by DNS
answer at connect time -- is refused unless the exact origin is registered for the
organization; a refusal is ``generation_failed:outbound_blocked`` and nothing is sent; a
deployment-local (loopback) server is the operator's and not governed; the factory hands
the policy to the adapters; the invocation rule refuses an organization-controlled
endpoint the policy does not permit before anything is built."""

import ipaddress
import uuid
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from lattice.answering import service
from lattice.answering.models import ANSWER_SCHEMA
from lattice.config import Environment
from lattice.db.engine import Database
from lattice.orgs.outbound_targets import revoke_target
from lattice.outbound import GuardedBackend, IpAddress, OutboundPolicy, parse_target
from lattice.providers.egress import ProviderRecord
from lattice.providers.gemini_generation import GeminiGenerationProvider
from lattice.providers.generation import GenerationError, GenerationRequest
from lattice.providers.generation_factory import DefaultGenerationFactory
from lattice.providers.http import provider_client
from lattice.providers.ollama_generation import OllamaGenerationProvider
from lattice.providers.records import CredentialRef, GenerationConfig

from ..answering.helpers import Harness, configure_generation, run
from ..retrieval.world import Org, new_org
from .admin_world import register_generation_target

REQUEST = GenerationRequest(system="s", prompt="p", schema=ANSWER_SCHEMA)
KEY = SecretStr("AIza-outbound-test-key-0001")


def _resolver(address: str) -> Any:
    async def resolve(host: str, port: int) -> list[IpAddress]:
        return [ipaddress.ip_address(address)]

    return resolve


def _ok(request: httpx.Request) -> httpx.Response:
    if "generativelanguage" in request.url.host:
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    return httpx.Response(200, json={"message": {"content": "{}"}, "done_reason": "stop"})


async def test_gemini_is_refused_when_its_name_resolves_privately() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    provider = GeminiGenerationProvider(
        KEY,
        transport=httpx.MockTransport(handler),
        outbound=OutboundPolicy(resolver=_resolver("10.1.2.3")),
    )
    with pytest.raises(GenerationError, match="outbound_blocked"):
        await provider.generate(REQUEST)
    assert seen == []  # nothing left Lattice
    public = GeminiGenerationProvider(
        KEY,
        transport=httpx.MockTransport(handler),
        outbound=OutboundPolicy(resolver=_resolver("142.250.80.42")),
    )
    await public.generate(REQUEST)
    assert len(seen) == 1


async def test_an_organization_controlled_server_needs_its_registration() -> None:
    endpoint = "https://llm.halden.internal:11434"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    unregistered = OllamaGenerationProvider(
        endpoint=endpoint,
        transport=httpx.MockTransport(handler),
        outbound=OutboundPolicy(resolver=_resolver("10.20.30.40")),
    )
    assert unregistered.deployment == "organization_controlled"
    with pytest.raises(GenerationError, match="outbound_blocked"):
        await unregistered.generate(REQUEST)
    registered = OllamaGenerationProvider(
        endpoint=endpoint,
        transport=httpx.MockTransport(handler),
        outbound=OutboundPolicy(
            frozenset({parse_target(endpoint)}), resolver=_resolver("10.20.30.40")
        ),
    )
    await registered.generate(REQUEST)
    assert len(seen) == 1


async def test_a_loopback_server_is_the_operators_and_not_governed() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    local = OllamaGenerationProvider(
        endpoint="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
        outbound=OutboundPolicy(resolver=_resolver("10.0.0.1")),
    )
    assert local.deployment == "local"
    await local.generate(REQUEST)
    assert len(seen) == 1


async def test_the_real_client_is_guarded_and_refuses_at_connect_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    policy = OutboundPolicy(resolver=_resolver("169.254.169.254"))
    async with provider_client(
        "https://generativelanguage.googleapis.com/v1beta",
        timeout_seconds=5,
        outbound=policy,
    ) as client:
        backend = client._transport._pool._network_backend  # type: ignore[attr-defined]
        assert isinstance(backend, GuardedBackend)
        assert client.follow_redirects is False
        assert client._trust_env is False
        with pytest.raises(httpx.ConnectError, match="target_not_public"):
            await client.get("/models")
    provider = GeminiGenerationProvider(KEY, outbound=policy)
    with pytest.raises(GenerationError, match="outbound_blocked"):
        await provider.generate(REQUEST)


def test_the_factory_hands_the_policy_to_the_adapters() -> None:
    policy = OutboundPolicy()
    factory = DefaultGenerationFactory(Environment.TEST)
    gemini = factory.build(
        GenerationConfig(
            ProviderRecord(
                "gemini", "gemini-3.5-flash-lite", "external_api", "internal", True, True
            ),
            None,
            True,
            outbound=policy,
        ),
        CredentialRef(id=uuid.uuid4(), provider="gemini", secret_ref="memory:k", data_use="paid"),
        KEY,
    )
    assert gemini._outbound is policy  # type: ignore[attr-defined]
    ollama = factory.build(
        GenerationConfig(
            ProviderRecord("ollama", "q", "organization_controlled", "internal", True, True),
            "https://llm.halden.internal:11434",
            True,
            outbound=policy,
        ),
        None,
        None,
    )
    assert ollama._outbound is policy  # type: ignore[attr-defined]


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.member("tom")
    await org.document("internal", "Onboarding checklist", ["The checklist has five steps."])
    await org.settings(organization_controlled_level="internal")
    return org


async def test_the_invocation_rule_refuses_an_endpoint_the_policy_does_not_permit(
    app_db: Database, owner_db: Database, org: Org
) -> None:
    endpoint = "https://llm.halden.internal:11434"  # not on 443: needs a registration
    await configure_generation(
        org,
        "ollama",
        "qwen3.5:4b",
        deployment="organization_controlled",
        endpoint=endpoint,
        level="internal",
    )
    harness = Harness(
        outputs=[
            {
                "claims": [],
                "conflicts": [],
                "unknowns": [],
                "clarifying_question": "",
                "status": "insufficient_evidence",
            }
        ]
    )
    refused = await run(app_db, org, "tom", "checklist steps", harness)
    assert refused.trace["egress"]["decision_reason"] == "outbound_blocked"
    assert refused.answer.status_reason == "egress_policy"
    assert harness.builds == 0
    await register_generation_target(owner_db, org.id, endpoint)
    allowed = await run(app_db, org, "tom", "checklist steps", harness)
    assert allowed.trace["egress"]["decision_reason"] is None
    assert len(harness.requests) == 1


async def test_a_registration_revoked_before_the_call_stops_it(
    app_db: Database, owner_db: Database, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapter is built with the request snapshot's registrations; the call-time
    re-check reads them again, so an operator's revocation in between stops the call."""
    endpoint = "https://llm.halden.internal:11434"
    await configure_generation(
        org,
        "ollama",
        "qwen3.5:4b",
        deployment="organization_controlled",
        endpoint=endpoint,
        level="internal",
    )
    target = await register_generation_target(owner_db, org.id, endpoint)
    real = service.recheck_generation  # type: ignore[attr-defined]
    seen: list[str | None] = []

    async def recheck(*args: Any, **kwargs: Any) -> str | None:
        async with owner_db.tenant_transaction(org.id) as s:
            await revoke_target(s, org.id, target, reason="decommissioned", actor="operator:t")
        found = await real(*args, **kwargs)
        seen.append(found)
        return found

    monkeypatch.setattr(service, "recheck_generation", recheck)
    harness = Harness(
        outputs=[
            {
                "claims": [],
                "conflicts": [],
                "unknowns": [],
                "clarifying_question": "",
                "status": "insufficient_evidence",
            }
        ]
    )
    outcome = await run(app_db, org, "tom", "checklist steps", harness)
    assert seen == ["outbound_blocked"]
    assert harness.builds == 1  # built when the request began ...
    assert harness.requests == []  # ... and never called
    assert outcome.answer.status_reason == "egress_policy"
    assert outcome.trace["egress"]["decision_reason"] == "outbound_blocked"
