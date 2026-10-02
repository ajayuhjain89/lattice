/**
 * What the member may search, as the API reports it (GET /v1/sources, GET /v1/accounts).
 * Nothing here decides access: it only narrows the choices the UI offers to what the API
 * already says the person can read.
 */
import type { MemberAccounts, MemberSource, SourceType } from "@/api/types";
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

const LISTED = 3;

/**
 * The first-run scope line: "You can search 3 sources · Acme Corp, Northpeak Logistics".
 * Only the person's own sources and accounts appear, as the API listed them.
 */
export function scopeLine(sources: readonly MemberSource[], accounts: MemberAccounts | undefined): string {
  const count = sources.length;
  const head = `You can search ${count} ${count === 1 ? "source" : "sources"}`;
  if (!accounts) return head;
  if (accounts.account_scope === "all") return `${head} · All accounts`;
  const names = accounts.accounts.map((a) => a.name);
  if (names.length === 0) return `${head} · No assigned accounts`;
  if (names.length <= LISTED) return `${head} · ${names.join(", ")}`;
  return `${head} · ${names.slice(0, LISTED).join(", ")} and ${names.length - LISTED} more`;
}
