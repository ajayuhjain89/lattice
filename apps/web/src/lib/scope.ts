/**
 * What the member may search, as the API reports it (GET /v1/sources, GET /v1/accounts).
 * Nothing here decides access: it only narrows the choices the UI offers to what the API
 * already says the person can read.
 */
import type { MemberSource, SourceType } from "@/api/types";
import { SOURCE_TYPES } from "@/api/types";

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
