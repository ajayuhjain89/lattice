import { isApiError } from "@/api/errors";

/**
 * What to tell an administrator when a change is refused. The API's fixed issue codes
 * pick the sentence; nothing submitted is echoed back.
 */
export function changeError(error: unknown, name: string): string {
  if (!isApiError(error)) return "Something went wrong. Try again.";
  if (error.kind === "network") return error.message;
  if (error.status === 409) {
    return `Someone changed ${name}'s access after you opened this. Close it, review the latest, and try again.`;
  }
  if (error.status === 403) {
    return error.issues.includes("own_access")
      ? "You can't change your own access. Ask another administrator."
      : "You no longer have administrator access.";
  }
  if (error.status === 404) return "This is no longer available to you. Close this and reload the page.";
  if (error.status === 422) {
    if (error.issues.includes("no_change")) return "This doesn't change anything.";
    if (error.issues.includes("source_group")) return "Private channels follow their source and can't be changed here.";
    if (error.issues.includes("member_removed")) return `${name} has been removed; their access can't be changed.`;
    if (error.issues.includes("source_grant")) return "This grant comes from the source and changes only there.";
    if (error.issues.includes("unknown")) return "Something in this change no longer exists. Close this and reload the page.";
    return "Lattice couldn't apply this. Check the reason and try again.";
  }
  return error.message;
}
