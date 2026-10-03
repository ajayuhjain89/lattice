/**
 * Where to go after signing in (`?next=`). Only a same-origin path is ever followed, by
 * the same rule as the API's OIDC `return_to` (`lattice.identity.oidc.safe_return_to`): it
 * starts with one "/", is at most 512 characters, and has no control character or
 * backslash anywhere -- browsers drop tabs and newlines from URLs, so "/\t/evil.example"
 * would otherwise become the protocol-relative "//evil.example". The parsed origin is
 * checked as well, so nothing the URL parser reads as another host gets through.
 */
const UNSAFE = /[\x00-\x1f\x7f\\]/;

export function safeNextPath(value: string | null | undefined): string | null {
  if (!value || value.length > 512 || !value.startsWith("/") || value.startsWith("//") || UNSAFE.test(value)) return null;
  try {
    const base = "http://next.invalid";
    if (new URL(value, base).origin !== base) return null;
  } catch {
    return null;
  }
  // Sign-in never sends you back to sign-in.
  if (value === "/sign-in" || value.startsWith("/sign-in?") || value.startsWith("/sign-in/") || value.startsWith("/sso/")) return null;
  return value;
}

/** The destination after sign-in: a safe `next`, or Ask. */
export function afterSignIn(value: string | null | undefined): string {
  return safeNextPath(value) ?? "/ask";
}
