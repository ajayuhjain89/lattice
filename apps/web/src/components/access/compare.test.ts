import { describe, expect, it } from "vitest";

import { ID, MEMBER_MAYA, PREVIEW_ADD_KESTREL } from "@/test/fixtures";

import { comparisonRows } from "./compare";

describe("comparisonRows", () => {
  it("reads an added account as a gain, keeping what stays", () => {
    expect(comparisonRows(MEMBER_MAYA, PREVIEW_ADD_KESTREL.effects)).toEqual([
      {
        key: "accounts",
        rule: "Assigned accounts",
        now: [{ name: "Acme Corp" }, { name: "Northpeak Logistics" }],
        after: [{ name: "Acme Corp" }, { name: "Northpeak Logistics" }, { name: "Kestrel Bank", change: "added" }],
        direction: "gain",
      },
    ]);
  });

  it("reads lowered clearance, a narrowed scope, suspension and mixed lists from the effects alone", () => {
    const rows = comparisonRows(MEMBER_MAYA, [
      { kind: "clearance", before: "confidential", after: "internal" },
      { kind: "account_scope", before: "all", after: "assigned" },
      { kind: "department_removed", subject: { id: ID.sales, name: "Sales" } },
      { kind: "department_added", subject: { id: ID.leadership, name: "Leadership" } },
      { kind: "suspended" },
    ]);
    expect(rows.map((r) => [r.rule, r.direction])).toEqual([
      ["Status", "loss"],
      ["Clearance", "loss"],
      ["Account scope", "loss"],
      ["Departments", "mixed"],
    ]);
    expect(rows[3]!.now).toEqual([{ name: "Sales", change: "removed" }]);
    expect(rows[3]!.after).toEqual([{ name: "Leadership", change: "added" }]);
  });

  it("never names more than the effect does", () => {
    const rows = comparisonRows(MEMBER_MAYA, [{ kind: "group_removed", subject: { id: ID.exec, name: null } }]);
    expect(rows[0]!.now).toEqual([{ name: "Exec staff" }, { name: "A group that no longer exists", change: "removed" }]);
    expect(rows[0]!.after).toEqual([{ name: "Exec staff" }]);
    expect(rows[0]!.direction).toBe("loss");
  });
});
