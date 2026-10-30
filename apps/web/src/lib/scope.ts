/**
 * What the member may search, as the API reports it (GET /v1/sources, GET /v1/accounts).
 * Nothing here decides access: it only narrows the choices the UI offers to what the API
 * already says the person can read.
 */
import type { MemberSource, SourceType } from "@/api/types";
import { SOURCE_TYPES } from "@/api/types";
import { formatDay } from "@/lib/format";

const SOURCE_TYPE_OF: Record<string, SourceType> = {
  document_repository: "doc",
  ticketing: "ticket",
  chat_export: "chat",
};

/** Source types with at least one source the member can read from, in the fixed order. */
export function readableSourceTypes(sources: readonly MemberSource[]): SourceType[] {
  const found = new Set(sources.map((s) => SOURCE_TYPE_OF[s.connector_type]).filter(Boolean));
  return SOURCE_TYPES.filter((t) => found.has(t));
}

/**
 * When a source was last synced, for the member's reach. A partial run (some records
 * refused, the rest indexed) still synced: it must not read as "not synced yet".
 */
export function syncLabel(source: MemberSource): string {
  const succeeded = formatDay(source.last_succeeded_at);
  if (succeeded) return `synced ${succeeded}`;
  const attempted = formatDay(source.last_synced_at);
  if (!attempted) return "not synced yet";
  return source.last_run_status === "partial" ? `partly synced ${attempted}` : `last attempt ${attempted}`;
}
