"""The invocation rule applied to generation (security-model §6, ADR-011, D-13, D-15,
D-16), observed through what providers receive.

Authorization comes first (only readable evidence exists here); then configuration,
trust class, organization ceilings, approval, data terms, the question's sensitivity,
per-evidence classification -- and only then a credential, which never changes any of
the above. Anything that fails sends nothing and builds nothing.
"""

import logging
import uuid
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from lattice.answering.models import AskRequest
from lattice.answering.service import AskDependencies, answer_question
from lattice.authz import (
    AuthorizationInvariantViolation,
    readable_document_classifications,
    resolve_authorization_context,
)
from lattice.db.engine import Database
from lattice.providers import configuration
from lattice.providers.generation_factory import Declaration
from lattice.providers.records import CredentialRef, GenerationConfig
from lattice.providers.secrets import EnvironmentSecretStore
from lattice.vault.keys import Keyring
from lattice.vault.sealing import SealedSecret

from ..retrieval.world import FAKE, NOW, Org, new_org
from .helpers import ACTOR, Harness, configure_generation, run, set_credential

INTERNAL = "The onboarding checklist has five steps for new admins."
CONFIDENTIAL = "The onboarding escalation budget is LATTICE-CANARY-CONF-1234."
RESTRICTED = "The onboarding acquisition codename is LATTICE-CANARY-REST-5678."
KEY = "AIza-policy-test-key-DO-NOT-LEAK-0001"
QUESTION = "What does the onboarding checklist say?"


@pytest.fixture
async def org(owner_db: Database) -> Org:
    org = await new_org(owner_db, "halden")
    await org.group("exec")
    await org.member("grace", clearance="confidential", groups=["exec"])
    await org.member("tom")
    await org.document("internal", "Onboarding checklist", [INTERNAL])
    await org.document(
        "confidential", "Onboarding escalations", [CONFIDENTIAL], sensitivity="confidential"
    )
    await org.document(
        "restricted",
        "Onboarding acquisition memo",
        [RESTRICTED],
        sensitivity="restricted",
        access_mode="grants_only",
        grants=[("group", "exec")],
    )
    return org


def _sent(harness: Harness) -> str:
    return "\n".join(harness.payloads)


async def test_no_configuration_means_no_call(app_db: Database, org: Org) -> None:
    harness = Harness()
    outcome = await run(app_db, org, "grace", QUESTION, harness)
    assert outcome.answer.status == "evidence_only"
    assert outcome.answer.status_reason == "generation_unavailable"
    assert outcome.trace["egress"]["decision_reason"] == "not_configured"
    assert harness.builds == 0
    assert harness.requests == []
    assert {e.title for e in outcome.answer.evidence} >= {"Onboarding checklist"}


async def test_default_policy_sends_internal_only(app_db: Database, org: Org) -> None:
    await configure_generation(org)
    harness = Harness()
    outcome = await run(app_db, org, "grace", QUESTION, harness)
    sent = _sent(harness)
    assert INTERNAL in sent
    assert "LATTICE-CANARY" not in sent
    assert "Onboarding escalations" not in sent  # not even the title
    evidence = {e.title: e for e in outcome.answer.evidence}
    assert evidence["Onboarding checklist"].used_for_answer
    for title in ("Onboarding escalations", "Onboarding acquisition memo"):
        assert not evidence[title].used_for_answer  # still returned to the authorized asker
        assert evidence[title].not_used_reason == "egress_policy"
    notice = next(n for n in outcome.answer.notices if n.code == "egress_excluded")
    assert notice.count == 2


async def test_all_relevant_evidence_above_the_level_is_evidence_only(
    app_db: Database, org: Org
) -> None:
    await configure_generation(org)
    harness = Harness()
    outcome = await run(app_db, org, "grace", "acquisition codename", harness)
    assert outcome.answer.status == "evidence_only"
    assert outcome.answer.status_reason == "egress_policy"
    assert "LATTICE-CANARY" not in _sent(harness)


async def test_opt_in_admits_confidential_but_never_restricted(app_db: Database, org: Org) -> None:
    await configure_generation(org, level="confidential")
    await org.settings(external_egress_level="confidential")
    harness = Harness()
    await run(app_db, org, "grace", QUESTION, harness)
    sent = _sent(harness)
    assert "LATTICE-CANARY-CONF-1234" in sent
    assert "LATTICE-CANARY-REST-5678" not in sent


