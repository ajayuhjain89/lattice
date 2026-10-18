"""The organization's AI providers, credentials and egress policy, operated in the product
(Phase 12, P12.5; phase-12-plan C3, C4, C6 / D-34, C11, C12; ADR-017).

Every function runs bound to the administrator's organization (row-level security); the
caller checks the administrator capability first (E7). Writes go through the 0021 fenced
functions only -- the API role has no table write on providers, credentials, settings or
grants -- each with its audit event in the same transaction (deferred coupling triggers
refuse to commit one without the other).

**Seven facts, separate (C6.1).** A credential (set, replaced, revoked) and its test touch
neither the provider record nor the organization's settings; approval, enablement, data
terms, the ceilings and the default question sensitivity are changes of the **egress
policy**, and only those can widen what leaves Lattice.

**Egress changes.** One document describes a change (``SettingsChange`` /
``ProviderChange``). ``preview_change`` asks the database what it would do
(``lattice_preview_egress_change``: always rolled back) and shows Now -> After per data
level and provider. ``apply_change`` commits a change the database does **not** classify
as an escalation (lowering applies at once: the next request sees it). An escalation --
some level newly reaching some provider, or the default question sensitivity lowered --
commits only through ``start_step_up`` -> the identity provider -> ``apply_step_up``: a
typed confirmation of the organization's name and a reason first, then a fresh
re-authentication bound to this administrator, this session, this exact change and this
policy version (``lattice.identity.oidc``, ``lattice.providers.stepup``), consumed once.
The database decides what is an escalation and refuses one without its grant, whatever
the caller says.

**Providers.** Generation records are created disabled and unapproved; an Ollama endpoint
is an outbound target the operator registered for this organization (C4), and its trust
class follows it -- never chosen. Embedding is the deployment's: only its approval for this
organization can be changed here. **Credentials** are sealed here (AES-256-GCM, the API
holds the keys, C3.4) and stored by ``lattice_set_provider_credential``, which destroys
the previous ciphertext; an administrator never names an environment variable, and an
operator's environment credential is never another organization's fallback (credentials
are per organization; nothing falls back). **Tests** (``lattice.providers.tester``) send
no organization content and change no policy.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice import metrics
from lattice.access.audit import record_event
from lattice.db.engine import Database
from lattice.errors import LatticeError
from lattice.identity.oidc import OidcFailure, OidcSignIn, Started
from lattice.log import log_event
from lattice.orgs.outbound_targets import access_class, policy_for
from lattice.outbound import OutboundPolicy, Target
from lattice.providers import records
from lattice.providers.admin_models import (
    AiSetupStatus,
    CellOut,
    ChangePreview,
    ChangeRequest,
    CredentialOut,
    CredentialRequest,
    DestinationOut,
    EgressSettingsOut,
    EndpointRequest,
    LastTestOut,
    NewProviderRequest,
    ProviderChange,
    ProviderOut,
    ProviderSettings,
    RevokeCredentialRequest,
    SettingsChange,
    StepUpAvailability,
    StepUpGrantOut,
    StepUpOut,
    StepUpRequest,
    SupportedProviderOut,
    TargetOut,
    TestOut,
    Transition,
    change_document,
)
from lattice.providers.egress import LEVELS_ORDER, OrgEgress
from lattice.providers.fences import Refused, call, invalid, refusal
from lattice.providers.gemini_generation import DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
from lattice.providers.generation_factory import GenerationFactory
from lattice.providers.lattice import PolicyRecord, allowed_level, lattice
from lattice.providers.ollama_generation import endpoint_deployment
from lattice.providers.secrets import SecretStore, SecretUnavailable
from lattice.providers.tester import ProviderTester
from lattice.setup.derive import SetupFacts, ai_policy_category
from lattice.setup.facts import ai_facts
from lattice.vault.keys import Keyring
from lattice.vault.records import load_sealed
from lattice.vault.sealing import Binding, hint_for, seal

logger = logging.getLogger(__name__)

# The Gemini models offered in the product (checked against the official list,
# providers/gemini_generation.py); a deployment's operator may record others by CLI.
GEMINI_MODELS: Final = (GEMINI_DEFAULT_MODEL,)
VENDOR_ORIGINS: Final = {
    "gemini": "https://generativelanguage.googleapis.com",
    "openai": "https://api.openai.com",
}
CREDENTIAL_PROVIDERS: Final = frozenset({"gemini"})
CONFIGURED: Final = "provider.configured"
APPROVAL_CHANGED: Final = "provider.approval_changed"
ENABLED: Final = "provider.enabled"
DISABLED: Final = "provider.disabled"
LEVELS_CHANGED: Final = "egress.levels_changed"
ESCALATION_REFUSED: Final = "egress.escalation_refused"
# Refusals of an escalation worth an audit event (an attempt that did not commit).
_REFUSED_ESCALATIONS: Final = frozenset(
    {
        "step_up_required",
        "grant_invalid",
        "grant_consumed",
        "grant_expired",
        "grant_mismatch",
        "session_ended",
        "policy_changed",
    }
)
TEST_NOTE: Final = (
    "A test proves only that the credential or endpoint works for this provider "
    "operation. It does not approve or enable the provider, raise a ceiling or permit "
    "anything to be sent."
)

_RECORDS_SQL = text(
    "SELECT id, purpose, provider, model, deployment, endpoint, approved_for_level, "
    "approved_at IS NOT NULL AS approved, enabled, retention_mode, training_opt_out, "
    "last_test_at, last_test_result, last_test_credential_id FROM model_providers "
    "WHERE organization_id = :o ORDER BY (purpose = 'generation') DESC, provider, model, id"
)
_CREDENTIALS_SQL = text(
    "SELECT c.id, c.provider, c.secret_ref, c.data_use, c.created_at, s.hint "
    "FROM provider_credentials c LEFT JOIN encrypted_secrets s "
    "ON s.organization_id = c.organization_id AND s.owner_kind = 'provider_api_key' "
    "AND s.owner_id = c.id AND s.destroyed_at IS NULL "
    "WHERE c.organization_id = :o AND c.status = 'active'"
)
_VERSION_SQL = text(
    "SELECT coalesce((SELECT version FROM egress_policy_versions WHERE organization_id = :o), 0)"
)
_SETTINGS_SQL = text(
    "SELECT external_egress_level, organization_controlled_level, default_query_sensitivity "
    "FROM org_settings WHERE organization_id = :o"
)
_TARGETS_SQL = text(
    "SELECT id, scheme, host, port FROM outbound_targets WHERE organization_id = :o "
    "AND purpose = 'generation' AND revoked_at IS NULL ORDER BY created_at, id"
)
_STEP_UP_SQL = text(
    "SELECT c.step_up_acr_values, EXISTS (SELECT 1 FROM directory_identities d "
    "WHERE d.organization_id = c.organization_id AND d.connection_id = c.id "
    "AND d.membership_id = :m) AS bound FROM oidc_connections c "
    "WHERE c.organization_id = :o AND c.status = 'active'"
)


def _actor(membership_id: uuid.UUID) -> str:
    return f"member:{membership_id}"


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


# --- reads --------------------------------------------------------------------------------


async def policy_version(session: AsyncSession, org: uuid.UUID) -> int:
    found: int = (await session.execute(_VERSION_SQL, {"o": org})).scalar_one()
    return found


async def _records(session: AsyncSession, org: uuid.UUID) -> list[Any]:
    return list((await session.execute(_RECORDS_SQL, {"o": org})).all())


async def _record(session: AsyncSession, org: uuid.UUID, provider_id: uuid.UUID) -> Any:
    for row in await _records(session, org):
        if row.id == provider_id:
            return row
    raise refusal("provider_not_found")


def _policy_record(row: Any) -> PolicyRecord:
    return PolicyRecord(
        id=row.id,
        purpose=row.purpose,
        provider=row.provider,
        model=row.model,
        deployment=row.deployment,
        endpoint=row.endpoint,
        approved_for_level=row.approved_for_level,
        explicitly_approved=bool(row.approved),
        enabled=bool(row.enabled),
        training_opt_out=bool(row.training_opt_out),
    )


def _requires_credential(row: Any) -> bool:
    return bool(row.purpose == "generation" and row.provider in CREDENTIAL_PROVIDERS)


def _destination(row: Any, policy: OutboundPolicy) -> DestinationOut:
    if row.provider == "fake":
        return DestinationOut(kind="test_double", origin=None, access="deployment_local")
    if row.deployment == "local":
        return DestinationOut(
            kind="deployment_local", origin=row.endpoint, access="deployment_local"
        )
    if row.endpoint is not None:
        found = access_class(policy, row.endpoint)
        return DestinationOut(
            kind="registered_target",
            origin=row.endpoint,
            access=found,
        )
    return DestinationOut(
        kind="vendor_api", origin=VENDOR_ORIGINS.get(row.provider), access="public"
    )


@dataclass(frozen=True)
class _Credential:
    id: uuid.UUID
    provider: str
    secret_ref: str
    data_use: str | None
    created_at: datetime
    hint: str | None

    @property
    def scheme(self) -> str:
        return self.secret_ref.partition(":")[0]


async def _credentials(session: AsyncSession, org: uuid.UUID) -> dict[str, _Credential]:
    rows = await session.execute(_CREDENTIALS_SQL, {"o": org})
    return {
        r.provider: _Credential(r.id, r.provider, r.secret_ref, r.data_use, r.created_at, r.hint)
        for r in rows
    }


async def _resolvable(
    session: AsyncSession, org: uuid.UUID, credential: _Credential, secrets: SecretStore
) -> bool:
    sealed = await load_sealed(
        session,
        credential.secret_ref,
        organization_id=org,
        owner_kind="provider_api_key",
        owner_id=credential.id,
    )
    return secrets.available(credential.secret_ref, sealed)


def _credential_out(credential: _Credential | None, resolvable: bool) -> CredentialOut:
    if credential is None:
        return CredentialOut(
            state="none",
            id=None,
            source=None,
            managed_by=None,
            resolvable=False,
            data_use=None,
            hint=None,
            set_at=None,
        )
    return CredentialOut(
        state="active",
        id=credential.id,
        source=credential.scheme,
        managed_by="organization" if credential.scheme == "vault" else "operator",
        resolvable=resolvable,
        data_use=credential.data_use,
        hint=credential.hint,
        set_at=credential.created_at,
    )


def _target_out(row: Any) -> TargetOut:
    origin = Target(row.scheme, row.host, row.port).origin
    trust = endpoint_deployment(origin)
    return TargetOut(
        id=row.id,
        origin=origin,
        trust_class=trust,
        usable=trust == "local" or row.scheme == "https",
    )


def _supported(production: bool) -> list[SupportedProviderOut]:
    found = [
        SupportedProviderOut(
            provider="gemini",
            trust_class="external_api",
            requires_credential=True,
            models=list(GEMINI_MODELS),
            needs_target=False,
        ),
        SupportedProviderOut(
            provider="ollama",
            trust_class="organization_controlled",
            requires_credential=False,
            models=[],
            needs_target=True,
        ),
    ]
    if not production:
        found.append(
            SupportedProviderOut(
                provider="fake",
                trust_class="external_api",
                requires_credential=False,
                models=["fake-extractive-v1"],
                needs_target=False,
            )
        )
    return found


async def step_up_availability(
    session: AsyncSession, org: uuid.UUID, membership_id: uuid.UUID
) -> StepUpAvailability:
    row = (await session.execute(_STEP_UP_SQL, {"o": org, "m": membership_id})).one_or_none()
    if row is None:
        return StepUpAvailability(available=False, reason="no_connection", assurance_required=None)
    acr = list(row.step_up_acr_values) if row.step_up_acr_values else None
    if not row.bound:
        return StepUpAvailability(available=False, reason="no_identity", assurance_required=acr)
    return StepUpAvailability(available=True, reason="available", assurance_required=acr)


async def provider_settings(
    session: AsyncSession,
    org: uuid.UUID,
    membership_id: uuid.UUID,
    *,
    organization_name: str,
    secrets: SecretStore,
    factory: GenerationFactory,
    production: bool,
) -> ProviderSettings:
    egress = await records.org_egress(session, org)
    version = await policy_version(session, org)
    rows = await _records(session, org)
    credentials = await _credentials(session, org)
    resolvable = {
        name: await _resolvable(session, org, credential, secrets)
        for name, credential in credentials.items()
    }
    policy = await policy_for(session, org, "generation")
    targets = [_target_out(r) for r in await session.execute(_TARGETS_SQL, {"o": org})]
    providers: list[ProviderOut] = []
    for row in rows:
        credential = credentials.get(row.provider) if _requires_credential(row) else None
        level, blocked = allowed_level(egress, _policy_record(row))
        last_test = (
            None
            if row.last_test_at is None
            else LastTestOut(
                at=row.last_test_at,
                result=row.last_test_result,
                current=row.last_test_credential_id
                == (credential.id if credential is not None else None),
            )
        )
        providers.append(
            ProviderOut(
                id=row.id,
                purpose=row.purpose,
                provider=row.provider,
                model=row.model,
                deployment=row.deployment,
                approved_for_level=row.approved_for_level,
                explicitly_approved=bool(row.approved),
                enabled=bool(row.enabled),
                endpoint_configured=row.endpoint is not None,
                retention_mode=row.retention_mode,
                training_opt_out=bool(row.training_opt_out),
                credential=_credential_out(
                    credential, resolvable.get(row.provider, False) if credential else False
                ),
                managed_by="deployment" if row.purpose == "embedding" else "organization",
                destination=_destination(row, policy),
                requires_credential=_requires_credential(row),
                allowed_up_to=level,
                blocked_by=blocked if level is None else None,
                last_test=last_test,
            )
        )
    cells = lattice(egress, [_policy_record(r) for r in rows])
    category = ai_policy_category(
        SetupFacts(
            default_question_sensitivity=egress.default_query_sensitivity,
            ai=await ai_facts(
                session, org, egress.default_query_sensitivity, factory=factory, secrets=secrets
            ),
        )
    )
    return ProviderSettings(
        egress=EgressSettingsOut(
            external_egress_level=egress.external_egress_level,
            organization_controlled_level=egress.organization_controlled_level,
            default_query_sensitivity=egress.default_query_sensitivity,
            data_origin=egress.data_origin,
            version=version,
        ),
        providers=providers,
        lattice=[
            CellOut(
                provider_id=c.provider_id,
                level=c.level,
                permitted=c.permitted,
                reason=c.reason,
            )
            for c in cells
        ],
        targets=targets,
        supported=_supported(production),
        step_up=await step_up_availability(session, org, membership_id),
        confirmation=organization_name,
        status=AiSetupStatus(
            state=category.state, reason=category.reason, message=category.message
        ),
    )


# --- previews -----------------------------------------------------------------------------


def _document(change: SettingsChange | ProviderChange) -> dict[str, Any]:
    try:
        return change_document(change)
    except ValueError:
        raise invalid("change", "invalid") from None


async def _preview_raw(
    session: AsyncSession, actor: uuid.UUID, document: dict[str, Any]
) -> dict[str, Any]:
    try:
        found = await call(
            session,
            "SELECT lattice_preview_egress_change(:a, CAST(:c AS jsonb))",
            {"a": actor, "c": json.dumps(document)},
        )
    except Refused as refused:
        raise refusal(refused.reason) from None
    result: dict[str, Any] = dict(_json(found))
    return result


def _keys(cells: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    return {(str(c["p"]), str(c["e"]), str(c["l"])) for c in cells}


async def _preview_out(
    session: AsyncSession, org: uuid.UUID, document: dict[str, Any], raw: dict[str, Any]
) -> ChangePreview:
    rows = {str(r.id): r for r in await _records(session, org)}
    before, after = _keys(raw["before"]), _keys(raw["after"])

    def transitions(keys: set[tuple[str, str, str]], *, was: bool) -> list[Transition]:
        found: list[Transition] = []
        for provider_id, endpoint, level in sorted(
            keys, key=lambda k: (k[0], LEVELS_ORDER.index(k[2]))
        ):
            row = rows.get(provider_id)
            if row is None:
                continue
            found.append(
                Transition(
                    provider_id=row.id,
                    provider=row.provider,
                    model=row.model,
                    purpose=row.purpose,
                    trust_class=row.deployment,
                    destination=endpoint or VENDOR_ORIGINS.get(row.provider),
                    level=level,
                    before=was,
                    after=not was,
                )
            )
        return found

    settings = (await session.execute(_SETTINGS_SQL, {"o": org})).one_or_none()
    external = settings.external_egress_level if settings else "internal"
    controlled = settings.organization_controlled_level if settings else None
    if document["kind"] == "settings":
        external = document.get("external_egress_level", external)
        controlled = document.get("organization_controlled_level", controlled)
    remaining = ["authorization_first", f"question_floor:{raw['default_after']}"]
    if external is None:
        remaining.append("external_not_allowed")
    if controlled is None:
        remaining.append("organization_controlled_not_allowed")
    permitted_levels = {k[2] for k in after}
    remaining.extend(f"never_sent:{lvl}" for lvl in LEVELS_ORDER if lvl not in permitted_levels)
    remaining.append("credentials_never_widen")
    affected = [uuid.UUID(str(i)) for i in raw.get("affected") or []]
    if document["kind"] == "provider":
        affected.insert(0, uuid.UUID(str(document["provider_id"])))
    return ChangePreview(
        policy_version=int(raw["version"]),
        escalation=bool(raw["escalation"]),
        newly_allowed=transitions(after - before, was=False),
        no_longer_allowed=transitions(before - after, was=True),
        default_query_sensitivity={
            "before": raw["default_before"],
            "after": raw["default_after"],
        },
        remaining=remaining,
        affected_provider_ids=affected,
    )


async def preview_change(
    db: Database,
    org: uuid.UUID,
    actor: uuid.UUID,
    change: SettingsChange | ProviderChange,
) -> ChangePreview:
    """Now -> After from the database (nothing is written: the dry run is rolled back
    inside the function, and this transaction writes nothing else)."""
    document = _document(change)
    async with db.tenant_transaction(org) as session:
        raw = await _preview_raw(session, actor, document)
        return await _preview_out(session, org, document, raw)


# --- egress changes -----------------------------------------------------------------------


async def _audit_state(
    session: AsyncSession, org: uuid.UUID, document: dict[str, Any]
) -> dict[str, Any]:
    """The before/after fields of what ``document`` touches (no secret, no content)."""
    if document["kind"] == "settings":
        row = (await session.execute(_SETTINGS_SQL, {"o": org})).one_or_none()
        current = (
            {
                "external_egress_level": row.external_egress_level,
                "organization_controlled_level": row.organization_controlled_level,
                "default_query_sensitivity": row.default_query_sensitivity,
            }
            if row is not None
            else {
                "external_egress_level": "internal",
                "organization_controlled_level": None,
                "default_query_sensitivity": "internal",
            }
        )
        return {k: current[k] for k in document if k != "kind"}
    row = await _record(session, org, uuid.UUID(str(document["provider_id"])))
    return {
        "enabled": bool(row.enabled),
        "approval": row.approved_for_level if row.approved else None,
        "training_opt_out": bool(row.training_opt_out),
        "deployment": row.deployment,
        "endpoint": row.endpoint,
    }


async def _commit(
    session: AsyncSession,
    org: uuid.UUID,
    actor: uuid.UUID,
    session_id: uuid.UUID,
    document: dict[str, Any],
    version: int,
    reason: str,
    grant: Any = None,
) -> dict[str, Any]:
    """Apply under the database's rules and record the event; ``Refused`` otherwise."""
    before = await _audit_state(session, org, document)
    raw = _json(
        await call(
            session,
            "SELECT lattice_apply_egress_change(:a, :s, CAST(:c AS jsonb), :v, :g)",
            {
                "a": actor,
                "s": session_id,
                "c": json.dumps(document),
                "v": version,
                "g": None if grant is None else grant.id,
            },
        )
    )
    after = await _audit_state(session, org, document)
    escalation = bool(raw["escalation"])
    details: dict[str, Any] = {"escalation": escalation}
    target: dict[str, Any] = {}
    if escalation and grant is not None:
        target["grant_id"] = str(grant.id)
        details["step_up"] = {
            "grant_id": str(grant.id),
            "auth_time": grant.auth_time.isoformat(),
            "assurance": grant.acr,
        }
    if document["kind"] == "settings":
        action = LEVELS_CHANGED
        target["settings"] = sorted(k for k in document if k != "kind")
    else:
        row = await _record(session, org, uuid.UUID(str(document["provider_id"])))
        if "approval" in document:
            action = APPROVAL_CHANGED
        elif "enabled" in document:
            action = ENABLED if document["enabled"] else DISABLED
        else:
            action = CONFIGURED
        target.update(
            {
                "provider_id": str(row.id),
                "purpose": row.purpose,
                "provider": row.provider,
                "model": row.model,
            }
        )
        if raw.get("affected"):
            target["affected_provider_ids"] = [str(i) for i in raw["affected"]]
    await record_event(
        session,
        org,
        actor=_actor(actor),
        action=action,
        target=target,
        before=before,
        after={**after, **details},
        reason=reason,
    )
    log_event(
        logger,
        logging.INFO,
        "egress.changed",
        organization_id=str(org),
        action=action,
        escalation=escalation,
        policy_version=version,
    )
    result: dict[str, Any] = dict(raw)
    return result


