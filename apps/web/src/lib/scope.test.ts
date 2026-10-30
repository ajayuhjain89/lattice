import { describe, expect, it } from "vitest";

import type { MemberSource } from "@/api/types";

import { readableSourceTypes, syncLabel } from "./scope";

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

describe("syncLabel", () => {
  const at = "2026-10-30T11:49:00Z";
  it("names the last successful sync, else a partial or other attempt, else none", () => {
    expect(syncLabel({ ...source("ticketing"), last_succeeded_at: at, last_synced_at: at })).toMatch(/^synced /);
    expect(syncLabel({ ...source("ticketing"), last_synced_at: at, last_run_status: "partial" })).toMatch(/^partly synced /);
    expect(syncLabel({ ...source("ticketing"), last_synced_at: at, last_run_status: "failed" })).toMatch(/^last attempt /);
    expect(syncLabel(source("ticketing"))).toBe("not synced yet");
  });
});