async def test_an_unapproved_provider_caps_the_level(app_db: Database, org: Org) -> None:
    await configure_generation(org)  # approved for internal only
    await org.settings(external_egress_level="restricted")
    harness = Harness()
    await run(app_db, org, "grace", QUESTION, harness)
    assert "LATTICE-CANARY" not in _sent(harness)


async def test_an_approval_does_not_raise_the_organization_ceiling(
    app_db: Database, org: Org
) -> None:
    await configure_generation(org, level="restricted")
    harness = Harness()
    await run(app_db, org, "grace", QUESTION, harness)
    assert "LATTICE-CANARY" not in _sent(harness)


@pytest.mark.parametrize(
    ("org_default", "declared"),
    [("internal", "confidential"), ("confidential", None), ("confidential", "internal")],
)
async def test_a_question_that_may_not_be_sent_is_never_sent(
    app_db: Database, org: Org, org_default: str, declared: str | None
) -> None:
    """D-13: the effective sensitivity is the stricter of the organization default and
    the declaration; a user cannot lower it."""
    await configure_generation(org)
    await org.settings(default_query_sensitivity=org_default)
    harness = Harness()
    outcome = await run(app_db, org, "tom", QUESTION, harness, classification=declared)
    assert outcome.answer.question_sensitivity == "confidential"
    assert outcome.answer.status == "evidence_only"
    assert outcome.answer.status_reason == "egress_policy"
    assert outcome.trace["egress"]["decision_reason"] == "question_classification"
    assert harness.requests == []
    assert harness.builds == 0


async def test_a_disabled_provider_receives_nothing(app_db: Database, org: Org) -> None:
    await configure_generation(org)
    async with org.owner.tenant_transaction(org.id) as s:
        await configuration.set_enabled(
            s,
            org.id,
            purpose="generation",
            provider="fake",
            model="fake-extractive-v1",
            enabled=False,
            reason="incident",
            actor=ACTOR,
        )
    harness = Harness()
    outcome = await run(app_db, org, "tom", QUESTION, harness)
    assert outcome.trace["egress"]["decision_reason"] == "provider_disabled"
    assert harness.builds == 0


async def test_an_unsupported_provider_receives_nothing(app_db: Database, org: Org) -> None:
    await configure_generation(org, provider="openai", model="gpt-x")
    harness = Harness()
    outcome = await run(app_db, org, "tom", QUESTION, harness)
    assert outcome.trace["egress"]["decision_reason"] == "provider_unsupported"
    assert harness.builds == 0


# --- credentials: necessary, never sufficient -------------------------------------------


async def _gemini(org: Org, *, data_origin: str) -> None:
    await org.settings(data_origin=data_origin)
    await configure_generation(org, provider="gemini", model="gemini-3.5-flash-lite")


async def test_an_unpaid_key_never_receives_customer_data(app_db: Database, org: Org) -> None:
    await _gemini(org, data_origin="customer")
    await set_credential(org, "gemini", "memory:halden-gemini", data_use="unpaid")
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
    outcome = await run(
        app_db, org, "tom", QUESTION, harness, memory={"halden-gemini": SecretStr(KEY)}
    )
    assert outcome.trace["egress"]["decision_reason"] == "provider_data_use"
    assert harness.secrets_seen == []  # not even resolved
    assert harness.builds == 0


async def test_a_credential_is_required_and_resolved_last(
    app_db: Database, org: Org, caplog: pytest.LogCaptureFixture
) -> None:
    await _gemini(org, data_origin="synthetic")
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
    memory = {"halden-gemini": SecretStr(KEY)}
    outcome = await run(app_db, org, "tom", QUESTION, harness, memory=memory)
    assert outcome.trace["egress"]["decision_reason"] == "credential_unavailable"
    assert harness.builds == 0

    await set_credential(org, "gemini", "memory:halden-gemini")
    with caplog.at_level(logging.DEBUG):
        outcome = await run(app_db, org, "tom", QUESTION, harness, memory=memory, persist=True)
    assert [s.get_secret_value() for s in harness.secrets_seen] == [KEY]
    assert harness.requests  # the call happened
    everywhere = (
        _sent(harness)
        + outcome.answer.model_dump_json()
        + str(outcome.trace)
        + caplog.text
        + "".join(str(getattr(r, "fields", "")) for r in caplog.records)
    )
    assert KEY not in everywhere
    assert "memory:halden-gemini" not in everywhere  # not even the reference


