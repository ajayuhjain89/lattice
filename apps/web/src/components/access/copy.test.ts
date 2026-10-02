import { describe, expect, it } from "vitest";

import type { MemberAccess } from "@/api/types";
import { AUDIT, MEMBER_MAYA } from "@/test/fixtures";

import { accessSummary, actorLabel, auditPhrase, decidingReasons, groupOrigin, previewSentence, type Part } from "./copy";

const text = (sentences: Part[][]) => sentences.map((s) => s.map((p) => (typeof p === "string" ? p : p.strong)).join("")).join(" ");

describe("accessSummary", () => {
  it("describes assigned accounts, clearance, departments, visible groups and grants", () => {
    expect(text(accessSummary(MEMBER_MAYA))).toBe(
      "Maya Okafor can see Acme Corp and Northpeak Logistics content (assigned accounts), up to Internal sensitivity, " +
        "including content restricted to Sales, plus what's shared with Exec staff and 1 document granted directly. " +
        "Maya Okafor is also in private channels you aren't in. They aren't named, listed or counted here. " +
        "Maya Okafor also has direct access to documents you can't read. They aren't listed or counted here.",
    );
  });

  it("acknowledges hidden channels and grants without a count", () => {
    const summary = text(accessSummary(MEMBER_MAYA));
    expect(summary).not.toContain("rec_");
    expect(summary).not.toMatch(/\d+ private channels?/);
    expect(summary).not.toMatch(/\d+ documents? you can't read/);
    const none = text(accessSummary({ ...MEMBER_MAYA, has_hidden_groups: false, has_other_grants: false }));
    expect(none).not.toContain("private channels");
  });

  it("covers every scope and the inactive states", () => {
    const all: MemberAccess = { ...MEMBER_MAYA, member: { ...MEMBER_MAYA.member, account_scope: "all" }, groups: [], grants: [], departments: [], has_hidden_groups: false, has_other_grants: false };
    expect(text(accessSummary(all))).toBe("Maya Okafor can see content for every account, up to Internal sensitivity.");
    const none: MemberAccess = { ...all, member: { ...all.member, account_scope: "assigned" }, accounts: [] };
    expect(text(accessSummary(none))).toBe("Maya Okafor can see content that isn't tied to an account, up to Internal sensitivity.");
    const suspended: MemberAccess = { ...MEMBER_MAYA, member: { ...MEMBER_MAYA.member, status: "suspended" } };
    expect(text(accessSummary(suspended))).toBe("Maya Okafor is suspended and can't read anything in Lattice.");
  });
});

describe("change copy", () => {
  it("states previews as rules, never documents", () => {
    expect(previewSentence({ kind: "account_removed", subject: { id: "x", name: "Kestrel Bank" } }, "Maya")).toBe(
      "Maya will lose access to all Kestrel Bank content, except documents granted directly.",
    );
    expect(previewSentence({ kind: "suspended" }, "Maya")).toBe("Maya will immediately lose access to everything in Lattice.");
    expect(previewSentence({ kind: "group_added", subject: { id: "x", name: null } }, "Maya")).toBe(
      "Maya will be able to read what's shared with a group that no longer exists.",
    );
  });

  it("summarizes recorded changes and their actors", () => {
    expect(auditPhrase({ kind: "clearance", before: "internal", after: "confidential" })).toBe("Clearance Internal → Confidential");
    expect(auditPhrase({ kind: "clearance", before: null, after: "internal" })).toBe("Clearance Internal");
    expect(auditPhrase({ kind: "administrator", before: "false", after: "true" })).toBe("Made an administrator");
    expect(actorLabel(AUDIT.items[0]!)).toBe("Lena Brandt");
    expect(actorLabel(AUDIT.items[1]!)).toBe("admin-grants.yaml");
    expect(actorLabel(AUDIT.items[2]!)).toBe("Your identity provider (SCIM)");
    expect(actorLabel({ ...AUDIT.items[0]!, actor: { kind: "member", name: null, operator: null } })).toBe("A former member");
  });

  it("says where a visible group comes from", () => {
    expect(groupOrigin(MEMBER_MAYA.groups[0]!)).toBe("Administrator-defined");
    expect(groupOrigin({ ...MEMBER_MAYA.groups[0]!, origin: "source", connector_type: "chat_export" })).toBe("Synced from Chat · you're a member");
  });

  it("keeps only the blocking reasons for a document the member can't read", () => {
    const reasons = ["clearance_covers_sensitivity", "not_account_specific", "department_not_matched"];
    expect(decidingReasons(reasons, false)).toEqual(["department_not_matched"]);
    expect(decidingReasons(reasons, true)).toEqual(reasons);
  });
});
