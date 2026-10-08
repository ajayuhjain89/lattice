"""``lattice org create``: the operator creates a tenant's trust root (D-28;
phase-12-plan C1; runbook §1).

One transaction, as ``lattice_owner`` through row-level security, creates:

* the organization (``active``) and its settings at the safe defaults (external egress
  ceiling ``internal``, organization-controlled ceiling unset, default question
  sensitivity ``internal``, ``data_origin = customer``);
* its OIDC connection (a new opaque login key) -- the client secret as an ``env:``
  reference, as ciphertext sealed for this connection (``--client-secret-stdin``), or
  none (a public PKCE client);
* the issuer as a registered private target when the operator asks for it (the issuer
  otherwise must pass the public outbound policy, C4);
* a **new** user owned by the organization, an active membership with the administrator
  capability and least access otherwise (clearance ``internal``, scope ``assigned``,
  nothing assigned), and the directory identity ``(connection, identifier)`` where the
  identifier is the exact value of the connection's identifying claim for that person
  (NFC-normalised and trimmed, case preserved);
* ``organization.created``, ``sso.connection_created``, ``access.member_changed`` (and
  ``outbound.target_added``) audit events, actor ``operator:<user>``.

Nothing is linked by e-mail and nothing is created at sign-in: the first administrator's
first sign-in through the connection maps to the directory identity created here and
marks the connection verified. An e-mail that exists in another organization is
irrelevant (users are organization-owned); an identity provider client (issuer and
client ID) already registered to another organization is refused -- each organization
needs its own client, so a token minted for one tenant's client never fits another's.

**Retry.** The slug is the idempotency key, under an advisory lock. An identical retry
writes nothing and reports the organization as it is; any difference is refused, naming
the differing fields, and changes nothing (later changes go through the product or
``sso update``). Any failure rolls the whole transaction back: there is never a
half-created organization.
"""

import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Final

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lattice.access.audit import MEMBER_CHANGED, record_event
from lattice.access.directory import access_snapshot
from lattice.db.engine import Database, bind_organization
from lattice.orgs.outbound_targets import TargetRejected, add_target, registration_target
from lattice.outbound import OutboundPolicy, OutboundRefused, Resolver, system_resolver
from lattice.providers.secrets import EnvironmentSecretStore, SecretUnavailable
from lattice.vault.keys import Keyring
from lattice.vault.records import load_sealed, store_secret

SLUG: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
CLAIM: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
OIDC_SECRET_REF: Final = re.compile(r"env:LATTICE_OIDC_SECRET_[A-Z0-9_]{1,64}")
EMAIL: Final = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}")
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
DEFAULT_SETTINGS: Final = {
    "external_egress_level": "internal",
    "organization_controlled_level": None,
    "default_query_sensitivity": "internal",
    "data_origin": "customer",
}


class BootstrapRejected(Exception):
    """A refused ``org create``; the message is safe to print (never a secret)."""


@dataclass(frozen=True)
class OrgRequest:
    slug: str
    name: str
    issuer: str
    client_id: str
    subject_claim: str
    admin_email: str
    admin_identifier: str
    admin_name: str
    reason: str
    client_secret_ref: str | None = None
    client_secret: SecretStr | None = None
    register_private_issuer: bool = False

    def __repr__(self) -> str:  # never the secret, even masked
        return f"OrgRequest(slug={self.slug!r})"


@dataclass(frozen=True)
class OrgCreated:
    organization_id: uuid.UUID
    slug: str
    name: str
    connection_id: uuid.UUID
    login_key: str
    issuer: str
    client_id: str
    subject_claim: str
    client_secret: str  # "encrypted", "reference" or "none" -- a label
    admin_membership_id: uuid.UUID
    admin_email: str
    created: bool  # False: an identical retry, nothing written