async def _refused_escalation(
    db: Database,
    org: uuid.UUID,
    actor: uuid.UUID,
    document: dict[str, Any],
    reason_code: str,
    reason: str,
    step_up_id: uuid.UUID | None = None,
) -> None:
    """An escalation that did not commit: its own event (the change's transaction rolled
    back) and a metric."""
    metrics.EGRESS_ESCALATIONS.labels(
        outcome=metrics.bounded(reason_code, metrics.EGRESS_ESCALATION_OUTCOMES)
    ).inc()
    target: dict[str, Any] = {"kind": document.get("kind")}
    if "provider_id" in document:
        target["provider_id"] = str(document["provider_id"])
    if step_up_id is not None:
        target["step_up_id"] = str(step_up_id)
    async with db.tenant_transaction(org) as session:
        await record_event(
            session,
            org,
            actor=_actor(actor),
            action=ESCALATION_REFUSED,
            target=target,
            before=None,
            after={"refused": reason_code, "change": document},
            reason=reason,
        )


async def apply_change(
    db: Database,
    org: uuid.UUID,
    actor: uuid.UUID,
    session_id: uuid.UUID,
    request: ChangeRequest,
) -> None:
    """Commit a change that is not an escalation (the database refuses one:
    ``step_up_required``)."""
    document = _document(request.change)
    try:
        async with db.tenant_transaction(org) as session:
            await _commit(
                session, org, actor, session_id, document, request.policy_version, request.reason
            )
    except Refused as refused:
        if refused.reason == "step_up_required":
            await _refused_escalation(db, org, actor, document, refused.reason, request.reason)
        raise refusal(refused.reason) from None


