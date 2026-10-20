import { isApiError, retryPhrase } from "@/api/errors";

/**
 * What to tell an administrator when a change is refused. The API's fixed issue codes
 * pick the sentence; nothing submitted is echoed back.
 */
export function changeError(error: unknown, name: string): string {
  if (!isApiError(error)) return "Something went wrong. Try again.";
  if (error.kind === "network") return error.message;
  if (error.status === 409) {
    if (error.issues.includes("taken")) return `Another member of this organization now uses ${name}'s e-mail address.`;
    return `Someone changed ${name}'s access after you opened this. Close it, review the latest, and try again.`;
  }
  if (error.status === 403) {
    return error.issues.includes("own_access")
      ? "You can't change your own access. Ask another administrator."
      : "You no longer have administrator access.";
  }
  if (error.status === 404) return "This is no longer available to you. Close this and reload the page.";
  if (error.isRateLimited) return `You're making changes quickly. Try again ${retryPhrase(error.retryAfter)}.`;
  if (error.status === 422) {
    if (error.issues.includes("no_change")) return "This doesn't change anything.";
    if (error.issues.includes("source_group")) return "Private channels follow their source and can't be changed here.";
    if (error.issues.includes("member_removed")) return `${name} has been removed; their access can't be changed.`;
    if (error.issues.includes("managed_by_identity_provider")) {
      return `${name} is managed by your identity provider. Suspend or reactivate them there.`;
    }
    if (error.issues.includes("source_grant")) return "This grant comes from the source and changes only there.";
    if (error.issues.includes("unknown")) return "Something in this change no longer exists. Close this and reload the page.";
    if (error.issues.includes("last_administrator")) {
      return `${name} is the only active administrator left, so this would leave the organization without one. Make another member an administrator first.`;
    }
    if (error.issues.includes("not_removed")) return `${name} isn't removed, so there is nothing to reinstate. Reload the page.`;
    if (error.issues.includes("identity_not_current")) {
      return `${name}'s sign-in identity belongs to an identity provider connection that's no longer active. Your Lattice operator can help.`;
    }
    if (error.issues.includes("user_inactive")) return `${name}'s user account is disabled. Your Lattice operator can help.`;
    return "Lattice couldn't apply this. Check the reason and try again.";
  }
  return error.message;
}
