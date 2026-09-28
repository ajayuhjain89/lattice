# ADR-005: Server-side sessions; development identity for the slice; OIDC before deployment

Status: Accepted (proposed in Phase 0; server-side sessions and the development identity
provider implemented in Phase 1; OIDC before any deployment, Phase 10)

## Context
Permission and membership changes must take effect immediately (SEC-4, SEC-11).
Enterprise customers authenticate through SSO. The slice needs several personas to
demonstrate permission differences.

## Decision
Opaque random session tokens in HttpOnly/Secure/SameSite=Lax cookies; SHA-256 stored
server-side; membership status checked each request. Identity providers behind one
interface: a development persona login (refuses to run in production) now; generic
OIDC (authorization code + PKCE) before any non-local deployment. No passwords.

## Alternatives considered
- **Stateless JWTs:** revocation needs deny-lists or short expiry + refresh
  complexity; no benefit at our scale.
- **Hosted auth (Auth0/Clerk/WorkOS):** reasonable later for SSO breadth, but adds a
  vendor to the trust boundary before it's needed; OIDC keeps it pluggable.
- **Password auth:** a product surface (reset, lockout, MFA) that enterprises don't
  want from us.

## Consequences
One indexed lookup per request; session table cleanup job; CSRF protection via
SameSite + custom header.

## Reconsider when
We need SAML-only customers or SCIM provisioning at scale (then a hosted identity
broker becomes attractive).