def _text(value: str, field: str, *, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit or _CONTROL.search(cleaned):
        raise BootstrapRejected(f"{field} must be 1-{limit} characters without control characters")
    return cleaned


def normalise_identifier(value: str) -> str:
    """The exact identifying-claim value: NFC-normalised, trimmed, case preserved."""
    return _text(unicodedata.normalize("NFC", value), "--first-admin-identifier", limit=255)


def validate(request: OrgRequest, keyring: Keyring) -> OrgRequest:
    """Every check that needs no network or database. Returns the normalised request."""
    if not SLUG.fullmatch(request.slug):
        raise BootstrapRejected(
            "--slug must be lower-case letters, digits and '-' (at most 63, starting with a "
            "letter or digit)"
        )
    if not CLAIM.fullmatch(request.subject_claim):
        raise BootstrapRejected("--subject-claim must be a claim name such as sub or email")
    email = request.admin_email.strip()
    if not EMAIL.fullmatch(email) or len(email) > 254:
        raise BootstrapRejected("--first-admin-email must be an e-mail address")
    if request.client_secret_ref is not None and request.client_secret is not None:
        raise BootstrapRejected("give --client-secret-ref or --client-secret-stdin, not both")
    if request.client_secret_ref is not None and not OIDC_SECRET_REF.fullmatch(
        request.client_secret_ref
    ):
        raise BootstrapRejected(
            "--client-secret-ref must be env:LATTICE_OIDC_SECRET_<NAME> (or use "
            "--client-secret-stdin to store it encrypted)"
        )
    if request.client_secret is not None and keyring.active is None:
        raise BootstrapRejected(
            "--client-secret-stdin needs LATTICE_SECRET_KEYS and LATTICE_SECRET_ACTIVE_KEY in "
            "this process's environment (the same keys as the API)"
        )
    if not request.issuer.startswith("https://") or len(request.issuer) > 500:
        raise BootstrapRejected("--issuer must be an https:// URL (at most 500 characters)")
    if not " ".join(request.reason.split()):
        raise BootstrapRejected("--reason is required")
    return OrgRequest(
        slug=request.slug,
        name=_text(request.name, "--name", limit=200),
        issuer=request.issuer,
        client_id=_text(request.client_id, "--client-id", limit=255),
        subject_claim=request.subject_claim,
        admin_email=email,
        admin_identifier=normalise_identifier(request.admin_identifier),
        admin_name=_text(request.admin_name or email, "--first-admin-name", limit=200),
        reason=request.reason,
        client_secret_ref=request.client_secret_ref,
        client_secret=request.client_secret,
        register_private_issuer=request.register_private_issuer,
    )


async def check_issuer(request: OrgRequest, resolver: Resolver = system_resolver) -> None:
    """The outbound policy (C4) for the issuer: public, or registered with this request."""
    try:
        target = registration_target(request.issuer)
    except TargetRejected as exc:
        raise BootstrapRejected(f"--issuer: {exc}") from None
    registered = frozenset({target}) if request.register_private_issuer else frozenset()
    policy = OutboundPolicy(registered, resolver=resolver)
    try:
        await policy.check_target(request.issuer)
    except OutboundRefused as refused:
        if refused.code == "target_not_public":
            raise BootstrapRejected(
                "the issuer resolves to an address that is not public (target_not_public); "
                "if it is the organization's own identity provider on a private network, "
                "register it with --register-private-issuer"
            ) from None
        raise BootstrapRejected(f"the issuer is not reachable as given ({refused.code})") from None


_EXISTING_SQL = text(
    "SELECT o.id, o.name, c.id AS connection_id, c.login_key, c.issuer, c.client_id, "
    "c.client_secret_ref, c.subject_claim FROM organizations o "
    "LEFT JOIN oidc_connections c ON c.organization_id = o.id AND c.status = 'active' "
    "WHERE o.slug = :s"
)
_ADMIN_SQL = text(
    "SELECT m.id, m.status, m.is_admin, u.email, u.display_name FROM directory_identities d "
    "JOIN memberships m ON m.organization_id = d.organization_id AND m.id = d.membership_id "
    "JOIN users u ON u.id = m.user_id "
    "WHERE d.organization_id = :o AND d.connection_id = :c AND d.external_id = :x"
)


def _secret_label(reference: str | None) -> str:
    if reference is None:
        return "none"
    return "encrypted" if reference.startswith("vault:") else "reference"


async def _differences(
    session: AsyncSession, request: OrgRequest, existing: Any, keyring: Keyring
) -> tuple[list[str], uuid.UUID | None]:
    """Fields of ``request`` that differ from the existing organization."""
    differs: list[str] = []
    if existing.name != request.name:
        differs.append("name")
    if existing.connection_id is None:
        return [*differs, "OIDC connection (none active)"], None
    for field, column in (
        ("issuer", "issuer"),
        ("client ID", "client_id"),
        ("identifying claim", "subject_claim"),
    ):
        if getattr(existing, column) != getattr(request, column):
            differs.append(field)
    stored = existing.client_secret_ref
    if request.client_secret is not None:
        same = False
        if stored is not None and stored.startswith("vault:"):
            sealed = await load_sealed(
                session,
                stored,
                organization_id=existing.id,
                owner_kind="oidc_client_secret",
                owner_id=existing.connection_id,
            )
            try:
                current = EnvironmentSecretStore(environ={}, keyring=keyring).resolve(
                    stored, sealed
                )
                same = secrets.compare_digest(
                    current.get_secret_value().encode(),
                    request.client_secret.get_secret_value().strip().encode(),
                )
            except SecretUnavailable:
                same = False
        if not same:
            differs.append("client secret")
    elif stored != request.client_secret_ref:
        differs.append("client secret")
    if request.register_private_issuer:
        live = await session.execute(
            text(
                "SELECT 1 FROM outbound_targets WHERE organization_id = :o AND purpose = 'oidc' "
                "AND scheme = :s AND host = :h AND port = :p AND revoked_at IS NULL"
            ),
            {"o": existing.id, **_target_params(request.issuer)},
        )
        if live.first() is None:
            differs.append("registered private issuer")
    admin = (
        await session.execute(
            _ADMIN_SQL,
            {"o": existing.id, "c": existing.connection_id, "x": request.admin_identifier},
        )
    ).one_or_none()
    if admin is None:
        differs.append("first administrator identifier")
        return differs, None
    if str(admin.email).lower() != request.admin_email.lower():
        differs.append("first administrator e-mail")
    if admin.display_name != request.admin_name:
        differs.append("first administrator name")
    if not (admin.is_admin and admin.status == "active"):
        differs.append("first administrator (no longer an active administrator)")
    return differs, admin.id


def _target_params(issuer: str) -> dict[str, Any]:
    target = registration_target(issuer)
    return {"s": target.scheme, "h": target.host, "p": target.port}


async def _client_in_use(session: AsyncSession, issuer: str, client_id: str) -> bool:
    """Whether any other organization has a connection (any status) with this issuer
    and client ID. The owner reads tenant rows only with an organization bound, so each
    is bound in turn (operator command, small N)."""
    orgs: list[uuid.UUID] = list(
        (await session.execute(text("SELECT id FROM organizations"))).scalars().all()
    )
    for org in orgs:
        await bind_organization(session, org)
        found = await session.execute(
            text(
                "SELECT 1 FROM oidc_connections WHERE organization_id = :o AND issuer = :i "
                "AND client_id = :c"
            ),
            {"o": org, "i": issuer, "c": client_id},
        )
        if found.first() is not None:
            return True
    return False


async def create_organization(
    db: Database,
    request: OrgRequest,
    *,
    keyring: Keyring,
    actor: str,
    resolver: Resolver = system_resolver,
) -> OrgCreated:
    request = validate(request, keyring)
    await check_issuer(request, resolver)
    async with db.unscoped_transaction() as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"lattice-org-create:{request.slug}"},
        )
        existing_id = (
            await session.execute(
                text("SELECT id FROM organizations WHERE slug = :s"), {"s": request.slug}
            )
        ).scalar_one_or_none()
        if existing_id is not None:
            await bind_organization(session, existing_id)
            existing = (await session.execute(_EXISTING_SQL, {"s": request.slug})).one()
            differs, admin_id = await _differences(session, request, existing, keyring)
            if differs:
                raise BootstrapRejected(
                    f"organization {request.slug!r} already exists and differs: "
                    f"{', '.join(differs)}. Nothing was changed"
                )
            assert admin_id is not None  # noqa: S101 - no differences means it was found
            return OrgCreated(
                organization_id=existing.id,
                slug=request.slug,
                name=existing.name,
                connection_id=existing.connection_id,
                login_key=existing.login_key,
                issuer=existing.issuer,
                client_id=existing.client_id,
                subject_claim=existing.subject_claim,
                client_secret=_secret_label(existing.client_secret_ref),
                admin_membership_id=admin_id,
                admin_email=request.admin_email,
                created=False,
            )
        if await _client_in_use(session, request.issuer, request.client_id):
            raise BootstrapRejected(
                "this identity provider client (issuer and client ID) is registered to "
                "another organization; create a separate client (application) for this "
                "organization in the identity provider"
            )
        try:
            return await _create(session, request, keyring=keyring, actor=actor)
        except DBAPIError as exc:  # a CHECK or unique constraint, reported without values
            raise BootstrapRejected(
                "the database refused the organization (a value has an invalid form or "
                "already exists); nothing was created"
            ) from exc


