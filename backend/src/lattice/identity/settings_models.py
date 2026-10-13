"""The identity-administration contract (Phase 12, P12.4; phase-12-plan C5, §10): what an
administrator is shown and may send about the organization's OIDC connection and SCIM
tokens.

Never shown: a client secret or its reference (only whether one is set, how it is kept
and when it was set), a SCIM token after the response that created it, a token hash, JWKS
or discovery internals, resolved addresses. A test sign-in shows the administrator their
*own* identifier under the tested values, and nobody else's. Plaintext secrets arrive only
in request models typed ``SecretStr`` and leave only as ``revealed_token`` in the one
response that created it (``Cache-Control: no-store``).
"""

import re
import unicodedata
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from lattice.access.models import MAX_REASON

ConnectionStatus = Literal["active", "disabled"]
ClientSecretState = Literal["encrypted", "environment", "none"]
IssuerAccess = Literal["public", "operator_approved", "not_permitted"]
CheckResult = Literal[
    "valid", "invalid_configuration", "credentials_rejected", "provider_unavailable",
    "outbound_blocked",
]  # fmt: skip
CheckStep = Literal["issuer", "discovery", "keys", "client"]
TestOutcome = Literal[
    "matched",
    "mismatched",
    "not_provisioned",
    "sign_in_failed",
    "token_invalid",
    "provider_unavailable",
    "invalid_configuration",
    "outbound_blocked",
    "credentials_rejected",
]
SetupState = Literal["complete", "required", "optional", "in_progress", "attention", "blocked"]
MAX_ISSUER = 500
MAX_CLIENT_ID = 255
MAX_SECRET = 4096
_CLAIM = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_CONTROL = frozenset(chr(c) for c in (*range(32), 127))


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class _In(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _reason(value: str) -> str:
    if not value.strip():
        raise ValueError("reason must not be blank")
    return value


def normalise_issuer(value: str) -> str:
    """An issuer exactly as the provider names itself (its discovery document's
    ``issuer`` must equal it): trimmed only; no control characters, query or fragment."""
    issuer = value.strip()
    if not 1 <= len(issuer) <= MAX_ISSUER or any(c in _CONTROL for c in issuer):
        raise ValueError(f"issuer must be 1-{MAX_ISSUER} characters")
    if not issuer.startswith(("https://", "http://")) or any(c in issuer for c in "?#@ "):
        raise ValueError("issuer must be an https:// URL without credentials, query or fragment")
    return issuer


def normalise_client_id(value: str) -> str:
    client_id = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(client_id) <= MAX_CLIENT_ID or any(c in _CONTROL for c in client_id):
        raise ValueError(f"client_id must be 1-{MAX_CLIENT_ID} characters")
    return client_id


def normalise_claim(value: str) -> str:
    claim = value.strip()
    if not _CLAIM.fullmatch(claim):
        raise ValueError("subject_claim must be a claim name (letters, digits, underscore)")
    return claim


# --- read models ----------------------------------------------------------------------------


class IdentitySetupStatus(_Out):
    """The setup record's category for this page (C12): derived by the API."""

    state: SetupState
    reason: str
    message: str


class ClientSecretInfo(_Out):
    state: ClientSecretState = Field(
        description="encrypted (set in the product, kept as ciphertext), environment (an "
        "operator's deployment reference) or none (a public client: PKCE only)."
    )
    set_at: datetime | None = Field(description="When the encrypted secret was set.")


class LastTest(_Out):
    kind: Literal["check", "sign_in"]
    result: str
    at: datetime


class LastFailure(_Out):
    code: str
    at: datetime


class OidcConnectionOut(_Out):
    id: uuid.UUID
    status: ConnectionStatus
    issuer: str
    client_id: str
    subject_claim: str
    client_secret: ClientSecretInfo
    issuer_access: IssuerAccess = Field(
        description="public (https, port 443, checked against public addresses at each "
        "connection), operator_approved (a private target your Lattice operator registered "
        "for this organization) or not_permitted (the outbound policy refuses it)."
    )
    sign_in_url: str
    redirect_uri: str
    last_success_at: datetime | None
    last_failure: LastFailure | None
    last_test: LastTest | None
    members_bound: int = Field(description="Members (not removed) this connection signs in.")
    live_sessions: int = Field(description="Sessions this connection issued that are live.")
    version: datetime


class ScimTokenOut(_Out):
    id: uuid.UUID
    hint: str | None = Field(description="The token's last four characters (newer tokens).")
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None = Field(description="Minute precision.")
    current_connection: bool = Field(
        description="Issued for the active connection (only those authenticate)."
    )


class ScimSettings(_Out):
    base_url: str
    identifying_claim: str | None = Field(
        description="externalId must equal this claim of the same person."
    )
    tokens: list[ScimTokenOut]
    provisioned_members: int
    refused_recently: int = Field(description="Refused provisioning requests, last 24 hours.")
    status: IdentitySetupStatus


class IdentitySettings(_Out):
    oidc: OidcConnectionOut | None = Field(
        description="The active connection, else the most recent disabled one, else none."
    )
    can_add_connection: bool = Field(
        description="No active connection and no disabled one that still signs members in."
    )
    sign_in_status: IdentitySetupStatus
    scim: ScimSettings


class StatusPreview(_Out):
    enabled: bool
    sessions_ending: int
    members_bound: int
    ends_own_session: bool = Field(
        default=False, description="Your own session came through this connection."
    )


class IdentityProof(_Out):
    test_id: uuid.UUID
    completed_at: datetime
    expires_at: datetime


class IdentityChangePreview(_Out):
    issuer: str
    subject_claim: str
    issuer_changed: bool
    claim_changed: bool
    issuer_access: IssuerAccess
    sessions_ending: int
    members_bound: int
    ends_own_session: bool = Field(
        default=False, description="Your own session came through this connection."
    )
    proof: IdentityProof | None = Field(
        description="Your matched test sign-in under exactly these values, unused and "
        "unexpired: the change needs one."
    )


class CheckOut(_Out):
    result: CheckResult
    step: CheckStep
    checked_at: datetime


class TestSignInStarted(_Out):
    test_id: uuid.UUID
    authorization_url: str


class TestSignInOut(_Out):
    test_id: uuid.UUID
    status: Literal["pending", "completed", "expired"]
    outcome: TestOutcome | None
    identifier: str | None = Field(
        description="Your own identifier under the tested claim (matched or mismatched)."
    )
    issuer: str
    subject_claim: str
    proposed: bool
    completed_at: datetime | None
    expires_at: datetime
    proof_available: bool = Field(
        description="matched under proposed values, unused and unexpired: usable once to "
        "apply exactly that change."
    )


class RevealedScimToken(_Out):
    token: ScimTokenOut
    revealed_token: str = Field(
        description="The bearer token. Shown in this response only: Lattice keeps a hash."
    )


class RevokePreview(_Out):
    last_live_token: bool
    provisioned_members: int


# --- requests --------------------------------------------------------------------------------


class _Reasoned(_In):
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    _check_reason = field_validator("reason")(_reason)


class _Versioned(_Reasoned):
    version: datetime


class NewConnectionRequest(_Reasoned):
    issuer: str
    client_id: str
    subject_claim: str = "sub"
    client_secret: SecretStr | None = Field(
        default=None,
        description="Omit for a public client (PKCE only). Encrypted; never returned.",
    )

    _issuer = field_validator("issuer")(normalise_issuer)
    _client = field_validator("client_id")(normalise_client_id)
    _claim = field_validator("subject_claim")(normalise_claim)

    @field_validator("client_secret")
    @classmethod
    def _secret(cls, value: SecretStr | None) -> SecretStr | None:
        return _secret_value(value)


def _secret_value(value: SecretStr | None) -> SecretStr | None:
    if value is None:
        return None
    raw = value.get_secret_value()
    if not raw.strip() or len(raw) > MAX_SECRET or any(c in _CONTROL for c in raw):
        # The message names the field only (never the value).
        raise ValueError("client_secret must be 1-4096 printable characters")
    return value


class ClientIdRequest(_Versioned):
    client_id: str

    _client = field_validator("client_id")(normalise_client_id)


class ClientSecretRequest(_Versioned):
    client_secret: SecretStr = Field(description="Encrypted at once; never returned.")

    @field_validator("client_secret")
    @classmethod
    def _secret(cls, value: SecretStr) -> SecretStr:
        found = _secret_value(value)
        assert found is not None  # noqa: S101 - required field
        return found


class RemoveSecretRequest(_Versioned):
    pass


class StatusRequest(_Versioned):
    enabled: bool


class StatusPreviewRequest(_In):
    enabled: bool


class IdentityChangePreviewRequest(_In):
    issuer: str | None = None
    subject_claim: str | None = None

    _issuer = field_validator("issuer")(lambda v: None if v is None else normalise_issuer(v))
    _claim = field_validator("subject_claim")(lambda v: None if v is None else normalise_claim(v))


class IdentityChangeRequest(_Versioned):
    issuer: str
    subject_claim: str
    proof_id: uuid.UUID
    confirmation: str = Field(
        description="Typed by the administrator: the new issuer when it changes, else the "
        "new claim name."
    )

    _issuer = field_validator("issuer")(normalise_issuer)
    _claim = field_validator("subject_claim")(normalise_claim)


class TestSignInRequest(_In):
    issuer: str | None = None
    subject_claim: str | None = None

    _issuer = field_validator("issuer")(lambda v: None if v is None else normalise_issuer(v))
    _claim = field_validator("subject_claim")(lambda v: None if v is None else normalise_claim(v))


class TokenRequest(_Reasoned):
    pass
