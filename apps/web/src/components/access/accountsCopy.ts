/**
 * Words for accounts and their external references (Phase 12, P12.3). Every rule here is
 * the backend's: the effect of a mapping change comes from its preview, refusals from the
 * API's fixed issue codes. A reference is always shown as the source sent it -- an ID,
 * never a channel name (D-18).
 */
import { isApiError, retryPhrase } from "@/api/errors";
import type { AccountRef, BusinessStatus, MappingPreview, RefKind } from "@/api/types";

export const BUSINESS_STATUSES: readonly BusinessStatus[] = ["active", "churned", "prospect"];

export function businessStatusLabel(status: BusinessStatus): string {
  return { active: "Active", churned: "Churned", prospect: "Prospect" }[status];
}

export function refKindLabel(kind: RefKind): string {
  return kind === "ticketing_organization" ? "Ticketing organization ID" : "Channel ID";
}

/** The reference kind a source's connector reports, if any (documents name account keys). */
export function refKindFor(connectorType: string): RefKind | null {
  if (connectorType === "ticketing") return "ticketing_organization";
  if (connectorType === "chat_export") return "chat_channel";
  return null;
}

export function accountLabel(account: AccountRef | null | undefined): string {
  if (!account) return "No account";
  return account.lifecycle === "archived" ? `${account.name} (archived)` : account.name;
}

/** What the change does at the next sync, in one or two sentences. */
export function mappingEffect(p: MappingPreview): string {
  const ref = `${refKindLabel(p.ref_kind).replace(" ID", "")} ${p.external_ref}`;
  const now = p.now?.name ?? "";
  const after = p.after?.name ?? "";
  const n = p.records_waiting;
  switch (p.effect) {
    case "indexed":
      return n === 0
        ? `Records from ${ref} are indexed under ${after}.`
        : n === 1
          ? `The record waiting for ${ref} is indexed under ${after}.`
          : `The ${n.toLocaleString("en-GB")} records waiting for ${ref} are indexed under ${after}.`;
    case "scoped":
      return `Records from ${ref} become ${after}'s: only people who can see ${after} can read them, on top of the source's own rules.`;
    case "moved":
      return `Records from ${ref} move from ${now} to ${after}. Until then they stay with ${now}.`;
    case "quarantined":
      return `Records from ${ref} become unresolved: nobody can read them until the reference is mapped to an account again. Nothing is deleted, and they are never indexed without an account.`;
  }
}

/** What to tell an administrator when an account or mapping change is refused. */
export function accountError(error: unknown, name = "this account"): string {
  if (!isApiError(error)) return "Something went wrong. Try again.";
  if (error.kind === "network") return error.message;
  if (error.status === 409) {
    if (error.fields.includes("key")) return "Another account already uses this key.";
    if (error.issues.includes("already_mapped")) return "This reference was mapped by someone else meanwhile. Close this and review it again.";
    if (error.issues.includes("mapping_changed")) return "This reference's mapping changed after you opened this. Close this and review the latest.";
    return `Someone changed ${name} after you opened this. Close this, review the latest, and try again.`;
  }
  if (error.status === 403) return "You no longer have administrator access.";
  if (error.status === 404) return "This account is no longer available. Close this and reload the page.";
  if (error.isRateLimited) return `You're making changes quickly. Try again ${retryPhrase(error.retryAfter)}.`;
  if (error.status === 422) {
    if (error.issues.includes("archived")) return `${name} is archived, so it can't be changed. Reactivate it first.`;
    if (error.issues.includes("account_archived")) return "That account is archived: it takes no new references. Reactivate it first, or pick another account.";
    if (error.issues.includes("no_change")) return "This doesn't change anything.";
    if (error.issues.includes("ref_kind_mismatch")) return "This source doesn't send that kind of reference.";
    if (error.issues.includes("reference_invalid")) return "Enter the reference exactly as the source sends it. A ticketing organization ID is digits only, without leading zeros.";
    if (error.issues.includes("unknown_source")) return "That source is no longer available. Close this and reload the page.";
    if (error.issues.includes("unknown_account")) return "That account is no longer available. Close this and reload the page.";
    return "Lattice couldn't apply this. Check each field and try again.";
  }
  return error.message;
}