# --- step-up (D-34) -----------------------------------------------------------------------


async def start_step_up(
    db: Database,
    sign_in: OidcSignIn,
    org: uuid.UUID,
    actor: uuid.UUID,
    session_id: uuid.UUID,
    *,
    organization_name: str,
    request: StepUpRequest,
) -> Started:
    """Check the escalation, its version and the typed confirmation, then send the
    administrator to the identity provider to authenticate again."""
    document = _document(request.change)
    async with db.tenant_transaction(org) as session:
        raw = await _preview_raw(session, actor, document)
        available = await step_up_availability(session, org, actor)
    if int(raw["version"]) != request.policy_version:
        raise refusal("policy_changed")
    if not raw["escalation"]:
        raise invalid("change", "not_escalation")
    if request.confirmation.strip() != organization_name:
        raise invalid("confirmation", "mismatch")
    if not available.available:
        raise invalid("step_up", f"unavailable_{available.reason}")
    try:
        started = await sign_in.start_step_up(
            org,
            actor,
            session_id,
            change=document,
            reason=" ".join(request.reason.split()),
            policy_version=request.policy_version,
        )
    except OidcFailure as failure:
        if failure.outcome == "unknown_connection":
            raise invalid("step_up", "unavailable_no_connection") from None
        raise invalid("step_up", failure.outcome) from None
    assert started.attempt_id is not None  # noqa: S101 - a step-up attempt
    return started


