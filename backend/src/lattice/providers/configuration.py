"""Organization-scoped provider configuration and credentials (D-16, ADR-011).

The minimum Phase 6 backend for BYOK, below the Phase 7 settings UI:

    organization
      → provider configuration   (model_providers: purpose, provider, model, endpoint,
                                   enabled, approval)
      → deployment trust class    (recorded; must equal what the implementation declares)
      → secret reference          (provider_credentials: env:NAME, or vault:<id> for a
                                   value encrypted at rest; never the value itself)
      → invocation                (only through the invocation rule, per request)

Writes are administrative actions: each needs a stated reason and is recorded in the
append-only ``audit_events`` table with before/after state (never a secret value or
a secret reference's target). These are the **operator's** writers, run through the CLI
(``lattice provider ...``, ``lattice org egress``) as ``lattice_owner``; administrators
change the same tables through the product and the 0021 fenced functions
(``lattice.providers.admin``), where raising egress needs a fresh step-up (D-34).

* configuring a provider records it approved for ``internal`` only (the default of
  security-model §6) and, for generation, makes it the one enabled configuration; a new
  generation record is **explicitly** approved for ``internal`` by the operator's
  configuration (``approved_at``: generation needs an explicit approval since P12.5;
  records an administrator creates in the product start unapproved and disabled);
* approval, enablement, credentials and the organization's ceilings each change only
  what they name -- none of them can change a trust class;
* rotating a credential revokes the active reference and records a new one; revoking
  leaves none; an encrypted (``vault:``) value is destroyed with its credential. Either
  takes effect on the next request (nothing is cached);
* disabling keeps the record and its history; a disabled provider receives nothing.

``provider_overview`` is the masked read model: whether a credential is configured and
resolvable -- never its value, and not even its reference.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.providers.egress import DEPLOYMENTS, LEVELS_ORDER, trust_class
from lattice.providers.ollama_generation import validate_endpoint
from lattice.providers.secrets import SecretStore, SecretUnavailable, parse_reference
from lattice.vault.keys import Keyring
from lattice.vault.records import destroy_secret, load_sealed, store_secret

Purpose = Literal["embedding", "generation"]


class ConfigurationRejected(Exception):
    """A configuration change was refused. ``message`` is safe to print (fixed text,
    no secret)."""


async def _audit(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    actor: str,
    action: str,
    target: dict[str, Any],
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str,
) -> None:
    reason = " ".join(reason.split())
    if not reason:
        raise ConfigurationRejected("a reason is required")
    await session.execute(
        text(
            "INSERT INTO audit_events (organization_id, actor, action, target, before, after, "
            "reason) VALUES (:o, :actor, :action, CAST(:target AS jsonb), "
            "CAST(:before AS jsonb), CAST(:after AS jsonb), :reason)"
        ),
        {
            "o": org,
            "actor": actor,
            "action": action,
            "target": _json(target),
            "before": None if before is None else _json(before),
            "after": None if after is None else _json(after),
            "reason": reason[:500],
        },
    )


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, default=str)


_RECORD_COLUMNS = (
    "provider, model, deployment, approved_for_level, approved_at IS NOT NULL AS approved, "
    "enabled, endpoint, retention_mode, training_opt_out"
)


async def _record(
    session: AsyncSession, org: uuid.UUID, purpose: str, provider: str, model: str
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {_RECORD_COLUMNS} FROM model_providers WHERE organization_id = :o "  # noqa: S608
                    "AND purpose = :u AND provider = :p AND model = :m"
                ),
                {"o": org, "u": purpose, "p": provider, "m": model},
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


async def configure_provider(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    purpose: Purpose,
    provider: str,
    model: str,
    deployment: str,
    endpoint: str | None,
    retention_mode: str,
    training_opt_out: bool,
    reason: str,
    actor: str,
) -> None:
    """Record (or update) a provider configuration, approved for ``internal`` only. For
    generation it becomes the organization's one enabled configuration. An existing
    record keeps its approval and trust class: the class cannot be changed here."""
    if trust_class(deployment) == "unknown":
        raise ConfigurationRejected(f"deployment must be one of {', '.join(DEPLOYMENTS)}")
    if endpoint is not None:
        try:
            endpoint = validate_endpoint(endpoint)
        except ValueError as exc:
            raise ConfigurationRejected(str(exc)) from None
    before = await _record(session, org, purpose, provider, model)
    if before is not None and before["deployment"] != deployment:
        raise ConfigurationRejected(
            "the recorded trust class cannot be changed; disable this record and configure "
            "a new one"
        )
    if purpose == "generation":
        await session.execute(
            text(
                "UPDATE model_providers SET enabled = false, updated_at = now() "
                "WHERE organization_id = :o AND purpose = 'generation' AND enabled "
                "AND NOT (provider = :p AND model = :m)"
            ),
            {"o": org, "p": provider, "m": model},
        )
    await session.execute(
        text(
            "INSERT INTO model_providers (organization_id, purpose, provider, model, deployment, "
            "endpoint, retention_mode, training_opt_out, enabled, approved_at) VALUES (:o, :u, "
            ":p, :m, :d, :e, :r, :t, true, CASE WHEN :u = 'generation' THEN now() END) "
            "ON CONFLICT (organization_id, purpose, provider, model) DO UPDATE "
            "SET endpoint = EXCLUDED.endpoint, retention_mode = EXCLUDED.retention_mode, "
            "training_opt_out = EXCLUDED.training_opt_out, enabled = true, updated_at = now()"
        ),
        {
            "o": org,
            "u": purpose,
            "p": provider,
            "m": model,
            "d": deployment,
            "e": endpoint,
            "r": retention_mode,
            "t": training_opt_out,
        },
    )
    await _audit(
        session,
        org,
        actor=actor,
        action="provider.configured",
        target={"purpose": purpose, "provider": provider, "model": model},
        before=before,
        after=await _record(session, org, purpose, provider, model),
        reason=reason,
    )


async def set_enabled(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    purpose: Purpose,
    provider: str,
    model: str,
    enabled: bool,
    reason: str,
    actor: str,
) -> None:
    before = await _record(session, org, purpose, provider, model)
    if before is None:
        raise ConfigurationRejected("no such provider record for this organization")
    if enabled and purpose == "generation":
        await session.execute(
            text(
                "UPDATE model_providers SET enabled = false, updated_at = now() "
                "WHERE organization_id = :o AND purpose = 'generation' AND enabled"
            ),
            {"o": org},
        )
    await session.execute(
        text(
            "UPDATE model_providers SET enabled = :x, updated_at = now() WHERE organization_id "
            "= :o AND purpose = :u AND provider = :p AND model = :m"
        ),
        {"x": enabled, "o": org, "u": purpose, "p": provider, "m": model},
    )
    await _audit(
        session,
        org,
        actor=actor,
        action="provider.enabled" if enabled else "provider.disabled",
        target={"purpose": purpose, "provider": provider, "model": model},
        before=before,
        after=await _record(session, org, purpose, provider, model),
        reason=reason,
    )


async def approve(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    purpose: Purpose,
    provider: str,
    model: str,
    level: str,
    reason: str,
    actor: str,
) -> dict[str, Any]:
    """Set the approved level and ``approved_at`` (explicit approval). Never the class."""
    if level not in LEVELS_ORDER:
        raise ConfigurationRejected("unknown level")
    before = await _record(session, org, purpose, provider, model)
    if before is None:
        raise ConfigurationRejected("no such provider record for this organization")
    await session.execute(
        text(
            "UPDATE model_providers SET approved_for_level = :l, approved_at = now(), "
            "updated_at = now() WHERE organization_id = :o AND purpose = :u AND provider = :p "
            "AND model = :m"
        ),
        {"l": level, "o": org, "u": purpose, "p": provider, "m": model},
    )
    after = await _record(session, org, purpose, provider, model)
    await _audit(
        session,
        org,
        actor=actor,
        action="provider.approval_changed",
        target={"purpose": purpose, "provider": provider, "model": model},
        before=before,
        after=after,
        reason=reason,
    )
    assert after is not None  # noqa: S101 - updated above
    return after


async def _credential_state(
    session: AsyncSession, org: uuid.UUID, provider: str
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, data_use, split_part(secret_ref, ':', 1) AS scheme, created_at "
                    "FROM provider_credentials WHERE organization_id = :o AND provider = :p "
                    "AND status = 'active'"
                ),
                {"o": org, "p": provider},
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


async def _active_reference(session: AsyncSession, org: uuid.UUID, provider: str) -> str | None:
    ref: str | None = (
        await session.execute(
            text(
                "SELECT secret_ref FROM provider_credentials WHERE organization_id = :o "
                "AND provider = :p AND status = 'active'"
            ),
            {"o": org, "p": provider},
        )
    ).scalar_one_or_none()
    return ref


async def _revoke_active(session: AsyncSession, org: uuid.UUID, provider: str) -> None:
    """Revoke the active credential; a vault: one has its ciphertext destroyed (C3.3)."""
    await destroy_secret(session, org, await _active_reference(session, org, provider))
    await session.execute(
        text(
            "UPDATE provider_credentials SET status = 'revoked', revoked_at = now() "
            "WHERE organization_id = :o AND provider = :p AND status = 'active'"
        ),
        {"o": org, "p": provider},
    )


async def set_credential(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    provider: str,
    secret_ref: str | None = None,
    value: SecretStr | None = None,
    keyring: Keyring | None = None,
    data_use: Literal["unpaid", "paid"] | None,
    reason: str,
    actor: str,
) -> None:
    """Record a new credential for the organization's ``provider``, revoking the active
    one (rotation / replacement). Either a reference (``secret_ref``; the value is never
    read here) or a ``value`` sealed into ``encrypted_secrets`` with ``keyring`` and
    referenced as ``vault:<id>`` -- the value itself is never stored, logged or
    audited."""
    if (secret_ref is None) == (value is None):
        raise ConfigurationRejected("give either a secret reference or a value")
    if secret_ref is not None:
        try:
            scheme, _ = parse_reference(secret_ref)
        except SecretUnavailable:
            scheme = ""
        if scheme not in ("env", "memory"):
            raise ConfigurationRejected(
                "the reference must be env:LATTICE_PROVIDER_SECRET_<NAME> (or a documented "
                "provider variable)"
            )
    elif keyring is None or keyring.active is None:
        raise ConfigurationRejected(
            "storing a credential value needs LATTICE_SECRET_KEYS and LATTICE_SECRET_ACTIVE_KEY"
        )
    before = await _credential_state(session, org, provider)
    await _revoke_active(session, org, provider)
    credential_id = uuid.uuid4()
    if value is not None:
        assert keyring is not None  # noqa: S101 - checked above
        try:
            secret_ref = await store_secret(
                session,
                keyring,
                organization_id=org,
                owner_kind="provider_api_key",
                owner_id=credential_id,
                value=value,
            )
        except ValueError as exc:  # empty or too long; the message never holds the value
            raise ConfigurationRejected(str(exc)) from None
    await session.execute(
        text(
            "INSERT INTO provider_credentials (id, organization_id, provider, secret_ref, "
            "data_use) VALUES (:id, :o, :p, :r, :d)"
        ),
        {"id": credential_id, "o": org, "p": provider, "r": secret_ref, "d": data_use},
    )
    await _audit(
        session,
        org,
        actor=actor,
        action="credential.rotated" if before else "credential.set",
        target={"provider": provider},
        before=before,
        after=await _credential_state(session, org, provider),
        reason=reason,
    )


async def revoke_credential(
    session: AsyncSession, org: uuid.UUID, *, provider: str, reason: str, actor: str
) -> bool:
    before = await _credential_state(session, org, provider)
    if before is None:
        return False
    await _revoke_active(session, org, provider)
    await _audit(
        session,
        org,
        actor=actor,
        action="credential.revoked",
        target={"provider": provider},
        before=before,
        after=None,
        reason=reason,
    )
    return True


_SETTINGS = (
    "external_egress_level",
    "organization_controlled_level",
    "default_query_sensitivity",
    "data_origin",
)


async def _settings(session: AsyncSession, org: uuid.UUID) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(f"SELECT {', '.join(_SETTINGS)} FROM org_settings WHERE organization_id = :o"),  # noqa: S608
                {"o": org},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else {}


async def set_egress_levels(
    session: AsyncSession,
    org: uuid.UUID,
    *,
    external_level: str | None = None,
    organization_controlled_level: str | Literal["unset"] | None = "unset",
    reason: str,
    actor: str,
) -> dict[str, Any]:
    """Change the organization's ceilings (security-model §6). ``None`` for
    ``organization_controlled_level`` means the class is not allowed; ``"unset"`` leaves
    it unchanged. ``external_level``: a level, ``"none"`` (external APIs are not allowed,
    P12.5) or None (unchanged)."""
    changes: dict[str, str | None] = {}
    if external_level is not None:
        if external_level != "none" and external_level not in LEVELS_ORDER:
            raise ConfigurationRejected("unknown level")
        changes["external_egress_level"] = None if external_level == "none" else external_level
    if organization_controlled_level != "unset":
        if organization_controlled_level is not None and (
            organization_controlled_level not in LEVELS_ORDER
        ):
            raise ConfigurationRejected("unknown level")
        changes["organization_controlled_level"] = organization_controlled_level
    if not changes:
        raise ConfigurationRejected("nothing to change")
    before = await _settings(session, org)
    names = sorted(changes)
    await session.execute(
        text(
            f"INSERT INTO org_settings (organization_id, {', '.join(names)}) "  # noqa: S608
            f"VALUES (:o, {', '.join(':' + n for n in names)}) ON CONFLICT (organization_id) "
            f"DO UPDATE SET {', '.join(f'{n} = EXCLUDED.{n}' for n in names)}, updated_at = now()"
        ),
        {"o": org, **changes},
    )
    after = await _settings(session, org)
    await _audit(
        session,
        org,
        actor=actor,
        action="egress.levels_changed",
        target={"settings": names},
        before=before or None,
        after=after,
        reason=reason,
    )
    return after


# --- masked read model ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CredentialState:
    state: Literal["none", "active"]
    source: str | None  # the reference scheme ("env"), never the reference
    resolvable: bool
    data_use: str | None


@dataclass(frozen=True, slots=True)
class ProviderView:
    purpose: str
    provider: str
    model: str
    deployment: str
    approved_for_level: str
    explicitly_approved: bool
    enabled: bool
    endpoint_configured: bool
    retention_mode: str
    training_opt_out: bool
    credential: CredentialState


async def provider_overview(
    session: AsyncSession, org: uuid.UUID, secrets: SecretStore
) -> list[ProviderView]:
    rows = (
        await session.execute(
            text(
                f"SELECT purpose, {_RECORD_COLUMNS} FROM model_providers "  # noqa: S608
                "WHERE organization_id = :o ORDER BY purpose, provider, model"
            ),
            {"o": org},
        )
    ).mappings()
    credentials = {
        str(r["provider"]): r
        for r in (
            await session.execute(
                text(
                    "SELECT id, provider, secret_ref, data_use FROM provider_credentials "
                    "WHERE organization_id = :o AND status = 'active'"
                ),
                {"o": org},
            )
        ).mappings()
    }
    resolvable: dict[str, bool] = {}
    for provider, active in credentials.items():
        reference = str(active["secret_ref"])
        sealed = await load_sealed(
            session,
            reference,
            organization_id=org,
            owner_kind="provider_api_key",
            owner_id=active["id"],
        )
        resolvable[provider] = secrets.available(reference, sealed)
    out: list[ProviderView] = []
    for r in rows:
        credential = credentials.get(str(r["provider"]))
        out.append(
            ProviderView(
                purpose=str(r["purpose"]),
                provider=str(r["provider"]),
                model=str(r["model"]),
                deployment=str(r["deployment"]),
                approved_for_level=str(r["approved_for_level"]),
                explicitly_approved=bool(r["approved"]),
                enabled=bool(r["enabled"]),
                endpoint_configured=r["endpoint"] is not None,
                retention_mode=str(r["retention_mode"]),
                training_opt_out=bool(r["training_opt_out"]),
                credential=(
                    CredentialState("none", None, False, None)
                    if credential is None
                    else CredentialState(
                        "active",
                        str(credential["secret_ref"]).partition(":")[0],
                        resolvable[str(r["provider"])],
                        credential["data_use"],
                    )
                ),
            )
        )
    return out
