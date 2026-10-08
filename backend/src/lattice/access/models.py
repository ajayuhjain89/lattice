"""The access-management contract (Phase 9): what an administrator is shown and may send.

Every model here crosses HTTP as-is (OpenAPI → the web app's generated types). The
rules behind the fields are security-model §10:

* members, accounts, departments and administrator-defined groups are the
  administrator's directory (operational, not content);
* a source-synced (private) group exists for the viewing administrator only if they are
  a member of it. Otherwise nothing about it is returned -- no name, members, count or
  identifier -- and a member's access says only that such membership exists
  (``has_hidden_groups``);
* a document appears (title, reference, source) only when the viewing administrator
  can read it now; a member's grants on other documents are not listed or counted
  (``has_other_grants``), and audit events about them are not listed at all;
* document counts are computed only over documents the viewing administrator can read.
"""

import unicodedata
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Clearance = Literal["internal", "confidential"]
AccountScope = Literal["all", "assigned"]
MemberStatus = Literal["active", "suspended", "removed"]
SettableStatus = Literal["active", "suspended"]
Origin = Literal["admin", "source"]

MAX_REASON = 500


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class _In(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Named(_Out):
    id: uuid.UUID
    name: str


class NamedRef(_Out):
    """An entity named in an audit event; ``name`` is ``None`` when it no longer exists."""

    id: uuid.UUID
    name: str | None


class MemberSummary(_Out):
    id: uuid.UUID
    name: str
    email: str
    role_label: str | None = Field(description="Display only; never an access input.")
    status: MemberStatus
    is_admin: bool = Field(description="Administrative capability; grants no content access.")
    clearance: Clearance
    account_scope: AccountScope
    assigned_accounts: int
    departments: list[Named]
    managed_by: Literal["identity_provider"] | None = Field(
        description="Provisioned by the organization's identity provider (SCIM): it owns "
        "the member's name, e-mail and whether they are active; access stays editable here."
    )


class MembersPage(_Out):
    items: list[MemberSummary]
    next_after: uuid.UUID | None = Field(
        description="Pass as `after` for the next page; null on the last page."
    )


class DirectoryAccount(_Out):
    id: uuid.UUID
    key: str
    name: str
    status: str


class DirectoryDepartment(_Out):
    id: uuid.UUID
    key: str
    name: str


class DirectoryGroup(_Out):
    id: uuid.UUID
    key: str
    name: str
    members: int


class Directory(_Out):
    """What an administrator may assign: every account and department, and the
    administrator-defined groups (source-synced groups are owned by their source)."""

    accounts: list[DirectoryAccount]
    departments: list[DirectoryDepartment]
    groups: list[DirectoryGroup]


class GroupAccess(_Out):
    id: uuid.UUID
    origin: Origin
    name: str
    connector_type: str | None = Field(description="The source's connector (source groups).")
    members: list[Named]
    editable: bool = Field(description="Administrator-defined groups only.")


class DocumentRef(_Out):
    """A document the viewing administrator can read."""

    id: uuid.UUID
    title: str
    display_ref: str | None
    source_name: str
    connector_type: str
    classification: str


class GrantOut(_Out):
    id: uuid.UUID
    origin: Origin
    document: DocumentRef
    created_at: datetime
    granted_by: str | None = Field(description="The administrator who granted it, if recorded.")
    reason: str | None
    revocable: bool = Field(
        description="Administrator grants only; source grants follow the source."
    )


LifecycleBlock = Literal[
    "own_membership",
    "managed_by_identity_provider",
    "last_administrator",
    "identity_not_current",
    "user_inactive",
    "email_taken",
]


class LifecycleAction(_Out):
    """One lifecycle action the product offers for this member, decided by the backend."""

    available: bool
    blocked_by: LifecycleBlock | None = Field(
        description="Why it is not available now; null when it is."
    )


class MemberLifecycle(_Out):
    """What may happen to the member's lifecycle and administrator capability here. An
    action that does not apply to the member's state is null (e.g. ``reinstate`` for a
    member who was not removed). The page shows these; it never infers them."""

    sign_in_identifier: str | None = Field(
        description="The exact value the organization's identity provider sends for them "
        "(the connection's identifying claim); null when they have no directory identity."
    )
    identity_managed_by: Literal["identity_provider", "organization"] | None = Field(
        description="Who owns their lifecycle: the identity provider (SCIM) or this "
        "organization's administrators; null without a directory identity."
    )
    suspend: LifecycleAction | None
    reactivate: LifecycleAction | None
    reinstate: LifecycleAction | None = Field(
        description="A removed member made active again with least access (D-36)."
    )
    grant_administrator: LifecycleAction | None
    revoke_administrator: LifecycleAction | None


class MemberAccess(_Out):
    member: MemberSummary
    lifecycle: MemberLifecycle
    version: datetime = Field(
        description="Send back as `version` with a change; a stale value is refused (409)."
    )
    is_self: bool = Field(description="Your own membership: it cannot be changed here.")
    accounts: list[Named]
    departments: list[Named]
    groups: list[GroupAccess] = Field(
        description="Administrator-defined groups, and synced groups you are a member of."
    )
    has_hidden_groups: bool = Field(
        description="The member is in private groups you are not in (not named, listed, "
        "counted or identified)."
    )
    grants: list[GrantOut] = Field(description="Grants on documents you can read.")
    has_other_grants: bool = Field(
        description="The member has grants on documents you cannot read (not listed or counted)."
    )


class MemberChange(_In):
    """A proposed change. Omitted fields stay as they are; ``accounts``,
    ``departments`` and ``groups`` are complete sets (groups: the
    administrator-defined groups only; source-group membership is untouched)."""

    status: SettableStatus | None = None
    clearance: Clearance | None = None
    account_scope: AccountScope | None = None
    accounts: list[uuid.UUID] | None = Field(default=None, max_length=1000)
    departments: list[uuid.UUID] | None = Field(default=None, max_length=200)
    groups: list[uuid.UUID] | None = Field(default=None, max_length=200)


class MemberChangeRequest(MemberChange):
    version: datetime
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _reason_has_text(self) -> "MemberChangeRequest":
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


EffectKind = Literal[
    "provisioned",
    "created",
    "suspended",
    "removed",
    "reinstated",
    "reactivated",
    "administrator",
    "clearance",
    "account_scope",
    "account_added",
    "account_removed",
    "department_added",
    "department_removed",
    "group_added",
    "group_removed",
]


class AccessEffect(_Out):
    """One effect of a change, as a rule ("loses Kestrel Bank"); never a document."""

    kind: EffectKind
    before: str | None = None
    after: str | None = None
    subject: NamedRef | None = Field(
        default=None, description="The account, department or group added or removed."
    )


class AccessPreview(_Out):
    effects: list[AccessEffect]
    documents_gained: int = Field(description="Among documents you can read.")
    documents_lost: int = Field(description="Among documents you can read.")


def _reason(value: str) -> str:
    if not value.strip():
        raise ValueError("reason must not be blank")
    return value


_CONTROL = frozenset(chr(c) for c in (*range(32), 127))


def _plain(value: str | None, field: str) -> str | None:
    """Trimmed; no control characters (the value is never echoed in an error)."""
    if value is None:
        return None
    cleaned = value.strip()
    if any(c in _CONTROL for c in cleaned):
        raise ValueError(f"{field} must not contain control characters")
    return cleaned


class IdentityRequirement(_Out):
    """What a member added by hand must be bound to: the organization's active OIDC
    connection and the claim whose exact value identifies a person."""

    available: bool = Field(description="False until the organization has an active connection.")
    subject_claim: str | None = Field(
        description="The identifying claim (e.g. `sub` or `email`) whose exact value is the "
        "member's identifier."
    )
    issuer: str | None


class NewMemberRequest(_In):
    """A person added by hand (D-30). Their access starts at least privilege: the request
    cannot carry access."""

    email: str = Field(min_length=3, max_length=255, description="Contact and display only.")
    name: str = Field(min_length=1, max_length=200)
    given_name: str | None = Field(default=None, max_length=200)
    family_name: str | None = Field(default=None, max_length=200)
    identifier: str = Field(
        min_length=1,
        max_length=255,
        description="The exact value the identity provider sends in the connection's "
        "identifying claim for this person. Compared exactly (case preserved), never "
        "matched by e-mail.",
    )
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _normalise(self) -> "NewMemberRequest":
        email = _plain(self.email, "email") or ""
        if (
            email.count("@") != 1
            or email.startswith("@")
            or email.endswith("@")
            or any(c.isspace() for c in email)
        ):
            raise ValueError("email must be one address")
        name = _plain(self.name, "name")
        if not name:
            raise ValueError("name must not be blank")
        identifier = _plain(unicodedata.normalize("NFC", self.identifier), "identifier")
        if not identifier:
            raise ValueError("identifier must not be blank")
        _reason(self.reason)
        for field, value in (
            ("email", email),
            ("name", name),
            ("given_name", _plain(self.given_name, "given_name") or None),
            ("family_name", _plain(self.family_name, "family_name") or None),
            ("identifier", identifier),
        ):
            object.__setattr__(self, field, value)
        return self


class AdministratorChange(_In):
    administrator: bool = Field(description="True: grant the capability; false: revoke it.")


class AdministratorRequest(AdministratorChange):
    version: datetime
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _reason_has_text(self) -> "AdministratorRequest":
        _reason(self.reason)
        return self


class AdministratorPreview(_Out):
    """Now → After for the administrator capability. Content access never changes."""

    now: bool
    after: bool
    content_access_changes: Literal[False] = Field(
        default=False, description="Always false: administration grants no access to content."
    )


class ReinstateRequest(_In):
    version: datetime
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _reason_has_text(self) -> "ReinstateRequest":
        _reason(self.reason)
        return self


class GrantRequest(_In):
    document_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _reason_has_text(self) -> "GrantRequest":
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


class RevokeRequest(_In):
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @model_validator(mode="after")
    def _reason_has_text(self) -> "RevokeRequest":
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


class DocumentsPage(_Out):
    items: list[DocumentRef]


class GrantVia(_Out):
    principal_type: Literal["membership", "department", "group"]
    name: str | None = Field(
        description="Null for a private group you are not in (nothing else is given)."
    )


class DocumentExplanation(_Out):
    """Why the member can or cannot read a document you can read (``explain_for_reviewer``)."""

    document: DocumentRef
    readable: bool
    path: Literal["grant", "policy", "none"]
    reasons: list[str]
    via: list[GrantVia]


class Actor(_Out):
    kind: Literal["member", "operator", "identity_provider"]
    name: str | None = Field(description="The member's name; null if no longer a member.")
    operator: str | None = Field(description="The operator tool (seed, admin-grants, cli).")


class AuditDetail(_Out):
    field: str
    before: str | None
    after: str | None


class AuditEventOut(_Out):
    id: uuid.UUID
    created_at: datetime
    action: str
    actor: Actor
    reason: str
    member: NamedRef | None = Field(description="The member whose access changed.")
    document: DocumentRef | None = Field(
        description="The target document. Events about documents you cannot read now are "
        "not listed."
    )
    effects: list[AccessEffect]
    details: list[AuditDetail] = Field(
        description="Provider and egress settings events, and identity changes from the "
        "identity provider (user name, e-mail, name)."
    )


class AuditPage(_Out):
    items: list[AuditEventOut]
    next_before: uuid.UUID | None


class VisibleAccount(_Out):
    key: str = Field(description="What an Ask scope names (`scope.account`).")
    name: str


class MemberAccounts(_Out):
    """The accounts you may know exist -- the only ones an Ask scope may name."""

    account_scope: AccountScope
    accounts: list[VisibleAccount]