_ATTEMPT_SQL = text(
    "SELECT id, outcome, created_at, change, reason, policy_version FROM oidc_login_attempts "
    "WHERE organization_id = :o AND id = :t AND purpose = 'step_up' AND membership_id = :a "
    "AND session_id = :s"
)
_GRANT_SQL = text(
    "SELECT id, change, policy_version, auth_time, acr, expires_at, consumed_at, "
    "expires_at > now() AS live FROM step_up_grants WHERE organization_id = :o "
    "AND attempt_id = :t AND membership_id = :a AND session_id = :s"
)


async def step_up_result(
    db: Database,
    org: uuid.UUID,
    actor: uuid.UUID,
    session_id: uuid.UUID,
    step_up_id: uuid.UUID,
) -> StepUpOut:
    """The step-up's outcome, only for the administrator and session that started it
    (anything else is the same 404), with Now -> After recomputed from the policy now."""
    params = {"o": org, "t": step_up_id, "a": actor, "s": session_id}
    async with db.tenant_transaction(org) as session:
        attempt = (await session.execute(_ATTEMPT_SQL, params)).one_or_none()
        if attempt is None:
            raise refusal("provider_not_found")
        grant = (await session.execute(_GRANT_SQL, params)).one_or_none()
        current = await policy_version(session, org)
        change = _json(attempt.change)
        preview: ChangePreview | None = None
        if grant is not None and grant.consumed_at is None and grant.live:
            try:
                raw = await _preview_raw(session, actor, change)
            except LatticeError:  # a change that no longer applies has no preview
                preview = None
            else:
                preview = await _preview_out(session, org, change, raw)
    return StepUpOut(
        id=attempt.id,
        outcome=attempt.outcome or "pending",
        created_at=attempt.created_at,
        change=change,
        reason=attempt.reason,
        policy_version=attempt.policy_version,
        current_version=current,
        grant=None
        if grant is None
        else StepUpGrantOut(
            id=grant.id,
            expires_at=grant.expires_at,
            auth_time=grant.auth_time,
            assurance=grant.acr,
            consumed=grant.consumed_at is not None,
        ),
        preview=preview,
    )


