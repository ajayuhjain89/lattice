import { describe, expect, it } from "vitest";

import type { MemberSource } from "@/api/types";

import { readableSourceTypes, scopeLine } from "./scope";

const source = (connector_type: string, name = connector_type): MemberSource => ({
  id: name,
  name,
  connector_type,
  readable_documents: 1,
  last_synced_at: null,
  last_succeeded_at: null,
  last_run_status: null,
});

describe("readableSourceTypes", () => {
  it("offers only the types the member can read from, in the fixed order", () => {
    expect(readableSourceTypes([source("chat_export"), source("document_repository"), source("chat_export", "b")])).toEqual(["doc", "chat"]);
    expect(readableSourceTypes([source("unknown_connector")])).toEqual([]);
  });
});

describe("scopeLine", () => {
  const two = [source("document_repository"), source("ticketing")];
  const acc = (names: string[], scope: "all" | "assigned" = "assigned") => ({
    account_scope: scope,
    accounts: names.map((n) => ({ key: n.toLowerCase(), name: n })),
  });

  it("names the member's own accounts", () => {
    expect(scopeLine(two, acc(["Acme Corp", "Northpeak Logistics"]))).toBe("You can search 2 sources · Acme Corp, Northpeak Logistics");
  });
  it("summarizes long lists and the all-accounts scope", () => {
    expect(scopeLine(two, acc(["A", "B", "C", "D", "E"]))).toBe("You can search 2 sources · A, B, C and 2 more");
    expect(scopeLine([source("ticketing")], acc(["A"], "all"))).toBe("You can search 1 source · All accounts");
    expect(scopeLine(two, acc([]))).toBe("You can search 2 sources · No assigned accounts");
  });
  it("omits accounts until they are known", () => {
    expect(scopeLine(two, undefined)).toBe("You can search 2 sources");
  });
});
