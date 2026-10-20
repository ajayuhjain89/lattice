"""Whether, and with what, generation may be invoked (security-model §6; ADR-011).

The same invocation rule as document and query embedding, applied to generation:

1. the asker is **authorized** for every evidence item -- decided before this module, in
   SQL, and re-checked here: classifications are read only for readable documents
   (``authz.readable_document_classifications`` aborts on anything else);
2. the organization's **generation configuration** exists, is enabled and was
   **explicitly approved** by an administrator or operator (``not_configured``,
   ``provider_disabled``, ``provider_unapproved``; P12.5: an enabled switch is not an
   approval) and names a supported implementation (``provider_unsupported``,
   ``endpoint_invalid``);
3. ``providers.egress.provider_decision``: the recorded trust class is known and equals
   the class the implementation declares, the organization allows that class, the data
   terms suit the organization's data, and the effective level is
   ``min(class ceiling, approval)`` (``deployment_unknown``,
   ``provider_deployment_mismatch``, ``deployment_not_allowed``, ``provider_unapproved``,
   ``provider_data_use``);
4. the **question's effective sensitivity** (D-13, computed by retrieval) is within that
   level -- otherwise the question may not be placed in the prompt at all
   (``question_classification``);
5. each evidence item's classification is within the level; the rest stays in the
   asker's evidence list, marked not used (``egress_policy``);
6. an ``organization_controlled`` endpoint must be permitted by the organization's
   **outbound policy** for generation (an operator-registered target, or a public https
   endpoint; ``outbound_blocked``) -- and the built adapter connects only through that
   policy, checked again at connect time (``lattice.providers.http``);
7. only then the **credential**: an active reference for this organization and provider
   that its secret store resolves (``credential_unavailable``). A credential never
   changes 1-6.

Every failure is a fixed reason code; nothing is sent and no provider is built.

**At invocation (P12.5).** The decision above is made in the request's snapshot, before
retrieval's evidence is assembled into a prompt; a model call may begin seconds later.
``recheck_generation`` repeats it in a fresh snapshot immediately before **each** call:
the same enabled, approved record at the same destination, a level that still admits the
question and every prompt document (re-read, and still readable by the asker), the same
active credential, and for an organization-controlled endpoint its live operator
registration. A disabled or unapproved provider, a lowered ceiling, a revoked credential,
a revoked registration or a changed destination therefore stops the next call. A request
already in flight is not cancelled: its content was permitted when it was sent.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace

from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import (
    AuthorizationContext,
    current_readable_classifications,
    readable_document_classifications,
)
from lattice.db.engine import Database
from lattice.orgs.outbound_targets import policy_for
from lattice.outbound import OutboundRefused
from lattice.providers import records
from lattice.providers.egress import may_send, more_restrictive, provider_decision
from lattice.providers.generation import GenerationProvider
from lattice.providers.generation_factory import GenerationFactory, GenerationUnavailable
from lattice.providers.secrets import SecretStore, SecretUnavailable
from lattice.retrieval import Evidence
from lattice.vault.records import load_sealed


@dataclass(frozen=True)
class GenerationAccess:
    """The outcome for one request. ``provider`` is set only when a call may happen."""

    provider: GenerationProvider | None
    level: str | None
    reason: str | None
    provider_name: str | None = None
    model: str | None = None
    deployment: str | None = None
    classifications: dict[uuid.UUID, str] = field(default_factory=dict)
    # What the call-time re-check compares (P12.5): the record, its destination, the
    # data terms the implementation declared and the credential it was built with.
    record_id: uuid.UUID | None = None
    endpoint: str | None = None
    training_opt_out: bool = True
    credential_id: uuid.UUID | None = None

    def permits(self, e: Evidence) -> bool:
        return self.level is not None and may_send(
            self.classifications.get(e.document_id, "restricted"), self.level
        )


async def resolve_generation(
    session: AsyncSession,
    ctx: AuthorizationContext,
    evidence: Sequence[Evidence],
    question_sensitivity: str,
    *,
    factory: GenerationFactory,
    secrets: SecretStore,
    invoke: bool = True,
) -> GenerationAccess:
    """Run inside the request's snapshot. Reads configuration and credential references;
    resolves the secret (a ``vault:`` one is read from ``encrypted_secrets`` bound to
    this credential) and builds the provider only when every policy check has passed
    and ``invoke`` says a call will be made."""
    classifications = await readable_document_classifications(
        session, ctx, {e.document_id for e in evidence}
    )
    config = await records.generation_config(session, ctx.organization_id)
    if config is None:
        return GenerationAccess(None, None, "not_configured", classifications=classifications)
    deployment: str | None = None

    def refuse(reason: str | None, level: str | None = None) -> GenerationAccess:
        assert config is not None  # noqa: S101 - narrowed above
        return GenerationAccess(
            None,
            level,
            reason,
            provider_name=config.record.provider,
            model=config.record.model,
            deployment=deployment,
            classifications=classifications,
            record_id=config.id,
            endpoint=config.endpoint,
        )

    if not config.enabled:
        return refuse("provider_disabled")
    if not config.record.explicitly_approved:
        return refuse("provider_unapproved")
    credential = await records.active_credential(
        session, ctx.organization_id, config.record.provider
    )
    try:
        declared = factory.declare(config, credential)
    except GenerationUnavailable as exc:
        return refuse(exc.code)
    deployment = declared.deployment
    org = await records.org_egress(session, ctx.organization_id)
    decision = provider_decision(
        org,
        config.record,
        deployment=declared.deployment,
        training_opt_out=declared.training_opt_out,
    )
    if decision.level is None:
        return refuse(decision.reason)
    if not may_send(question_sensitivity, decision.level):
        return refuse("question_classification", decision.level)
    outbound = await policy_for(session, ctx.organization_id, "generation")
    if declared.deployment == "organization_controlled":
        try:
            outbound.check_url(config.endpoint or "")
        except OutboundRefused:
            return refuse("outbound_blocked", decision.level)
    config = replace(config, outbound=outbound)
    if not invoke:
        return refuse(None, decision.level)
    secret = None
    if declared.requires_credential:
        if credential is None:
            return refuse("credential_unavailable", decision.level)
        sealed = await load_sealed(
            session,
            credential.secret_ref,
            organization_id=ctx.organization_id,
            owner_kind="provider_api_key",
            owner_id=credential.id,
        )
        try:
            secret = secrets.resolve(credential.secret_ref, sealed)
        except SecretUnavailable:
            return refuse("credential_unavailable", decision.level)
    try:
        provider = factory.build(config, credential, secret)
    except GenerationUnavailable as exc:
        return refuse(exc.code, decision.level)
    if provider.deployment != declared.deployment:
        # The built adapter must be exactly what was declared and approved.
        return refuse("provider_deployment_mismatch", decision.level)
    return replace(
        refuse(None, decision.level),
        provider=provider,
        training_opt_out=declared.training_opt_out,
        credential_id=credential.id if credential is not None else None,
    )


async def recheck_generation(
    db: Database,
    ctx: AuthorizationContext,
    access: GenerationAccess,
    question_sensitivity: str,
    document_ids: Collection[uuid.UUID],
) -> str | None:
    """Immediately before a model call: None if the call may still happen, else why not
    (``policy_changed``: the record, its approval, enablement, destination or the
    organization's ceilings no longer admit the question or a prompt document, or a
    document is no longer readable by the asker; ``outbound_blocked``: an
    organization-controlled endpoint's registration was revoked; ``credential_revoked``). A fresh
    snapshot, so changes committed since the request began are seen."""
    async with db.tenant_snapshot(ctx.organization_id) as session:
        config = await records.generation_config(session, ctx.organization_id)
        if (
            config is None
            or config.id != access.record_id
            or not config.enabled
            or not config.record.explicitly_approved
            or config.endpoint != access.endpoint
            or access.deployment is None
        ):
            return "policy_changed"
        if access.deployment == "organization_controlled":
            # The adapter was built with the registrations of the request's snapshot; an
            # operator who revoked the endpoint's registration since then stops the call.
            outbound = await policy_for(session, ctx.organization_id, "generation")
            try:
                outbound.check_url(config.endpoint or "")
            except OutboundRefused:
                return "outbound_blocked"
        org = await records.org_egress(session, ctx.organization_id)
        decision = provider_decision(
            org,
            config.record,
            deployment=access.deployment,
            training_opt_out=access.training_opt_out,
        )
        # D-13: a default raised meanwhile applies -- the stricter trusted value wins.
        sensitivity = more_restrictive(question_sensitivity, org.default_query_sensitivity)
        if decision.level is None or not may_send(sensitivity, decision.level):
            return "policy_changed"
        if access.credential_id is not None:
            credential = await records.active_credential(
                session, ctx.organization_id, config.record.provider
            )
            if credential is None or credential.id != access.credential_id:
                return "credential_revoked"
        classifications = await current_readable_classifications(session, ctx, document_ids)
        if classifications is None or not all(
            may_send(level, decision.level) for level in classifications.values()
        ):
            return "policy_changed"
    return None
