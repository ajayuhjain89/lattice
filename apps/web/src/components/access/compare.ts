/**
 * The review of an access change as *Now → After*, rule by rule (design-direction §6.8).
 * Built from the member as loaded and the effects the preview endpoint returned -- the
 * server's reading of the change, not the form's -- so the comparison shows exactly what
 * the API says will happen. Direction (gain / loss) is a reading of each effect's kind;
 * nothing is counted here: document counts come only from the preview, among documents
 * the reviewer can read.
 */
import type { AccessEffect, MemberAccess } from "@/api/types";
import { humanize, levelLabel } from "@/lib/format";

import { scopeLabel } from "./copy";

export type Direction = "gain" | "loss" | "mixed" | "none";

export interface Item {
  name: string;
  /** In After only (added) or in Now only (removed); otherwise kept. */
  change?: "added" | "removed";
}

export interface ComparisonRow {
  key: "status" | "administrator" | "clearance" | "account_scope" | "accounts" | "departments" | "groups" | "account" | "lifecycle";
  rule: string;
  now: Item[];
  after: Item[];
  direction: Direction;
}

const named = (e: AccessEffect, kind: string) => e.subject?.name ?? `A ${kind} that no longer exists`;

function listRow(
  key: "accounts" | "departments" | "groups",
  rule: string,
  current: readonly string[],
  added: readonly string[],
  removed: readonly string[],
): ComparisonRow | null {
  if (!added.length && !removed.length) return null;
  const gone = new Set(removed);
  const listed = new Set(current);
  const now: Item[] = [
    ...current.map((name): Item => (gone.has(name) ? { name, change: "removed" } : { name })),
    // A removal of something no longer listed (it was deleted) still shows as removed.
    ...removed.filter((n) => !listed.has(n)).map((name): Item => ({ name, change: "removed" })),
  ];
  const after: Item[] = [...current.filter((n) => !gone.has(n)).map((name) => ({ name })), ...added.map((name) => ({ name, change: "added" as const }))];
  return { key, rule, now, after, direction: added.length && removed.length ? "mixed" : added.length ? "gain" : "loss" };
}

export function comparisonRows(member: MemberAccess, effects: readonly AccessEffect[]): ComparisonRow[] {
  const rows: ComparisonRow[] = [];
  const of = (kind: AccessEffect["kind"]) => effects.filter((e) => e.kind === kind);
  const m = member.member;

  if (of("suspended").length) rows.push({ key: "status", rule: "Status", now: [{ name: humanize(m.status) }], after: [{ name: "Suspended" }], direction: "loss" });
  if (of("reactivated").length || of("reinstated").length) {
    rows.push({ key: "status", rule: "Status", now: [{ name: humanize(m.status) }], after: [{ name: "Active" }], direction: "gain" });
  }
  for (const e of of("administrator")) {
    const yes = (v: string | null | undefined) => (v === "true" ? "Administrator" : "Not an administrator");
    rows.push({
      key: "administrator",
      rule: "Administrator",
      now: [{ name: yes(e.before ?? String(m.is_admin)) }],
      after: [{ name: yes(e.after) }],
      direction: e.after === "true" ? "gain" : "loss",
    });
  }

  for (const e of of("clearance")) {
    const before = e.before ?? m.clearance;
    const after = e.after ?? before;
    rows.push({
      key: "clearance",
      rule: "Clearance",
      now: [{ name: levelLabel(before) }],
      after: [{ name: levelLabel(after) }],
      direction: after === "confidential" ? "gain" : "loss",
    });
  }
  for (const e of of("account_scope")) {
    const before = e.before ?? m.account_scope;
    const after = e.after ?? before;
    rows.push({
      key: "account_scope",
      rule: "Account scope",
      now: [{ name: scopeLabel(before) }],
      after: [{ name: scopeLabel(after) }],
      direction: after === "all" ? "gain" : "loss",
    });
  }

  const accounts = listRow(
    "accounts",
    "Assigned accounts",
    member.accounts.map((a) => a.name),
    of("account_added").map((e) => named(e, "account")),
    of("account_removed").map((e) => named(e, "account")),
  );
  const departments = listRow(
    "departments",
    "Departments",
    member.departments.map((d) => d.name),
    of("department_added").map((e) => named(e, "department")),
    of("department_removed").map((e) => named(e, "department")),
  );
  const groups = listRow(
    "groups",
    "Groups",
    member.groups.filter((g) => g.origin === "admin").map((g) => g.name),
    of("group_added").map((e) => named(e, "group")),
    of("group_removed").map((e) => named(e, "group")),
  );
  for (const row of [accounts, departments, groups]) if (row) rows.push(row);
  return rows;
}

export const DIRECTION_LABEL: Record<Direction, string> = { gain: "Gains", loss: "Loses", mixed: "Gains and loses", none: "No change" };