async def test_a_credential_cannot_widen_what_is_sent(app_db: Database, org: Org) -> None:
    await _gemini(org, data_origin="synthetic")
    await set_credential(org, "gemini", "memory:k", data_use="paid")
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
    await run(app_db, org, "grace", QUESTION, harness, memory={"k": SecretStr(KEY)})
    assert harness.requests
    assert "LATTICE-CANARY" not in _sent(harness)


async def test_revocation_and_rotation_take_effect_on_the_next_request(
    app_db: Database, org: Org
) -> None:
    await _gemini(org, data_origin="synthetic")
    memory = {"old": SecretStr(KEY), "new": SecretStr(KEY + "-rotated")}
    empty: dict[str, Any] = {
        "claims": [],
        "conflicts": [],
        "unknowns": [],
        "clarifying_question": "",
        "status": "insufficient_evidence",
    }
    await set_credential(org, "gemini", "memory:old")
    harness = Harness(outputs=[empty])
    await run(app_db, org, "tom", QUESTION, harness, memory=memory)
    await set_credential(org, "gemini", "memory:new")  # rotation
    await run(app_db, org, "tom", QUESTION, harness, memory=memory)
    assert [s.get_secret_value() for s in harness.secrets_seen] == [KEY, KEY + "-rotated"]
    async with org.owner.tenant_transaction(org.id) as s:
        assert await configuration.revoke_credential(
            s, org.id, provider="gemini", reason="leaked", actor=ACTOR
        )
    outcome = await run(app_db, org, "tom", QUESTION, harness, memory=memory)
    assert outcome.trace["egress"]["decision_reason"] == "credential_unavailable"
    assert harness.builds == 2


# --- trust classes (D-15) ---------------------------------------------------------------


