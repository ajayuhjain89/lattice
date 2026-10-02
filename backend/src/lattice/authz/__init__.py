"""Authorization: which resources inside the bound organization a member may read.

Authentication (``lattice.identity``) says who the caller is; the session binds the
organization (RLS). This package answers the third question, deterministically and
without any model involvement. Its public contract:

* ``resolve_authorization_context`` -- the per-request ``AuthorizationContext``.
* ``readable`` -- the reference policy (semantic source of truth).
* ``readable_documents_predicate`` -- the same policy as SQL, for retrieval (E3).
* ``account_visible`` / ``visible_accounts_predicate`` -- which accounts a member may
  know exist (lists, filters, autocomplete, mention detection).
* ``visible_document_account`` -- the account a readable document may show as
  structured metadata: none when the account is outside the member's scope (D-8).
* ``explain`` (internal), ``explain_own_access``, ``explain_for_reviewer``.
* ``assert_all_readable`` -- the pre-generation tripwire (E4).
* ``readable_document_classifications`` -- the classification of readable evidence
  documents, for the AI egress decision (Phase 6; aborts on anything unreadable).
* ``summarize_context`` -- counts-only summary of a context for query traces.
* ``resolve_member_context`` / ``proposed_context`` -- another member's context, as it
  is or as a proposed change would make it, for access review (Phase 9). Never a
  request's own context.
* ``POLICY_VERSION``.

Other modules import from ``lattice.authz`` only (never its submodules) and never
build access conditions themselves (``tests/test_authz_boundary.py``). The access-group
models are exported for the few reviewed writers of access state (the seed, later
ingestion and access management); the boundary test rejects their use anywhere else.
"""

from lattice.authz.access import load_document_access
from lattice.authz.classification import readable_document_classifications
from lattice.authz.context import AuthorizationContext
from lattice.authz.explain import (
    AccessExplanation,
    AccessReason,
    OwnAccessExplanation,
    explain,
    explain_for_reviewer,
    explain_own_access,
)
from lattice.authz.invariant import AuthorizationInvariantViolation, assert_all_readable
from lattice.authz.models import AccessGroup, AccessGroupMember, Document
from lattice.authz.policy import (
    POLICY_VERSION,
    AccountRef,
    DocumentAccess,
    GrantRef,
    account_visible,
    readable,
    visible_document_account,
)
from lattice.authz.resolve import AuthorizationContextUnavailable, resolve_authorization_context
from lattice.authz.review import proposed_context, resolve_member_context
from lattice.authz.sql import readable_documents_predicate, visible_accounts_predicate
from lattice.authz.summary import summarize_context

__all__ = [
    "POLICY_VERSION",
    "AccessExplanation",
    "AccessGroup",
    "AccessGroupMember",
    "AccessReason",
    "AccountRef",
    "AuthorizationContext",
    "AuthorizationContextUnavailable",
    "AuthorizationInvariantViolation",
    "Document",
    "DocumentAccess",
    "GrantRef",
    "OwnAccessExplanation",
    "account_visible",
    "assert_all_readable",
    "explain",
    "explain_for_reviewer",
    "explain_own_access",
    "load_document_access",
    "proposed_context",
    "readable",
    "readable_document_classifications",
    "readable_documents_predicate",
    "resolve_authorization_context",
    "resolve_member_context",
    "summarize_context",
    "visible_accounts_predicate",
    "visible_document_account",
]
