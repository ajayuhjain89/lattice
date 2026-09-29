/**
 * Display formatting. The API emits ISO-8601 UTC; dates are shown in UTC so a source's
 * day never shifts with the viewer's time zone, and every render is deterministic.
 */

const DAY = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

const DAY_TIME = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "14 Aug 2026" (UTC), or null when absent/invalid. */
export function formatDay(iso: string | null | undefined): string | null {
  const date = parse(iso);
  return date ? DAY.format(date).replace(/Sept/, "Sep") : null;
}

/** "14 Aug 2026, 09:41:07 UTC". */
export function formatDayTime(iso: string | null | undefined): string | null {
  const date = parse(iso);
  return date ? `${DAY_TIME.format(date).replace(/Sept/, "Sep")} UTC` : null;
}

/** 840 → "840 ms"; 4213 → "4.2 s"; 61000 → "1 min 1 s". */
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes} min ${seconds} s`;
}

export function formatCount(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "3f2a9c1e…" -- a readable prefix of an opaque identifier (the full value stays available). */
export function shortId(id: string, length = 8): string {
  return id.length > length ? `${id.slice(0, length)}…` : id;
}

const SOURCE_TYPE_LABEL: Record<string, string> = {
  doc: "Doc",
  ticket: "Ticket",
  chat: "Chat",
};

export function sourceTypeLabel(sourceType: string): string {
  return SOURCE_TYPE_LABEL[sourceType] ?? sourceType;
}

const KIND_LABEL: Record<string, string> = {
  doc: "Document",
  ticket: "Ticket",
  chat_thread: "Chat thread",
  chat_channel_day: "Channel messages (one day)",
};

export function documentKindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? humanize(kind);
}

const AUTHORITY_LABEL: Record<string, string> = {
  system_of_record: "System of record",
  working: "Working document",
  conversational: "Conversation",
};

export function authorityLabel(authority: string): string {
  return AUTHORITY_LABEL[authority] ?? humanize(authority);
}

export function levelLabel(level: string): string {
  return humanize(level);
}

/** "provider_data_use" → "Provider data use". Codes are safe identifiers, never content. */
export function humanize(code: string): string {
  const text = code.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Only http(s) links are ever rendered as links. */
export function safeHttpUrl(uri: string | null | undefined): string | null {
  if (!uri) return null;
  try {
    const url = new URL(uri);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string): boolean {
  return UUID.test(value);
}

const TRUST_CLASS: Record<string, string> = {
  local: "Local",
  organization_controlled: "Organization-controlled",
  external_api: "External API",
  unknown: "Unknown",
};

/** Deployment trust classes (D-15). */
export function trustClassLabel(deployment: string): string {
  return TRUST_CLASS[deployment] ?? humanize(deployment);
}

const RETENTION: Record<string, string> = {
  none: "No retention",
  zero: "Zero retention",
  provider_default: "Provider default retention",
};

export function retentionLabel(mode: string): string {
  return RETENTION[mode] ?? `${humanize(mode)} retention`;
}

const CONNECTOR: Record<string, string> = {
  document_repository: "Documents",
  ticketing: "Tickets",
  chat_export: "Chat",
};

/** Source connector types (ingestion-design §1). */
export function connectorLabel(connectorType: string): string {
  return CONNECTOR[connectorType] ?? humanize(connectorType);
}

const SYNC_STATUS: Record<string, string> = {
  succeeded: "Synced",
  partial: "Partial",
  failed: "Failed",
  aborted: "Interrupted",
  running: "Syncing",
  queued: "Queued",
};

/** A sync run's or requested job's status, as the Sources page shows it. */
export function syncStatusLabel(status: string | null | undefined): string {
  if (!status) return "Never synced";
  return SYNC_STATUS[status] ?? humanize(status);
}