async def test_a_local_endpoint_is_governed_by_its_approval_not_the_external_level(
    app_db: Database, org: Org
) -> None:
    await configure_generation(
        org, "ollama", "qwen3.5:4b", deployment="local", endpoint="http://127.0.0.1:11434"
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
    await run(app_db, org, "grace", QUESTION, harness)
    assert harness.requests
    assert "LATTICE-CANARY" not in _sent(harness)  # local, but approved for internal only
    await configure_generation(
        org,
        "ollama",
        "qwen3.5:4b",
        deployment="local",
        endpoint="http://127.0.0.1:11434",
        level="confidential",
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
    await run(app_db, org, "grace", QUESTION, harness)
    sent = _sent(harness)
    assert "LATTICE-CANARY-CONF-1234" in sent  # no external ceiling applies to local
    assert "LATTICE-CANARY-REST-5678" not in sent


@pytest.mark.parametrize(
    ("endpoint", "recorded"),
    [
        ("https://llm.halden.example", "local"),  # a remote server called "local"
        ("http://127.0.0.1:11434", "external_api"),
        ("http://127.0.0.1:11434", "organization_controlled"),
    ],
)
async def test_a_class_that_differs_from_the_endpoint_sends_nothing(
    app_db: Database, org: Org, endpoint: str, recorded: str
) -> None:
    await org.settings(organization_controlled_level="restricted")
    await configure_generation(
        org, "ollama", "qwen3.5:4b", deployment=recorded, endpoint=endpoint, level="internal"
    )
    harness = Harness()
    outcome = await run(app_db, org, "tom", QUESTION, harness)
    assert outcome.trace["egress"]["decision_reason"] == "provider_deployment_mismatch"
    assert harness.builds == 0


async def test_organization_controlled_needs_the_class_allowed_and_an_explicit_approval(
    app_db: Database, org: Org
) -> None:
    remote = "https://llm.halden.example"
    await configure_generation(
        org, "ollama", "qwen3.5:4b", deployment="organization_controlled", endpoint=remote
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
    first = await run(app_db, org, "tom", QUESTION, harness)
    assert first.trace["egress"]["decision_reason"] == "provider_unapproved"
    await configure_generation(
        org,
        "ollama",
        "qwen3.5:4b",
        deployment="organization_controlled",
        endpoint=remote,
        level="internal",
    )
    second = await run(app_db, org, "tom", QUESTION, harness)
    assert second.trace["egress"]["decision_reason"] == "deployment_not_allowed"
    assert harness.builds == 0
    await org.settings(organization_controlled_level="internal")
    await run(app_db, org, "tom", QUESTION, harness)
    assert harness.requests


class _Unknown(Harness):
    def declare(self, config: GenerationConfig, credential: CredentialRef | None) -> Declaration:
        return Declaration("private", True, False)  # type: ignore[arg-type]


async def test_an_unknown_declared_class_fails_closed(app_db: Database, org: Org) -> None:
    await configure_generation(org)
    harness = _Unknown()
    outcome = await run(app_db, org, "tom", QUESTION, harness)
    assert outcome.trace["egress"]["decision_reason"] == "deployment_unknown"
    assert harness.builds == 0


async def test_another_organizations_configuration_is_never_used(
    app_db: Database, owner_db: Database, org: Org
) -> None:
    other = await new_org(owner_db, "veridian")
    await configure_generation(other, level="restricted")
    await other.settings(external_egress_level="restricted")
    harness = Harness()
    outcome = await run(app_db, org, "tom", QUESTION, harness)
    assert outcome.trace["egress"]["decision_reason"] == "not_configured"
    assert harness.builds == 0


class _CountingStore(EnvironmentSecretStore):
    """Records every resolution attempt, successful or not."""

    def __init__(self, memory: dict[str, SecretStr]) -> None:
        super().__init__(environ={}, memory=memory)
        self.resolved: list[str] = []

    def resolve(self, reference: str, sealed: SealedSecret | None = None) -> SecretStr:
        self.resolved.append(reference)
        return super().resolve(reference, sealed)


@pytest.mark.parametrize(
    "setup",
    ["customer_data", "question_classification", "disabled", "class_not_allowed"],
)
async def test_no_secret_is_resolved_when_the_policy_refuses(
    app_db: Database, org: Org, setup: str
) -> None:
    """The credential is consulted last: when any policy step refuses, the secret store
    is never asked -- not even to check that the reference resolves."""
    await _gemini(org, data_origin="customer" if setup == "customer_data" else "synthetic")
    await set_credential(
        org, "gemini", "memory:k", data_use="paid" if setup != "customer_data" else "unpaid"
    )
    kwargs: dict[str, Any] = {}
    if setup == "question_classification":
        kwargs["classification"] = "confidential"
    if setup == "disabled":
        async with org.owner.tenant_transaction(org.id) as s:
            await configuration.set_enabled(
                s,
                org.id,
                purpose="generation",
                provider="gemini",
                model="gemini-3.5-flash-lite",
                enabled=False,
                reason="x",
                actor=ACTOR,
            )
    if setup == "class_not_allowed":
        await configure_generation(
            org,
            "ollama",
            "qwen3.5:4b",
            deployment="organization_controlled",
            endpoint="https://llm.halden.example",
            level="internal",
        )
    store = _CountingStore({"k": SecretStr(KEY)})
    harness = Harness()
    outcome = await answer_question(
        app_db,
        organization_id=org.id,
        membership_id=org.members["tom"],
        request=AskRequest(question=QUESTION, **kwargs),
        deps=AskDependencies(embedding=FAKE, generation=harness, secrets=store),
        now=NOW,
    )
    assert outcome.answer.status == "evidence_only"
    assert store.resolved == []
    assert harness.builds == 0


async def test_classifications_are_answered_for_readable_documents_only(
    app_db: Database, org: Org
) -> None:
    """The egress input is itself re-authorized: asking for the classification of a
    document the reader cannot read aborts the request (a bug, never filtered)."""
    async with app_db.tenant_snapshot(org.id) as s:
        ctx = await resolve_authorization_context(
            s, organization_id=org.id, membership_id=org.members["tom"]
        )
        readable = await readable_document_classifications(s, ctx, [org.documents["internal"]])
        assert readable == {org.documents["internal"]: "internal"}
        for hidden in (org.documents["confidential"], org.documents["restricted"], uuid.uuid4()):
            with pytest.raises(AuthorizationInvariantViolation):
                await readable_document_classifications(s, ctx, [org.documents["internal"], hidden])


# --- P12.1: an encrypted (vault:) credential ------------------------------------------------

_MASTER = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="
_OTHER_MASTER = "QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI="


def _keyring(material: str = _MASTER) -> Keyring:
    return Keyring.from_environment(
        {"LATTICE_SECRET_KEYS": f"k1:{material}", "LATTICE_SECRET_ACTIVE_KEY": "k1"}
    )


async def _vault_credential(org: Org) -> str:
    async with org.owner.tenant_transaction(org.id) as s:
        await configuration.set_credential(
            s,
            org.id,
            provider="gemini",
            value=SecretStr(KEY),
            keyring=_keyring(),
            data_use="paid",
            reason="test setup",
            actor=ACTOR,
        )
        reference: str = (
            await s.execute(
                text("SELECT secret_ref FROM provider_credentials WHERE status = 'active'")
            )
        ).scalar_one()
    return reference


def _empty_answer() -> dict[str, Any]:
    return {
        "claims": [],
        "conflicts": [],
        "unknowns": [],
        "clarifying_question": "",
        "status": "insufficient_evidence",
    }


async def test_an_encrypted_credential_is_decrypted_by_the_api_at_call_time(
    app_db: Database, org: Org, caplog: pytest.LogCaptureFixture
) -> None:
    await _gemini(org, data_origin="synthetic")
    reference = await _vault_credential(org)
    assert reference.startswith("vault:")
    harness = Harness(outputs=[_empty_answer()])
    store = EnvironmentSecretStore(environ={}, keyring=_keyring())
    with caplog.at_level(logging.DEBUG):
        outcome = await answer_question(
            app_db,
            organization_id=org.id,
            membership_id=org.members["tom"],
            request=AskRequest(question=QUESTION),
            deps=AskDependencies(embedding=FAKE, generation=harness, secrets=store),
            now=NOW,
        )
    assert [s.get_secret_value() for s in harness.secrets_seen] == [KEY]
    everywhere = (
        _sent(harness) + outcome.answer.model_dump_json() + str(outcome.trace) + caplog.text
    )
    assert KEY not in everywhere
    assert reference not in everywhere


@pytest.mark.parametrize("fault", ["no_keys", "wrong_key", "destroyed"])
async def test_an_encrypted_credential_that_cannot_be_opened_is_evidence_only(
    app_db: Database, org: Org, fault: str
) -> None:
    await _gemini(org, data_origin="synthetic")
    await _vault_credential(org)
    store = EnvironmentSecretStore(
        environ={},
        keyring=None
        if fault == "no_keys"
        else _keyring(_OTHER_MASTER if fault == "wrong_key" else _MASTER),
    )
    if fault == "destroyed":
        async with org.owner.tenant_transaction(org.id) as s:
            await s.execute(
                text(
                    "UPDATE encrypted_secrets SET nonce = NULL, ciphertext = NULL, hint = NULL, "
                    "destroyed_at = now()"
                )
            )
    harness = Harness(outputs=[_empty_answer()])
    outcome = await answer_question(
        app_db,
        organization_id=org.id,
        membership_id=org.members["tom"],
        request=AskRequest(question=QUESTION),
        deps=AskDependencies(embedding=FAKE, generation=harness, secrets=store),
        now=NOW,
    )
    assert outcome.trace["egress"]["decision_reason"] == "credential_unavailable"
    assert harness.builds == 0


async def test_rotating_and_revoking_an_encrypted_credential_destroys_its_ciphertext(
    org: Org,
) -> None:
    await _gemini(org, data_origin="synthetic")
    first = await _vault_credential(org)
    second = await _vault_credential(org)  # rotation
    assert first != second
    async with org.owner.tenant_transaction(org.id) as s:
        await configuration.revoke_credential(
            s, org.id, provider="gemini", reason="test", actor=ACTOR
        )
        rows = (
            await s.execute(
                text(
                    "SELECT destroyed_at IS NOT NULL AS gone, ciphertext IS NULL AS empty "
                    "FROM encrypted_secrets"
                )
            )
        ).all()
        events = (
            await s.execute(
                text(
                    "SELECT action, before, after FROM audit_events "
                    "WHERE action LIKE 'credential.%' ORDER BY created_at"
                )
            )
        ).all()
    assert [tuple(r) for r in rows] == [(True, True), (True, True)]
    assert [e.action for e in events] == [
        "credential.set",
        "credential.rotated",
        "credential.revoked",
    ]
    assert events[0].after["scheme"] == "vault"
    assert KEY not in str(events)