async def _create(
    session: AsyncSession, request: OrgRequest, *, keyring: Keyring, actor: str
) -> OrgCreated:
    org = uuid.uuid4()
    await bind_organization(session, org)
    await session.execute(
        text("INSERT INTO organizations (id, slug, name) VALUES (:o, :s, :n)"),
        {"o": org, "s": request.slug, "n": request.name},
    )
    await session.execute(
        text(
            "INSERT INTO org_settings (organization_id, external_egress_level, "
            "organization_controlled_level, default_query_sensitivity, data_origin) "
            "VALUES (:o, :e, :c, :d, :origin)"
        ),
        {
            "o": org,
            "e": DEFAULT_SETTINGS["external_egress_level"],
            "c": DEFAULT_SETTINGS["organization_controlled_level"],
            "d": DEFAULT_SETTINGS["default_query_sensitivity"],
            "origin": DEFAULT_SETTINGS["data_origin"],
        },
    )
    await record_event(
        session,
        org,
        actor=actor,
        action="organization.created",
        target={"organization_id": str(org)},
        before=None,
        after={"slug": request.slug, "name": request.name, **DEFAULT_SETTINGS},
        reason=request.reason,
    )
    if request.register_private_issuer:
        await add_target(
            session,
            org,
            target=registration_target(request.issuer),
            purpose="oidc",
            reason=request.reason,
            actor=actor,
        )

    connection_id = uuid.uuid4()
    reference = request.client_secret_ref
    if request.client_secret is not None:
        reference = await store_secret(
            session,
            keyring,
            organization_id=org,
            owner_kind="oidc_client_secret",
            owner_id=connection_id,
            value=request.client_secret,
        )
    login_key = secrets.token_urlsafe(24)
    await session.execute(
        text(
            "INSERT INTO oidc_connections (id, organization_id, login_key, issuer, client_id, "
            "client_secret_ref, subject_claim) VALUES (:id, :o, :k, :i, :c, :s, :claim)"
        ),
        {
            "id": connection_id,
            "o": org,
            "k": login_key,
            "i": request.issuer,
            "c": request.client_id,
            "s": reference,
            "claim": request.subject_claim,
        },
    )
    await record_event(
        session,
        org,
        actor=actor,
        action="sso.connection_created",
        target={"connection_id": str(connection_id)},
        before=None,
        after={
            "issuer": request.issuer,
            "client_id": request.client_id,
            "subject_claim": request.subject_claim,
            "client_secret": _secret_label(reference),
        },
        reason=request.reason,
    )

    user_id: uuid.UUID = (
        await session.execute(
            text(
                "INSERT INTO users (email, display_name, home_organization_id) "
                "VALUES (:e, :n, :o) RETURNING id"
            ),
            {"e": request.admin_email, "n": request.admin_name, "o": org},
        )
    ).scalar_one()
    membership_id: uuid.UUID = (
        await session.execute(
            text(
                "INSERT INTO memberships (organization_id, user_id, status, is_admin, "
                "clearance, account_scope) VALUES (:o, :u, 'active', true, 'internal', "
                "'assigned') RETURNING id"
            ),
            {"o": org, "u": user_id},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO directory_identities (organization_id, connection_id, membership_id, "
            "external_id, user_name) VALUES (:o, :c, :m, :x, :n)"
        ),
        {
            "o": org,
            "c": connection_id,
            "m": membership_id,
            "x": request.admin_identifier,
            "n": request.admin_email,
        },
    )
    snapshot = await access_snapshot(session, org, membership_id)
    assert snapshot is not None  # noqa: S101 - inserted above
    await record_event(
        session,
        org,
        actor=actor,
        action=MEMBER_CHANGED,
        target={"membership_id": str(membership_id)},
        before=None,
        after=snapshot[0].as_json(),
        reason=request.reason,
    )
    return OrgCreated(
        organization_id=org,
        slug=request.slug,
        name=request.name,
        connection_id=connection_id,
        login_key=login_key,
        issuer=request.issuer,
        client_id=request.client_id,
        subject_claim=request.subject_claim,
        client_secret=_secret_label(reference),
        admin_membership_id=membership_id,
        admin_email=request.admin_email,
        created=True,
    )
