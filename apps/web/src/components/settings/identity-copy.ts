/**
 * Words for the identity settings (Phase 12, P12.4). The API decides every state, result
 * and refusal; this file only says them. It never infers whether sign-in works.
 */
import { isApiError, retryPhrase } from "@/api/errors";
import type { ConnectionCheck, OidcConnection, ScimToken, TestOutcome, TestSignIn } from "@/api/types";

export const CLAIM_HELP = "The ID-token claim whose value identifies a person. It must never change for a person, and SCIM's externalId must carry the same value.";

/** The identifying claims offered first; any claim name can be typed. */
export const COMMON_CLAIMS = ["sub", "oid", "email", "preferred_username"] as const;

export function issuerAccessLabel(access: OidcConnection["issuer_access"]): string {
  switch (access) {
    case "public":
      return "Public address";
    case "operator_approved":
      return "Private address approved by your Lattice operator";
    case "not_permitted":
      return "Refused by the outbound policy";
  }
}

export function clientSecretLabel(secret: OidcConnection["client_secret"]): string {
  switch (secret.state) {
    case "encrypted":
      return "Set in Lattice, encrypted";
    case "environment":
      return "Set by your Lattice operator in the deployment";
    case "none":
      return "None: a public client (PKCE only)";
  }
}

const CHECK_STEP: Record<ConnectionCheck["step"], string> = {
  issuer: "the issuer's address",
  discovery: "the discovery document",
  keys: "the signing keys",
  client: "Lattice's client credentials",
};

/** One sentence for a connection check's result. */
export function checkMessage(check: ConnectionCheck): string {
  switch (check.result) {
    case "valid":
      return "The connection works: Lattice reached the identity provider, read its keys, and the provider accepted Lattice's client credentials.";
    case "invalid_configuration":
      return `The identity provider's configuration doesn't match what Lattice expects, at ${CHECK_STEP[check.step]}. Check the issuer address.`;
    case "credentials_rejected":
      return "The identity provider rejected Lattice's client ID or client secret.";
    case "provider_unavailable":
      return `Lattice couldn't reach the identity provider (${CHECK_STEP[check.step]}). Try again later; nothing changed.`;
    case "outbound_blocked":
      return "Lattice's outbound policy refuses this address. A private or self-hosted identity provider must be approved for this organization by your Lattice operator.";
  }
}

const OUTCOME_TITLE: Record<TestOutcome, string> = {
  matched: "Test sign-in matched your identity",
  mismatched: "Test sign-in returned a different identifier",
  not_provisioned: "Test sign-in worked, but your membership doesn't sign in this way",
  sign_in_failed: "Test sign-in didn't complete",
  token_invalid: "The identity provider's token was refused",
  provider_unavailable: "Lattice couldn't reach the identity provider",
  invalid_configuration: "The identity provider's configuration doesn't match",
  outbound_blocked: "The outbound policy refused the identity provider",
  credentials_rejected: "The identity provider rejected Lattice's client credentials",
};

export function testTitle(test: TestSignIn): string {
  if (test.status === "pending") return "Waiting for the test sign-in to finish";
  if (test.status === "expired" || test.outcome === null || test.outcome === undefined) return "This test sign-in expired before it finished";
  return OUTCOME_TITLE[test.outcome];
}

/** What a finished test sign-in means, for the administrator who ran it. */
export function testMessage(test: TestSignIn): string {
  const claim = test.subject_claim;
  switch (test.outcome) {
    case "matched":
      return test.proposed
        ? `The identity provider returned your own identifier for ${claim}. You can apply the change once, within ten minutes of this test.`
        : "Sign-in through the identity provider works, and it recognises you. No session was created.";
    case "mismatched":
      return test.proposed
        ? `With these values the identity provider sends a different ${claim} than the one your membership signs in with. Existing members would not be recognised: that is a change of identity provider subjects, which Lattice doesn't support. Nothing was changed.`
        : `The identity provider returned a different ${claim} than the one your membership signs in with. Someone else may have signed in at the provider.`;
    case "not_provisioned":
      return "You signed in at the provider, but your own membership has no identity on this connection (you may use another sign-in method), so nothing could be compared.";
    case "sign_in_failed":
      return "The sign-in was cancelled or the provider refused the request. Nothing was recorded against the connection.";
    case "token_invalid":
      return "Lattice refused the provider's ID token (its signature, audience, issuer, or the identifying claim). Check the client ID and the identifying claim.";
    case "provider_unavailable":
      return "Lattice couldn't reach the identity provider during the test.";
    case "invalid_configuration":
      return "The provider's discovery document or keys don't match the issuer address.";
    case "outbound_blocked":
      return "Lattice's outbound policy refuses this identity provider's address. A private provider needs your Lattice operator's approval.";
    case "credentials_rejected":
      return "The identity provider rejected Lattice's client ID or client secret.";
    default:
      return test.status === "pending"
        ? "Finish signing in at your identity provider. This page updates when it's done."
        : "Start a new test sign-in.";
  }
}

export function tokenLabel(token: ScimToken): string {
  return token.hint ? `Token ending ${token.hint}` : `Token ${token.id.slice(0, 8)}`;
}

/** A label inside a sentence: only its first letter lowered ("redirect URI", "token ending Qx7f"). */
export function lowerFirst(label: string): string {
  return label.charAt(0).toLowerCase() + label.slice(1);
}

export function tokenName(token: ScimToken): string {
  return lowerFirst(tokenLabel(token));
}

/** What to tell an administrator when an identity change is refused. */
export function identityError(error: unknown): string {
  if (!isApiError(error)) return "Something went wrong. Try again.";
  if (error.kind === "network") return error.message;
  if (error.status === 403) return "You no longer have administrator access.";
  if (error.status === 404) return "This is no longer available. Close this and reload the page.";
  if (error.isRateLimited) return `You're making changes quickly. Try again ${retryPhrase(error.retryAfter)}.`;
  const issue = error.issues;
  if (error.status === 409) {
    if (issue.includes("connection_exists")) return "Another connection is active. Disable it first, or change it instead.";
    if (issue.includes("token_not_live")) return "This token is already revoked.";
    return "Someone changed the identity settings after you opened this. Close this, review the latest, and try again.";
  }
  if (issue.includes("outbound_blocked"))
    return "Lattice's outbound policy refuses this address. A private or self-hosted identity provider must be approved for this organization by your Lattice operator.";
  if (issue.includes("issuer_unreachable")) return "Lattice couldn't find this address. Check the issuer.";
  if (issue.includes("identity_namespace_in_use"))
    return "A disabled connection still signs members in. Enable it again instead: a new connection wouldn't recognise them.";
  if (issue.includes("identity_unverified"))
    return "Your own test sign-in with exactly these values hasn't matched your identity (or it was used or expired). Run the test sign-in again. Moving to an identity provider whose identifiers differ isn't supported.";
  if (issue.includes("connection_disabled")) return "The connection is disabled. Enable it first.";
  if (issue.includes("secret_storage_unavailable"))
    return "Lattice can't store secrets in this deployment yet. Your Lattice operator needs to configure its encryption keys.";
  if (issue.includes("mismatch")) return "The confirmation doesn't match. Type it exactly as shown.";
  if (issue.includes("no_change")) return "Nothing would change.";
  if (issue.includes("no_active_connection")) return "There's no active connection to test or use.";
  return error.message;
}
