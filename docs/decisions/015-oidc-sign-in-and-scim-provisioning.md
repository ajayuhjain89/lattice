# ADR-015: OIDC sign-in and SCIM provisioning, organization-scoped; database-enforced access audit

Status: Accepted for Phase 10 (2026-10-02; D-1 and D-20 are engineering defaults for
review). Implemented: migration 0013, `lattice.identity.oidc`, `lattice.provisioning`,
`lattice.identity.admin`, `/v1/auth/oidc/*`, `/scim/v2/*`.

## Context

ADR-005 promised generic OIDC before any non-local deployment; Phase 9 left member
provisioning and removal to Phase 10 ("SCIM, with OIDC") and recorded a residual risk:
that every access write has its audit event was enforced by code only. Constraints:
administration is not data access; organizations must not be able to affect, find or
infer each other's people; an operator or a misconfigured provider must not bypass the
authorization model; nothing should depend on a hosted identity broker.

Lattice's `users` and `external_identities` are global (one person, several
organizations). Mapping an OIDC `(issuer, sub)` onto them breaks down across tenants:
two organizations may share an issuer (Google, a shared Keycloak realm), and a SCIM
client can send any `externalId` it likes. Linking by e-mail is unsafe (reuse, takeover)
and was rejected in Phase 1.

## Decision

1. **Generic OIDC, one connection per organization** (D-1). Authorization code with
   PKCE (S256), `state`, `nonce` and a browser-binding cookie; attempts stored
   server-side, consumed before anything else (single use, ten minutes). The sign-in URL
   names an opaque login key, never the organization. ID tokens: asymmetric algorithms
   from an allow-list matched to the key type, `iss` exactly the configured issuer (also
   in discovery), `aud`/`azp`, `exp`/`iat`, `nonce`. Network calls bounded (time, size,
   no redirects, HTTPS in production). Client secrets only as
   `env:LATTICE_OIDC_SECRET_*` references. A successful sign-in creates the usual
   server-side session (ADR-005 unchanged).
2. **Identity is scoped to the organization and its connection.** A *directory identity*
   `(connection, external_id) -> membership` exists only in that organization (forced
   RLS). Sign-in maps the connection's configured claim (default `sub`) to it; nothing
   else — no e-mail, no global identity, no other organization — can make a match. No
   just-in-time creation: a person must be provisioned first.
3. **SCIM 2.0 `Users` provisioning** (D-20) with a per-organization bearer token (hash
   stored, operator-issued, revocable). The provider owns user name, e-mail, name and
   `active`; Lattice owns every access attribute and never reads one from SCIM. A
   provisioned person is a **user owned by the organization** (`users.home_organization_id`;
   e-mail unique per owner; a trigger keeps the user out of every other organization), so
   provisioning can never collide with, find, link or modify another organization's user.
   New memberships take the least-access column defaults (enforced by column privileges).
   `active=false` suspends, `DELETE` removes (terminal); sessions are revoked in the same
   transaction. Identical requests are no-ops. The Access page shows such members as
   managed by the provider and refuses lifecycle changes for them (D-19 revisited).
4. **The API role does these writes, fenced in the database** (as ADR-014): INSERT of
   owned users and of least-access memberships for them only, `removed` only for
   provisioned members, directory-identity, sign-in-attempt and counter writes. Two more
   pre-authentication lookups owned by `lattice_auth` (connection by login key, token by
   hash).
5. **Access audit enforced by the database.** `audit_events.txid` records the writing
   transaction; deferred constraint triggers refuse to commit any access write by
   `lattice_app` (memberships, assignments, departments, administrator-group members,
   administrator grants, directory identities) unless the same transaction recorded an
   `access.*` event naming that membership. The threat-model review found this a real
   gap once a second writer (SCIM) existed: a bug in either path would otherwise change
   access silently.
6. **The administrator capability** changes only through the operator command
   `lattice member admin` (audited), so an SSO organization can get its first
   administrator without the API ever being able to grant it.

## Alternatives considered

- **Map OIDC subjects to the global `external_identities`.** Rejected: cross-tenant
  subject collisions and SCIM-asserted subjects would let one organization's provider
  attach or alter another organization's person.
- **Link by verified e-mail / just-in-time membership.** Rejected: e-mail reuse and
  takeover; JIT would let any account of the provider become a member with baseline
  access.
- **A hosted identity broker (WorkOS, Auth0).** Still a reasonable later option (SAML
  breadth); not needed for OIDC + SCIM, and it would add a vendor to the trust boundary.
- **A separate database role for provisioning.** The API process would hold both
  credentials (the ADR-014 argument); column privileges and restrictive policies give
  the same fence.
- **Code-only audit for SCIM.** Rejected: see decision 5.

## Consequences

- A compromised organization identity provider can provision, rename, suspend and
  remove *that organization's* provisioned members and sign in as them, with baseline
  access only; it cannot reach other organizations, manifest members, access attributes
  or the administrator capability. Detectable in the audit log (actor `scim:<token>`).
- A compromised API process can still write access changes **with** an event it
  composes itself (actor and reason are not authenticated by the database); it can no
  longer write one without an event.
- Re-hiring a removed person needs a new `externalId` or an operator. Groups are not
  provisioned. One connection per organization. Sessions survive a connection being
  disabled until they expire or the members are suspended.

## Reconsider when

A customer needs SAML, SCIM groups mapped to Lattice groups (they would grant content:
it needs its own design), several connections per organization, or identity-provider
logout.