async def apply_step_up(
    db: Database,
    org: uuid.UUID,
    actor: uuid.UUID,
    session_id: uuid.UUID,
    step_up_id: uuid.UUID,
) -> None:
    """Commit exactly the change the step-up authorized, once."""
    params = {"o": org, "t": step_up_id, "a": actor, "s": session_id}
    refused: Refused | None = None
    document: dict[str, Any] = {}
    reason = "Egress change"
    committed: dict[str, Any] = {}
    try:
        async with db.tenant_transaction(org) as session:
            attempt = (await session.execute(_ATTEMPT_SQL, params)).one_or_none()
            grant = (await session.execute(_GRANT_SQL, params)).one_or_none()
            if attempt is None or grant is None:
                raise refusal("provider_not_found")
            document = _json(grant.change)
            reason = attempt.reason
            if grant.consumed_at is not None:
                # Said first: the version it was bound to has moved on by its own use.
                raise Refused("grant_consumed")
            committed = await _commit(
                session,
                org,
                actor,
                session_id,
                document,
                int(grant.policy_version),
                attempt.reason,
                grant=grant,
            )
    except Refused as exc:
        refused = exc
    if refused is not None:
        if refused.reason in _REFUSED_ESCALATIONS:
            await _refused_escalation(
                db, org, actor, document, refused.reason, reason, step_up_id=step_up_id
            )
        raise refusal(refused.reason)
    if committed.get("escalation"):
        # Counted once the transaction (and its deferred audit coupling) has committed.
        metrics.EGRESS_ESCALATIONS.labels(outcome="committed").inc()


