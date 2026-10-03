/**
 * What the sign-in pages may say. Shared by the production and development compositions,
 * which differ in job, not in rules.
 */
import { DESTINATIONS, SUBPAGES } from "@/components/shell/nav";
import { safeNextPath } from "@/lib/next-path";

/**
 * The sign-in errors the API's OIDC routes redirect with: a closed set of three, each a
 * fixed sentence. Every failure the API doesn't call "unavailable" or "rate limited" --
 * an unknown or malformed login key, an expired, reused or foreign-browser attempt, an
 * identity with no active membership, a suspended or removed member -- arrives as
 * `sso_failed` and reads the same, so the page never says whether a person, an
 * organization or a connection exists. Any other value is ignored, never echoed.
 */
export const SSO_ERRORS = {
  sso_failed:
    "Sign-in didn't complete. Try again from your identity provider. If it keeps failing, ask your administrator whether your account has access to Lattice.",
  sso_unavailable: "Your identity provider couldn't be reached. Try again in a moment.",
  rate_limited: "Too many sign-in attempts from this browser. Wait a minute and try again.",
} as const;

export type SsoError = keyof typeof SSO_ERRORS;

export function ssoError(code: string | null | undefined): SsoError | null {
  return code && Object.hasOwn(SSO_ERRORS, code) ? (code as SsoError) : null;
}

/** The document title while an error shows, so it is heard on arrival as well as seen. */
export const SSO_ERROR_TITLES: Record<SsoError, string> = {
  sso_failed: "Sign-in didn't complete",
  sso_unavailable: "Identity provider unavailable",
  rate_limited: "Too many sign-in attempts",
};

export interface Continuation {
  /** The safe path itself (the viewer's own, from their own address bar). */
  path: string;
  /** The destination it belongs to ("Access"), from the navigation model. */
  label: string;
}

/**
 * Where the person was going, named by the page it belongs to. Only the path is used:
 * nothing is looked up, so a member or trace id is never resolved to a name here.
 */
export function continuation(next: string | null | undefined): Continuation | null {
  const path = safeNextPath(next);
  if (!path) return null;
  const pathname = path.split(/[?#]/)[0]!;
  const sub = SUBPAGES.find((s) => pathname === s.href);
  const destination = DESTINATIONS.find((d) => d.match(pathname));
  const label = sub?.label ?? destination?.label;
  return label ? { path, label } : null;
}
