/**
 * Access-page sentences, built only from what the API returned (design-system §5.9
 * "Access explanation": sentences, not policy objects). No counts, names or documents
 * are added here: private channels the administrator isn't in and documents they can't
 * read are acknowledged only as such -- never named, counted or identified.
 */
import type { AccessEffect, AuditEvent, GroupAccess, MemberAccess } from "@/api/types";
import { connectorLabel, humanize, levelLabel } from "@/lib/format";

/** A sentence as parts; `strong` parts are names the UI emphasises. */
export type Part = string | { strong: string };

export function listNames(names: readonly string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

function strongList(names: readonly string[]): Part[] {
  const parts: Part[] = [];
  names.forEach((name, i) => {
    if (i > 0) parts.push(i === names.length - 1 ? " and " : ", ");
    parts.push({ strong: name });
  });
  return parts;
}

/** Where a group comes from: "Administrator-defined" or "Synced from Chat". */
export function groupOrigin(group: GroupAccess): string {
  if (group.origin === "admin") return "Administrator-defined";
  return `Synced from ${group.connector_type ? connectorLabel(group.connector_type) : "a source"} · you're a member`;
}

export function scopeLabel(scope: string): string {
  return scope === "all" ? "All accounts" : "Assigned accounts only";
}

/** The member's access in one or two sentences (B12 "Access explanation"). */
export function accessSummary(access: MemberAccess): Part[][] {
  const name = access.member.name;
  const status = access.member.status;
  if (status === "suspended") return [[{ strong: name }, " is suspended and can't read anything in Lattice."]];
  if (status === "removed") return [[{ strong: name }, " has been removed and can't read anything in Lattice."]];

  const first: Part[] = [{ strong: name }, " can see "];
  if (access.member.account_scope === "all") first.push("content for every account");
  else if (access.accounts.length === 0) first.push("content that isn't tied to an account");
  else first.push(...strongList(access.accounts.map((a) => a.name)), " content (assigned accounts)");
  first.push(", up to ", { strong: levelLabel(access.member.clearance) }, " sensitivity");
  if (access.departments.length > 0) {
    first.push(", including content restricted to ", ...strongList(access.departments.map((d) => d.name)));
  }
  const extras: Part[][] = [];
  if (access.groups.length > 0) {
    extras.push(["what's shared with ", ...strongList(access.groups.map((g) => g.name))]);
  }
  if (access.grants.length > 0) {
    extras.push([`${access.grants.length} ${access.grants.length === 1 ? "document" : "documents"} granted directly`]);
  }
  extras.forEach((extra, i) => {
    first.push(i === 0 ? ", plus " : i === extras.length - 1 ? " and " : ", ", ...extra);
  });
  first.push(".");
  const sentences = [first];
  if (access.has_hidden_groups) {
    sentences.push([
      { strong: name },
      " is also in private channels you aren't in. They aren't named, listed or counted here.",
    ]);
  }
  if (access.has_other_grants) {
    sentences.push([
      { strong: name },
      " also has direct access to documents you can't read. They aren't listed or counted here.",
    ]);
  }
  return sentences;
}

function subject(effect: AccessEffect, kind: string): string {
  return effect.subject?.name ?? `a ${kind} that no longer exists`;
}

/** What a proposed change will do, as a rule (B12 change previews). Never a document. */
export function previewSentence(effect: AccessEffect, name: string): string {
  switch (effect.kind) {
    case "suspended":
      return `${name} will immediately lose access to everything in Lattice.`;
    case "reactivated":
      return `${name} will regain the access described on this page.`;
    case "clearance":
      return effect.after === "confidential"
        ? `${name} will be able to read Confidential content that the other rules allow.`
        : `${name} will lose access to Confidential content, except documents granted directly.`;
    case "account_scope":
      return effect.after === "all"
        ? `${name} will see content for every account.`
        : `${name} will see only the accounts assigned to them here.`;
    case "account_added":
      return `${name} will gain access to ${subject(effect, "account")} content.`;
    case "account_removed":
      return `${name} will lose access to all ${subject(effect, "account")} content, except documents granted directly.`;
    case "department_added":
      return `${name} will be able to read content restricted to ${subject(effect, "department")}.`;
    case "department_removed":
      return `${name} will lose access to content restricted to ${subject(effect, "department")}.`;
    case "group_added":
      return `${name} will be able to read what's shared with ${subject(effect, "group")}.`;
    case "group_removed":
      return `${name} will lose what's shared with ${subject(effect, "group")}.`;
    default:
      return humanize(effect.kind);
  }
}

/** A recorded change, compactly (the audit view). */
export function auditPhrase(effect: AccessEffect): string {
  const arrow = (a: string | null | undefined, b: string | null | undefined, label: (v: string) => string) =>
    a ? `${label(a)} → ${label(b ?? "")}` : label(b ?? "");
  switch (effect.kind) {
    case "provisioned":
      return "Added to Lattice";
    case "suspended":
      return "Suspended";
    case "reactivated":
      return "Reactivated";
    case "administrator":
      return effect.after === "true" ? "Made an administrator" : "Administrator capability removed";
    case "clearance":
      return `Clearance ${arrow(effect.before, effect.after, levelLabel)}`;
    case "account_scope":
      return `Account scope ${arrow(effect.before, effect.after, (v) => scopeLabel(v).toLowerCase())}`;
    case "account_added":
      return `Assigned ${subject(effect, "account")}`;
    case "account_removed":
      return `Unassigned ${subject(effect, "account")}`;
    case "department_added":
      return `Added to ${subject(effect, "department")}`;
    case "department_removed":
      return `Removed from ${subject(effect, "department")}`;
    case "group_added":
      return `Added to ${subject(effect, "group")}`;
    case "group_removed":
      return `Removed from ${subject(effect, "group")}`;
    default:
      return humanize(effect.kind);
  }
}

const ACTIONS: Record<string, string> = {
  "access.member_changed": "Access changed",
  "access.grant_created": "Document granted",
  "access.grant_revoked": "Grant revoked",
  "provider.configured": "Provider configured",
  "provider.approval_changed": "Provider approval changed",
  "provider.enabled": "Provider enabled",
  "provider.disabled": "Provider disabled",
  "credential.set": "Credential set",
  "credential.rotated": "Credential rotated",
  "credential.revoked": "Credential revoked",
  "egress.levels_changed": "AI data policy changed",
  "access.identity_changed": "Profile updated",
  "sso.connection_created": "Sign-in connection added",
  "sso.connection_disabled": "Sign-in connection disabled",
  "sso.connection_enabled": "Sign-in connection re-enabled",
  "sso.connection_updated": "Sign-in connection updated",
  "session.revoked": "Sessions ended",
  "organization.suspended": "Organization suspended",
  "organization.reactivated": "Organization reactivated",
  "scim.token_created": "Provisioning token created",
  "scim.token_revoked": "Provisioning token revoked",
};

export function actionLabel(action: string): string {
  return ACTIONS[action] ?? humanize(action.replace(".", " "));
}

const OPERATORS: Record<string, string> = {
  seed: "Organization manifest (seed)",
  "admin-grants": "admin-grants.yaml",
  cli: "Operator command",
};

export function actorLabel(event: AuditEvent): string {
  if (event.actor.kind === "member") return event.actor.name ?? "A former member";
  if (event.actor.kind === "identity_provider") return "Your identity provider (SCIM)";
  return OPERATORS[event.actor.operator ?? ""] ?? `Operator (${event.actor.operator ?? "unknown"})`;
}

const REASONS: Record<string, (name: string) => string> = {
  grant_to_member: (n) => `it's granted to ${n} directly`,
  grant_to_department: (n) => `it's granted to a department ${n} is in`,
  grant_to_group: (n) => `it's granted to a group or channel ${n} is in`,
  clearance_covers_sensitivity: (n) => `${n}'s clearance covers its sensitivity`,
  not_account_specific: () => "it isn't tied to an account",
  account_scope_all: (n) => `${n}'s scope covers every account`,
  account_assigned: (n) => `${n} is assigned to its account`,
  no_department_restriction: () => "it isn't restricted to a department",
  department_member: (n) => `${n} is in a department it's restricted to`,
  membership_inactive: (n) => `${n} is suspended`,
  document_inactive: () => "it has been deleted",
  grants_only_without_grant: (n) => `it's shared only with specific people, groups or channels, and ${n} isn't one of them`,
  restricted_without_grant: () => "it's Restricted, and only an explicit grant gives access",
  clearance_insufficient: (n) => `${n}'s clearance doesn't cover its sensitivity`,
  account_not_in_scope: (n) => `its account isn't in ${n}'s scope`,
  department_not_matched: (n) => `it's restricted to departments ${n} isn't in`,
};

const GRANTING = new Set([
  "grant_to_member",
  "grant_to_department",
  "grant_to_group",
  "clearance_covers_sensitivity",
  "not_account_specific",
  "account_scope_all",
  "account_assigned",
  "no_department_restriction",
  "department_member",
]);

/** The reasons that decide: for a readable document every reason; otherwise only those that block. */
export function decidingReasons(reasons: readonly string[], readable: boolean): string[] {
  return readable ? [...reasons] : reasons.filter((r) => !GRANTING.has(r));
}

export function reasonPhrase(code: string, name: string): string {
  return REASONS[code]?.(name) ?? "an access setting Lattice doesn't recognise (denied)";
}
