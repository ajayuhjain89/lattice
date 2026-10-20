import { describe, expect, it } from "vitest";

import { ApiError } from "@/api/errors";

import { blockerSentence, EXPORT_FORMATS, formatBytes, limitsSentence, nextStep, rejectSentence, sourceError } from "./source-copy";

const refusal = (status: number, field: string, issue: string) =>
  new ApiError({ kind: "http", status, code: null, message: "server words", requestId: "r", issues: [issue], fields: [field] });

describe("source copy", () => {
  it("names exactly the three export formats, never a live integration", () => {
    expect(EXPORT_FORMATS.map((f) => f.type)).toEqual(["document_repository", "ticketing", "chat_export"]);
    for (const f of EXPORT_FORMATS) expect(`${f.title} ${f.summary} ${f.structure.join(" ")}`).not.toMatch(/connect|sign in|OAuth|live|API/i);
    expect(EXPORT_FORMATS[1]!.structure.join(" ")).toContain("not a native help-desk export");
  });

  it("formats sizes in binary units and the limits in one sentence", () => {
    expect(formatBytes(512)).toBe("512 bytes");
    expect(formatBytes(1_843_200)).toBe("1.8 MiB");
    expect(formatBytes(100 * 1024 * 1024)).toBe("100 MiB");
    expect(formatBytes(2 * 1024 ** 3)).toBe("2 GiB");
    expect(limitsSentence({ available: true, formats: ["zip"], max_upload_bytes: 100 * 1024 ** 2, max_expanded_bytes: 512 * 1024 ** 2, max_entries: 20_000, max_file_bytes: 32 * 1024 ** 2 })).toBe(
      "Up to 100 MiB per upload, 512 MiB once expanded, 20,000 entries and 32 MiB per file.",
    );
  });

  it("words every next action the server can send, and nothing for none", () => {
    for (const action of ["upload_import", "replace_import", "wait", "map_references", "start_sync", "retry_sync", "resume"] as const) {
      expect(nextStep(action)?.label).toBeTruthy();
    }
    expect(nextStep("none")).toBeNull();
    expect(nextStep("map_references", 3)?.sentence).toContain("3 account references have no account yet");
  });

  it("explains blockers and every refusal code without echoing server text", () => {
    expect(blockerSentence(null)).toBeNull();
    expect(blockerSentence("source_paused")).toContain("resume");
    for (const code of ["archive_invalid", "too_large", "too_many_entries", "ratio_exceeded", "unsafe_path", "link_refused", "duplicate_name", "unsupported_layout", "upload_incomplete", "upload_timeout", "storage_full", "upload_abandoned", "upload_failed", "storage_unavailable"]) {
      expect(rejectSentence(code)).not.toBe("The upload was refused.");
    }
    expect(sourceError(refusal(422, "import", "link_refused"))).toBe(rejectSentence("link_refused"));
    expect(sourceError(refusal(413, "import", "too_large"))).toBe(rejectSentence("too_large"));
    expect(sourceError(refusal(503, "import", "storage_unavailable"))).toBe(rejectSentence("storage_unavailable"));
    expect(sourceError(refusal(409, "version", "source_changed"), "Halden Escalations")).toContain("Someone changed Halden Escalations");
    expect(sourceError(refusal(409, "source", "sync_running"))).toContain("A sync of this source is running");
    expect(sourceError(refusal(422, "change", "widening_not_acknowledged"))).toContain("more people");
    expect(sourceError(refusal(422, "source", "confirmation_mismatch"))).toContain("doesn't match");
    expect(sourceError(refusal(422, "source", "source_not_active"))).toContain("paused");
    expect(sourceError(refusal(422, "x", "something_new"))).not.toContain("server words");
  });
});