# --- providers ----------------------------------------------------------------------------


async def create_provider(
    db: Database,
    org: uuid.UUID,
    actor: uuid.UUID,
    request: NewProviderRequest,
    *,
    production: bool,
) -> uuid.UUID:
    """A generation record, disabled and unapproved: nothing can be sent to it yet."""
    if request.provider == "fake" and production:
        raise invalid("provider", "unsupported")
    if request.provider == "gemini":
        if request.model not in GEMINI_MODELS:
            raise invalid("model", "unsupported")
        if request.training_opt_out is None:
            raise invalid("training_opt_out", "required")
        if request.target_id is not None:
            raise invalid("target_id", "not_used")
    if request.provider == "ollama" and request.target_id is None:
        raise invalid("target_id", "required")
    if request.provider == "fake" and request.target_id is not None:
        raise invalid("target_id", "not_used")
    async with db.tenant_transaction(org) as session:
        try:
            provider_id: uuid.UUID = await call(
                session,
                "SELECT lattice_create_provider(:a, :p, :m, :t, :o)",
                {
                    "a": actor,
                    "p": request.provider,
                    "m": request.model.strip(),
                    "t": request.target_id,
                    "o": request.training_opt_out,
                },
            )
        except Refused as refused:
            raise refusal(refused.reason) from None
        row = await _record(session, org, provider_id)
        await record_event(
            session,
            org,
            actor=_actor(actor),
            action=CONFIGURED,
            target={
                "provider_id": str(provider_id),
                "purpose": row.purpose,
                "provider": row.provider,
                "model": row.model,
            },
            before=None,
            after={
                "deployment": row.deployment,
                "endpoint": row.endpoint,
                "enabled": False,
                "approval": None,
                "training_opt_out": bool(row.training_opt_out),
                "retention_mode": row.retention_mode,
            },
            reason=request.reason,
        )
    return provider_id


