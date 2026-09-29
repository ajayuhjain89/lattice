"""Whether, and with what, generation may be invoked (security-model §6; ADR-011).

The same invocation rule as document and query embedding, applied to generation:

1. the asker is **authorized** for every evidence item -- decided before this module, in
   SQL, and re-checked here: classifications are read only for readable documents
   (``authz.readable_document_classifications`` aborts on anything else);
2. the organization's **generation configuration** exists and is enabled
   (``not_configured``, ``provider_disabled``) and names a supported implementation
   (``provider_unsupported``, ``endpoint_invalid``);
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
6. only then the **credential**: an active reference for this organization and provider
   that its secret store resolves (``credential_unavailable``). A credential never
   changes 1-5.

Every failure is a fixed reason code; nothing is sent and no provider is built.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from lattice.authz import AuthorizationContext, readable_document_classifications
from lattice.providers import records
from lattice.providers.egress import may_send, provider_decision
from lattice.providers.generation import GenerationProvider
from lattice.providers.generation_factory import GenerationFactory, GenerationUnavailable
from lattice.providers.secrets import SecretStore, SecretUnavailable
from lattice.retrieval import Evidence


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
    resolves the secret (no database access) and builds the provider only when every
    policy check has passed and ``invoke`` says a call will be made."""
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
        )

    if not config.enabled:
        return refuse("provider_disabled")
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
    if not invoke:
        return refuse(None, decision.level)
    secret = None
    if declared.requires_credential:
        if credential is None:
            return refuse("credential_unavailable", decision.level)
        try:
            secret = secrets.resolve(credential.secret_ref)
        except SecretUnavailable:
            return refuse("credential_unavailable", decision.level)
    try:
        provider = factory.build(config, credential, secret)
    except GenerationUnavailable as exc:
        return refuse(exc.code, decision.level)
    if provider.deployment != declared.deployment:
        # The built adapter must be exactly what was declared and approved.
        return refuse("provider_deployment_mismatch", decision.level)
    access = refuse(None, decision.level)
    return GenerationAccess(
        provider,
        access.level,
        None,
        provider_name=access.provider_name,
        model=access.model,
        deployment=access.deployment,
        classifications=classifications,
    )
