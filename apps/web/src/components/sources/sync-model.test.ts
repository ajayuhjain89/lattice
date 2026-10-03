import { describe, expect, it } from "vitest";

import type { SyncJob } from "@/api/types";
import { SOURCES_ADMIN } from "@/test/fixtures";

import { failedCounts, isOld, readRun, registerSummary, requestMessage, sourceFailed } from "./sync-model";

const NOW = Date.parse("2026-09-29T10:00:00Z");

describe("sync model", () => {
  it("reads only a failed or interrupted run as a fault", () => {
    expect(readRun("succeeded")).toMatchObject({ label: "Synced", tone: "healthy" });
    expect(readRun("partial")).toMatchObject({ label: "Partial", tone: "neutral" });
    expect(readRun("failed")).toMatchObject({ label: "Failed", tone: "fault" });
    expect(readRun("aborted")).toMatchObject({ label: "Interrupted", tone: "fault" });
    expect(readRun(null)).toMatchObject({ label: "Never synced", tone: "neutral" });
  });

  it("calls a sync old only after a week", () => {
    expect(isOld("2026-09-22T10:00:00Z", NOW)).toBe(false);
    expect(isOld("2026-09-21T06:00:02Z", NOW)).toBe(true);
    expect(isOld(null, NOW)).toBe(false);
  });

  it("separates record failures from a failed source", () => {
    const [drive, support] = SOURCES_ADMIN.sources;
    expect(failedCounts(support!)).toEqual({ records: 1, embeddings: 0 });
    expect(sourceFailed(support!)).toBe(false);
    expect(sourceFailed({ ...drive!, last_run: { ...drive!.last_run!, status: "failed", error_code: "source_unavailable:timeout" } })).toBe(true);
  });

  it("summarises the register from the listing alone", () => {
    expect(registerSummary(SOURCES_ADMIN.sources, NOW)).toBe(
      "3 sources: 1 syncing now · 1 with failed records · 1 last fully synced over a week ago · 1 never synced.",
    );
    expect(registerSummary([SOURCES_ADMIN.sources[0]!].map((s) => ({ ...s, job: null })), NOW)).toBe("1 source, all synced.");
  });
});

describe("requested sync", () => {
  const [, , slack] = SOURCES_ADMIN.sources;
  const job: SyncJob = { ...SOURCES_ADMIN.sources[0]!.job!, id: "j-new", source_id: slack!.id, status: "queued", created_at: "2026-09-29T09:10:00Z" };
  const request = { sourceId: slack!.id, jobId: "j-new", name: "Halden Slack", createdAt: job.created_at };
  const listing = (j: SyncJob | null, run = slack!.last_run) => [{ ...slack!, job: j, last_run: run }];

  it("stays queued until the listing shows the job, then follows it", () => {
    expect(requestMessage(request, listing(null))).toBe("Sync requested for Halden Slack. It is queued.");
    expect(requestMessage(request, listing({ ...job, status: "running" }))).toBe("Halden Slack is syncing.");
    expect(requestMessage(request, listing({ ...job, status: "failed", error_code: "source_unavailable:timeout" }))).toBe(
      "Halden Slack: the requested sync failed (source_unavailable:timeout).",
    );
    const done = SOURCES_ADMIN.sources[0]!.last_run;
    expect(requestMessage(request, listing({ ...job, status: "succeeded" }, done))).toBe("Halden Slack finished syncing: Synced.");
    expect(requestMessage(request, listing({ ...job, id: "j-later", created_at: "2026-09-29T09:30:00Z" }, done))).toBe("Halden Slack finished syncing: Synced.");
  });
});
