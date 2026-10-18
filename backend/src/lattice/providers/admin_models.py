"""The provider-administration contract (Phase 12, P12.5; phase-12-plan C3, C4, C6, §11):
what an administrator is shown and may send about the organization's AI providers,
credentials and egress policy.

Seven facts stay separate (C6.1), in this contract as in the page: whether a credential
exists, whether its test passed, what the record is approved for, its trust class, the
organization's ceiling for that class, the question sensitivity, and -- only at call time
-- each evidence item's authorization. ``lattice`` is the server's reading of what may
leave Lattice; the page renders it and never computes a cell.

Never shown: an API key (only whether one is set, who manages it, its last four
characters when it was entered in the product, when it was set and its last test), a
secret reference, ciphertext, a key ID, a resolved address, a provider's response. Keys
arrive only in ``CredentialRequest.api_key`` (``SecretStr``); validation errors name the
field, never the value.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from lattice.access.models import MAX_REASON

Level = Literal["internal", "confidential", "restricted"]
SetupState = Literal["complete", "required", "optional", "in_progress", "attention", "blocked"]
TestResult = Literal[
    "valid",
    "invalid_credential",
    "configuration_invalid",
    "provider_unavailable",
    "outbound_blocked",
    "credential_missing",
    "unsupported",
]
StepUpOutcome = Literal[
    "pending",
    "granted",
    "not_fresh",
    "session_mismatch",
    "assurance_insufficient",
    "step_up_invalid",
    "mismatched",
    "not_provisioned",
    "sign_in_failed",
    "token_invalid",
    "provider_unavailable",
    "invalid_configuration",
    "outbound_blocked",
    "credentials_rejected",
]
MAX_KEY = 4096
_CONTROL = frozenset(chr(c) for c in (*range(32), 127))


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class _In(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _reason(value: str) -> str:
    if not value.strip():
        raise ValueError("reason must not be blank")
    return value


# --- read model ------------------------------------------------------------------------------


class EgressSettingsOut(_Out):
    external_egress_level: Level | None = Field(
        description="The ceiling for external APIs; null: external APIs are not allowed."
    )
    organization_controlled_level: Level | None = Field(
        description="The ceiling for organization-controlled servers; null: not allowed."
    )
    default_query_sensitivity: Level = Field(
        description="D-13: a question is at least this sensitive; askers can only raise it."
    )
    data_origin: str = Field(description="customer or synthetic (operator-managed).")
    version: int = Field(
        description="The policy version: every change (by anyone) raises it. Previews, "
        "changes and step-ups name the version they were computed on."
    )


class CredentialOut(_Out):
    state: Literal["none", "active"]
    id: uuid.UUID | None = Field(
        description="The active credential's ID (send it back as `expected_credential_id`)."
    )
    source: str | None = Field(
        description="How the value is kept: vault (entered in the product, encrypted), env "
        "(an operator's deployment variable), memory (tests). Never the reference."
    )
    managed_by: Literal["organization", "operator"] | None
    resolvable: bool = Field(description="Whether Lattice can open it now (never the value).")
    data_use: str | None = Field(description="The declared tier: paid or unpaid.")
    hint: str | None = Field(description="The last four characters, for keys entered here.")
    set_at: datetime | None


class LastTestOut(_Out):
    at: datetime
    result: TestResult
    current: bool = Field(
        description="Whether the test used the credential that is active now (false after "
        "a replacement: test again)."
    )


class DestinationOut(_Out):
    kind: Literal["vendor_api", "registered_target", "deployment_local", "test_double"]
    origin: str | None = Field(description="The endpoint (never resolved addresses).")
    access: Literal["public", "operator_approved", "not_permitted", "deployment_local"]


class ProviderOut(_Out):
    id: uuid.UUID
    purpose: str
    provider: str
    model: str
    deployment: str = Field(description="The D-15 trust class, as recorded.")
    approved_for_level: str
    explicitly_approved: bool
    enabled: bool
    endpoint_configured: bool
    retention_mode: str
    training_opt_out: bool
    credential: CredentialOut
    managed_by: Literal["organization", "deployment"] = Field(
        description="deployment: the embedding provider your operator runs for every "
        "organization (only its approval for this organization is yours to change)."
    )
    destination: DestinationOut
    requires_credential: bool
    allowed_up_to: Level | None = Field(
        description="The most sensitive level this record may receive under the policy now; "
        "null: nothing (see `blocked_by`)."
    )
    blocked_by: str | None = Field(description="Why nothing may be sent (a fixed code).")
    last_test: LastTestOut | None


class CellOut(_Out):
    provider_id: uuid.UUID
    level: Level
    permitted: bool
    reason: str = Field(description="permitted, or why not (a fixed code).")


class TargetOut(_Out):
    """An outbound target your operator registered for this organization's generation
    servers (C4): the only endpoints an Ollama provider may use."""

    id: uuid.UUID
    origin: str
    trust_class: Literal["local", "organization_controlled"]
    usable: bool = Field(description="False for plain http on a non-loopback host.")


class SupportedProviderOut(_Out):
    provider: Literal["gemini", "ollama", "fake"]
    trust_class: str
    requires_credential: bool
    models: list[str] = Field(description="Fixed models; empty: any model name the server has.")
    needs_target: bool


class StepUpAvailability(_Out):
    available: bool
    reason: Literal["available", "no_connection", "no_identity"] = Field(
        description="no_connection: the organization signs in without OIDC; no_identity: "
        "you have no directory identity on its connection. Raising egress needs a fresh "
        "sign-in at the identity provider: without one it is refused (an operator may "
        "still change it)."
    )
    assurance_required: list[str] | None = Field(
        description="acr values your operator requires the identity provider to assert; "
        "null: a fresh authentication is required, not a particular method."
    )
    max_age_seconds: int = 300


class AiSetupStatus(_Out):
    state: SetupState
    reason: str
    message: str


class ProviderSettings(_Out):
    egress: EgressSettingsOut
    providers: list[ProviderOut]
    lattice: list[CellOut]
    targets: list[TargetOut]
    supported: list[SupportedProviderOut]
    step_up: StepUpAvailability
    confirmation: str = Field(
        description="What to type to confirm an escalation: the organization's name."
    )
    status: AiSetupStatus


# --- changes ---------------------------------------------------------------------------------


class SettingsChange(_In):
    """Set the named fields of the organization's egress policy (omitted ones stay)."""

    kind: Literal["settings"]
    external_egress_level: Level | None = None
    organization_controlled_level: Level | None = None
    default_query_sensitivity: Level | None = None