async def set_endpoint(
    db: Database,
    org: uuid.UUID,
    actor: uuid.UUID,
    provider_id: uuid.UUID,
    request: EndpointRequest,
) -> None:
    """Another registered target for an Ollama record; its approval is withdrawn."""
    async with db.tenant_transaction(org) as session:
        before = await _record(session, org, provider_id)
        try:
            await call(
                session,
                "SELECT lattice_set_provider_endpoint(:a, :p, :v, :t)",
                {"a": actor, "p": provider_id, "v": request.policy_version, "t": request.target_id},
            )
        except Refused as refused:
            raise refusal(refused.reason) from None
        await record_event(
            session,
            org,
            actor=_actor(actor),
            action=CONFIGURED,
            target={
                "provider_id": str(provider_id),
                "purpose": before.purpose,
                "provider": before.provider,
                "model": before.model,
            },
            before={
                "endpoint": before.endpoint,
                "approval": before.approved_for_level if before.approved else None,
            },
            after={
                "endpoint": (await _record(session, org, provider_id)).endpoint,
                "approval": None,
            },
            reason=request.reason,
        )


# --- credentials --------------------------------------------------------------------------


def _credential_state(credential: _Credential | None) -> dict[str, Any]:
    if credential is None:
        return {"credential": "none"}
    kept = {"vault": "encrypted", "env": "environment"}.get(credential.scheme, credential.scheme)
    return {"credential": kept, "data_use": credential.data_use}


