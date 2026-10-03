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

const TIME = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
const TIME_SECONDS = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZone: "UTC" });

const WEEKDAY_DAY = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** "09:41" (or "09:41:07" with seconds), UTC: the time of day, for rows already grouped under their date. */
export function formatTime(iso: string | null | undefined, { seconds = false }: { seconds?: boolean } = {}): string | null {
  const date = parse(iso);
  return date ? (seconds ? TIME_SECONDS : TIME).format(date) : null;
}

const DAY_MS = 86_400_000;

/**
 * Whole UTC calendar days from `iso` to `now` (0 = the same day, negative = a later day,
 * e.g. a clock ahead of this browser's); null when absent.
 */
export function daysAgo(iso: string | null | undefined, now: number): number | null {
  const date = parse(iso);
  if (!date) return null;
  const start = (t: number) => Math.floor(t / DAY_MS);
  return start(now) - start(date.getTime());
}

/** "Today", "Yesterday", or "Tue 29 Sep 2026": the heading of a day in a chronology. */
export function dayHeading(iso: string, now: number): string {
  const days = daysAgo(iso, now);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  const date = parse(iso);
  return date ? WEEKDAY_DAY.format(date).replace(/Sept/, "Sep").replace(",", "") : iso;
}

/** The UTC date of an ISO timestamp ("2026-09-29"), for grouping by day. */
export function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

/** "today", "yesterday", "5 days ago": an age in calendar days, for reading beside a date. */
export function ageLabel(iso: string | null | undefined, now: number): string | null {
  const days = daysAgo(iso, now);
  if (days === null) return null;
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${days.toLocaleString("en-GB")} days ago`;
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

const SOURCE_TYPE_LABEL: Record<string, string> = {
  doc: "Document",
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
