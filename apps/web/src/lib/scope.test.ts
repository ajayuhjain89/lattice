import { describe, expect, it } from "vitest";

import type { MemberSource } from "@/api/types";

import { readableSourceTypes } from "./scope";

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