class ProviderChange(_In):
    """Set the named facts of one provider record (omitted ones stay). `approval`: a level
    approves explicitly at that level; null withdraws the approval."""

    kind: Literal["provider"]
    provider_id: uuid.UUID
    enabled: bool | None = None
    approval: Level | None = None
    training_opt_out: bool | None = None


Change = Annotated[SettingsChange | ProviderChange, Field(discriminator="kind")]


def change_document(change: SettingsChange | ProviderChange) -> dict[str, Any]:
    """The change exactly as the database applies it: only the fields that were sent
    (``null`` included, where it means "not allowed" or "withdraw")."""
    document = change.model_dump(mode="json", exclude_unset=True)
    document["kind"] = change.kind
    if (
        isinstance(change, SettingsChange)
        and document.get("default_query_sensitivity", "x") is None
    ):
        raise ValueError("default_query_sensitivity cannot be null")
    if isinstance(change, ProviderChange):
        for name in ("enabled", "training_opt_out"):
            if name in document and document[name] is None:
                raise ValueError(f"{name} cannot be null")
    return document


class ChangePreviewRequest(_In):
    change: Change


class ChangeRequest(_In):
    change: Change
    policy_version: int
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    _reason = field_validator("reason")(_reason)


class StepUpRequest(ChangeRequest):
    confirmation: str = Field(
        max_length=300, description="The organization's name, typed (`confirmation` of the page)."
    )


class Transition(_Out):
    provider_id: uuid.UUID
    provider: str
    model: str
    purpose: str
    trust_class: str
    destination: str | None
    level: Level
    before: bool
    after: bool


class ChangePreview(_Out):
    policy_version: int
    escalation: bool = Field(
        description="The database's reading: some level newly reaches some provider, or the "
        "questions' default level is lowered. Committing it needs a fresh step-up."
    )
    newly_allowed: list[Transition]
    no_longer_allowed: list[Transition]
    default_query_sensitivity: dict[str, Level]
    remaining: list[str] = Field(
        description="Restrictions that stay in force whatever this change does (fixed codes)."
    )
    affected_provider_ids: list[uuid.UUID]


class StepUpStarted(_Out):
    step_up_id: uuid.UUID
    authorization_url: str


class StepUpGrantOut(_Out):
    id: uuid.UUID
    expires_at: datetime
    auth_time: datetime
    assurance: str | None = Field(
        description="The acr the identity provider asserted when your operator requires "
        "one; null otherwise. A fresh sign-in is not by itself multi-factor authentication."
    )
    consumed: bool


class StepUpOut(_Out):
    id: uuid.UUID
    outcome: StepUpOutcome
    created_at: datetime
    change: dict[str, Any]
    reason: str
    policy_version: int
    current_version: int
    grant: StepUpGrantOut | None
    preview: ChangePreview | None = Field(
        description="Now -> After recomputed from the current policy (null when it can no "
        "longer apply: the change is no longer valid)."
    )


# --- providers and credentials -------------------------------------------------------------


class NewProviderRequest(_In):
    provider: Literal["gemini", "ollama", "fake"]
    model: str = Field(min_length=1, max_length=100)
    target_id: uuid.UUID | None = Field(
        default=None, description="ollama: a registered target (`targets`)."
    )
    training_opt_out: bool | None = Field(
        default=None,
        description="gemini: whether the provider's terms for this key's tier exclude "
        "training on submitted content (a paid tier). Ignored for the others.",
    )
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    _reason = field_validator("reason")(_reason)


class EndpointRequest(_In):
    target_id: uuid.UUID
    policy_version: int
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    _reason = field_validator("reason")(_reason)


class CredentialRequest(_In):
    api_key: SecretStr = Field(description="Encrypted at once; never returned.")
    data_use: Literal["paid", "unpaid"] = Field(
        description="paid: the provider does not use submitted content; unpaid: it may (then "
        "only synthetic organizations' content is ever sent)."
    )
    expected_credential_id: uuid.UUID | None = Field(
        description="The active credential you are replacing (null when there is none)."
    )
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    _reason = field_validator("reason")(_reason)

    @field_validator("api_key")
    @classmethod
    def _key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.strip() or len(raw) > MAX_KEY or any(c in _CONTROL for c in raw):
            # The message names the field only (never the value).
            raise ValueError("api_key must be 1-4096 printable characters")
        return SecretStr(raw.strip())


class RevokeCredentialRequest(_In):
    expected_credential_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    _reason = field_validator("reason")(_reason)


class TestOut(_Out):
    provider_id: uuid.UUID
    result: TestResult
    at: datetime
    note: str = Field(
        description="What the result proves: only that the credential or endpoint works for "
        "this provider operation -- never approval, egress, a higher ceiling or permission "
        "to send content."
    )
