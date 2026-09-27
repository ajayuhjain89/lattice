"""Authorization: which resources inside the bound organization a member may read.

Authentication (``lattice.identity``) says who the caller is; the session binds the
organization (RLS). This package answers the third question, deterministically and
without any model involvement. Its public contract:

* ``resolve_authorization_context`` -- the per-request ``AuthorizationContext``.
* ``readable`` -- the reference policy (semantic source of truth).
* ``readable_documents_predicate`` -- the same policy as SQL, for retrieval (E3).
* ``account_visible`` / ``visible_accounts_predicate`` -- which accounts a member may
  know exist (lists, filters, autocomplete, mention detection).
* ``explain`` (internal), ``explain_own_access``, ``explain_for_reviewer``.
* ``assert_all_readable`` -- the pre-generation tripwire (E4).
* ``POLICY_VERSION``.

Other modules import from ``lattice.authz`` only (never its submodules) and never
build access conditions themselves (``tests/test_authz_boundary.py``).
"""

from lattice.authz.access import load_document_access
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
from lattice.authz.models import Document
from lattice.authz.policy import (
    POLICY_VERSION,
    AccountRef,
    DocumentAccess,
    GrantRef,
    account_visible,
    readable,
)
from lattice.authz.resolve import AuthorizationContextUnavailable, resolve_authorization_context
from lattice.authz.sql import readable_documents_predicate, visible_accounts_predicate

__all__ = [
    "POLICY_VERSION",
    "AccessExplanation",
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
    "readable",
    "readable_documents_predicate",
    "resolve_authorization_context",
    "visible_accounts_predicate",
]
