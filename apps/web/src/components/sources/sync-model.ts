/**
 * How the Sources register reads the API's sync facts (GET /v1/sources, GET
 * /v1/admin/sources). Everything here is a reading of fields the API returns -- a run's
 * status, its counts and codes, three timestamps and the requested job. Nothing is
 * scored, estimated or projected: there is no uptime, freshness score or progress.
 */
import { AlertTriangle, CheckCircle2, CircleDashed, XCircle, type LucideIcon } from "lucide-react";

import type { AdminSource, SyncJob } from "@/api/types";
import type { StatusTone } from "@/components/primitives/StatusMark";
import { SOURCE_GLYPHS } from "@/components/primitives/glyphs";
import { daysAgo } from "@/lib/format";

const TYPE_OF: Record<string, keyof typeof SOURCE_GLYPHS> = { document_repository: "doc", ticketing: "ticket", chat_export: "chat" };

/** The source type of a connector, which picks its glyph: `SOURCE_GLYPHS[sourceKind(c)]`. */
export function sourceKind(connectorType: string): keyof typeof SOURCE_GLYPHS {
  return TYPE_OF[connectorType] ?? "doc";
}

/**
 * A sync older than this many days is read as old (design-direction §6.6: ink under a
 * week, ochre after). It is a reading rule for one timestamp, not a health metric.
 */
export const OLD_AFTER_DAYS = 7;

export function isOld(iso: string | null | undefined, now: number): boolean {
  const days = daysAgo(iso, now);
  return days !== null && days > OLD_AFTER_DAYS;
}

export interface RunReading {
  label: string;
  tone: StatusTone;
  icon: LucideIcon;
}

/**
 * A run's status as words and a signal. Only a failed or interrupted run is a fault: a
 * partial run synced the source but some records failed -- it is shown with a warning
 * icon and its count, not in red.
 */
export function readRun(status: string | null | undefined): RunReading {
  switch (status) {
    case "succeeded":
      return { label: "Synced", tone: "healthy", icon: CheckCircle2 };
    case "partial":
      return { label: "Partial", tone: "neutral", icon: AlertTriangle };
    case "failed":
      return { label: "Failed", tone: "fault", icon: XCircle };
    case "aborted":
      return { label: "Interrupted", tone: "fault", icon: XCircle };
    case null:
    case undefined:
      return { label: "Never synced", tone: "neutral", icon: CircleDashed };
    default:
      return { label: status.replace(/[_-]+/g, " "), tone: "neutral", icon: CircleDashed };
  }
}

export const jobActive = (job: SyncJob | null | undefined) => job?.status === "queued" || job?.status === "running";

/** Records that failed in the last run, and chunks whose embedding failed (both counts). */
export function failedCounts(s: AdminSource): { records: number; embeddings: number } {
  return { records: s.last_run?.counts["failed"] ?? 0, embeddings: s.last_run?.counts["embedding_failures"] ?? 0 };
}

/** A source-level failure: the run itself failed or was interrupted (not a record). */
export const sourceFailed = (s: AdminSource) => s.last_run?.status === "failed" || s.last_run?.status === "aborted";

/** The register's headline: only states that hold for at least one source, in this order. */
export function registerSummary(sources: readonly AdminSource[], now: number): string {
  const count = (n: number, one: string, many: string) => `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;
  const clauses: string[] = [];
  const syncing = sources.filter((s) => jobActive(s.job)).length;
  const failed = sources.filter(sourceFailed).length;
  const records = sources.filter((s) => !sourceFailed(s) && failedCounts(s).records + failedCounts(s).embeddings > 0).length;
  const old = sources.filter((s) => s.last_succeeded_at && isOld(s.last_succeeded_at, now)).length;
  const never = sources.filter((s) => !s.last_run).length;
  if (syncing) clauses.push(`${syncing.toLocaleString("en-GB")} syncing now`);
  if (failed) clauses.push(`${failed.toLocaleString("en-GB")} whose last sync failed`);
  if (records) clauses.push(`${records.toLocaleString("en-GB")} with failed records`);
  if (old) clauses.push(`${old.toLocaleString("en-GB")} last fully synced over a week ago`);
  if (never) clauses.push(`${never.toLocaleString("en-GB")} never synced`);
  const head = count(sources.length, "source", "sources");
  return clauses.length ? `${head}: ${clauses.join(" · ")}.` : `${head}, all synced.`;
}

export interface SyncRequest {
  sourceId: string;
  jobId: string;
  name: string;
  /** When the job was created: an older job in the listing means it hasn't refreshed yet. */
  createdAt: string;
}

/**
 * Where the sync requested last on this page stands, read from the polled listing. Until
 * the listing shows that job (or a later one), it is still the request just accepted.
 */
export function requestState(request: SyncRequest | undefined, sources: readonly AdminSource[] | undefined): SyncJob["status"] | "superseded" | null {
  if (!request) return null;
  const job = sources?.find((x) => x.id === request.sourceId)?.job;
  if (job?.id === request.jobId) return job.status;
  return job && job.created_at > request.createdAt ? "superseded" : "queued";
}

/** What to announce about that request: exactly what the register now shows for it. */
export function requestMessage(request: SyncRequest | undefined, sources: readonly AdminSource[] | undefined): string {
  if (!request) return "";
  const s = sources?.find((x) => x.id === request.sourceId);
  const outcome = readRun(s?.last_run?.status).label;
  switch (requestState(request, sources)) {
    case "queued":
      return `Sync requested for ${request.name}. It is queued.`;
    case "running":
      return `${request.name} is syncing.`;
    case "failed":
      return `${request.name}: the requested sync failed${s?.job?.error_code ? ` (${s.job.error_code})` : ""}.`;
    case "succeeded":
    case "superseded":
      return `${request.name} finished syncing: ${outcome}.`;
    default:
      return "";
  }
}