async def set_credential(
    db: Database,
    keyring: Keyring,
    org: uuid.UUID,
    actor: uuid.UUID,
    provider_id: uuid.UUID,
    request: CredentialRequest,
) -> None:
    """Set or replace the organization's key for this record's provider: sealed here, the
    previous one revoked and its ciphertext destroyed. No policy changes."""
    async with db.tenant_transaction(org) as session:
        row = await _record(session, org, provider_id)  # 404 first
        if not _requires_credential(row):
            raise invalid("provider", "credential_not_used")
        if keyring.active is None:
            raise invalid("api_key", "secret_storage_unavailable")
        before = (await _credentials(session, org)).get(row.provider)
        credential_id, secret_id = uuid.uuid4(), uuid.uuid4()
        value = SecretStr(request.api_key.get_secret_value().strip())
        try:
            sealed = seal(
                keyring, value, Binding(org, secret_id, "provider_api_key", credential_id)
            )
        except (SecretUnavailable, ValueError):
            raise invalid("api_key", "secret_storage_unavailable") from None
        try:
            done: str = await call(
                session,
                "SELECT lattice_set_provider_credential(:a, :p, :e, :c, :s, :k, :n, :ct, :h, :d)",
                {
                    "a": actor,
                    "p": row.provider,
                    "e": request.expected_credential_id,
                    "c": credential_id,
                    "s": secret_id,
                    "k": sealed.key_id,
                    "n": sealed.nonce,
                    "ct": sealed.ciphertext,
                    "h": hint_for("provider_api_key", value),
                    "d": request.data_use,
                },
            )
        except Refused as refused:
            raise refusal(refused.reason) from None
        await record_event(
            session,
            org,
            actor=_actor(actor),
            action="credential.rotated" if done == "replaced" else "credential.set",
            target={"provider": row.provider},
            before=_credential_state(before),
            after={"credential": "encrypted", "data_use": request.data_use},
            reason=request.reason,
        )


async def revoke_credential(
    db: Database,
    org: uuid.UUID,
    actor: uuid.UUID,
    provider_id: uuid.UUID,
    request: RevokeCredentialRequest,
) -> None:
    """Revoke the active key (whoever manages it): excluded from the next request."""
    async with db.tenant_transaction(org) as session:
        row = await _record(session, org, provider_id)
        before = (await _credentials(session, org)).get(row.provider)
        try:
            await call(
                session,
                "SELECT lattice_revoke_provider_credential(:a, :p, :e)",
                {"a": actor, "p": row.provider, "e": request.expected_credential_id},
            )
        except Refused as refused:
            raise refusal(refused.reason) from None
        await record_event(
            session,
            org,
            actor=_actor(actor),
            action="credential.revoked",
            target={"provider": row.provider},
            before=_credential_state(before),
            after=None,
            reason=request.reason,
        )


# --- tests --------------------------------------------------------------------------------


async def test_provider(
    db: Database,
    tester: ProviderTester,
    secrets: SecretStore,
    org: uuid.UUID,
    provider_id: uuid.UUID,
) -> TestOut:
    """A synthetic check of the credential or endpoint (no organization content); the
    result is recorded on the record as a fact. Never changes the policy."""
    credential: _Credential | None = None
    secret: SecretStr | None = None
    async with db.tenant_snapshot(org) as session:
        row = await _record(session, org, provider_id)
        policy = await policy_for(session, org, "generation")
        if _requires_credential(row):
            credential = (await _credentials(session, org)).get(row.provider)
            if credential is not None:
                sealed = await load_sealed(
                    session,
                    credential.secret_ref,
                    organization_id=org,
                    owner_kind="provider_api_key",
                    owner_id=credential.id,
                )
                try:
                    secret = secrets.resolve(credential.secret_ref, sealed)
                except SecretUnavailable:
                    secret = None
    if row.purpose != "generation" or row.provider not in ("gemini", "ollama"):
        result = "unsupported"
    elif row.provider == "gemini":
        result = (
            "credential_missing"
            if secret is None
            else await tester.gemini(policy, row.model, secret)
        )
    else:
        result = (
            await tester.ollama(policy, row.endpoint, row.model)
            if row.endpoint
            else "configuration_invalid"
        )
    del secret
    async with db.tenant_transaction(org) as session:
        at: datetime = (
            await session.execute(
                text(
                    "UPDATE model_providers SET last_test_at = now(), last_test_result = :r, "
                    "last_test_credential_id = :c WHERE organization_id = :o AND id = :p "
                    "RETURNING last_test_at"
                ),
                {
                    "r": result,
                    "c": credential.id if credential is not None else None,
                    "o": org,
                    "p": provider_id,
                },
            )
        ).scalar_one()
    metrics.PROVIDER_TESTS.labels(
        provider=metrics.bounded(row.provider, metrics.PROVIDERS),
        result=metrics.bounded(result, metrics.PROVIDER_TEST_RESULTS),
    ).inc()
    log_event(
        logger,
        logging.INFO,
        "provider.tested",
        organization_id=str(org),
        provider_id=str(provider_id),
        provider=row.provider,
        result=result,
    )
    return TestOut(provider_id=provider_id, result=result, at=at, note=TEST_NOTE)


__all__ = [
    "GEMINI_MODELS",
    "OrgEgress",
    "apply_change",
    "apply_step_up",
    "create_provider",
    "preview_change",
    "provider_settings",
    "revoke_credential",
    "set_credential",
    "set_endpoint",
    "start_step_up",
    "step_up_result",
    "test_provider",
]
