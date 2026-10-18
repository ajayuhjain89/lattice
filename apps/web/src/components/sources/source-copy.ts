/**
 * Words for export sources operated in the product (Phase 12, P12.6). Every rule here is
 * the backend's: the supported formats are the three connectors' own (connector-design
 * §3), the next action and the reason a sync is blocked come from the server, refusals are
 * its fixed issue codes. Nothing here is inferred from content: there is none to infer from.
 */
import { isApiError, retryPhrase } from "@/api/errors";
import type { ConnectorType, NextAction, SourceDetail, UploadLimits } from "@/api/types";

export interface ExportFormat {
  type: ConnectorType;
  /** The source type as a choice: what it is, and that it is an export. */
  title: string;
  summary: string;
  /** What the upload must contain, as the connector reads it. */
  structure: readonly string[];
  /** Defaults for a new source of this type (the administrator can change them). */
  authority: "system_of_record" | "working" | "conversational";
  accountScoped: boolean;
}

export const EXPORT_FORMATS: readonly ExportFormat[] = [
  {
    type: "document_repository",
    title: "Documents — Markdown export",
    summary: "Markdown files with Lattice front matter (title, sensitivity, departments, account, grants).",
    structure: [
      "A zip archive of .md files, in folders or not.",
      "Each file starts with Lattice front matter; its access comes from there.",
      "Other files (images, attachments) are ignored.",
    ],
    authority: "working",
    accountScoped: false,
  },
  {
    type: "ticketing",
    title: "Tickets — JSON Lines export",
    summary: "Lattice's ticket interchange: one ticket per line, with its organization ID and access fields.",
    structure: [
      "One .jsonl file, or a zip archive of .jsonl files at its top level.",
      "One ticket per line in the Lattice ticket format; not a native help-desk export.",
      "Each ticket's organization ID is mapped to an account in Access.",
    ],
    authority: "working",
    accountScoped: true,
  },
  {
    type: "chat_export",
    title: "Slack workspace export",
    summary: "A Slack workspace export archive, uploaded as it was downloaded.",
    structure: [
      "The zip archive Slack produces: users.json and channels.json at its root (groups.json for private channels).",
      "One folder per channel, holding one .json file per day.",
      "Channels are identified by ID; a private channel stays private.",
    ],
    authority: "conversational",
    accountScoped: false,
  },
];

export function exportFormat(type: string): ExportFormat {
  return EXPORT_FORMATS.find((f) => f.type === type) ?? EXPORT_FORMATS[0]!;
}

/** "100 MiB", "512 MiB", "2 GiB" -- binary units, as the limits are configured. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  const units = ["bytes", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  if (unit === 0) return `${bytes.toLocaleString("en-GB")} ${bytes === 1 ? "byte" : "bytes"}`;
  const shown = value >= 100 || Number.isInteger(value) ? Math.round(value).toLocaleString("en-GB") : value.toFixed(1);
  return `${shown} ${units[unit]}`;
}

/** The deployment's limits, in one sentence. */
export function limitsSentence(limits: UploadLimits): string {
  return (
    `Up to ${formatBytes(limits.max_upload_bytes)} per upload, ${formatBytes(limits.max_expanded_bytes)} once expanded, ` +
    `${limits.max_entries.toLocaleString("en-GB")} entries and ${formatBytes(limits.max_file_bytes)} per file.`
  );
}

export interface NextStep {
  /** A short label for the register's cell and the record's band. */
  label: string;
  /** One sentence on the record. */
  sentence: string;
}

/** The server's next action in words. `null`: nothing to do. */
export function nextStep(action: NextAction, references = 0): NextStep | null {
  switch (action) {
    case "upload_import":
      return { label: "Upload an export", sentence: "Upload an export to give this source something to sync." };
    case "replace_import":
      return { label: "Upload a new export", sentence: "The current export can't be synced as a whole. Upload a corrected one." };
    case "wait":
      return { label: "In progress", sentence: "Lattice is checking or syncing this source. This page updates when it finishes." };
    case "map_references":
      return {
        label: "Map account references",
        sentence:
          references > 0
            ? `${references === 1 ? "One account reference has" : `${references.toLocaleString("en-GB")} account references have`} no account yet. Their records stay unindexed until each is mapped in Access.`
            : "Some account references have no account yet. Their records stay unindexed until each is mapped in Access.",
      };
    case "start_sync":
      return { label: "Start the first sync", sentence: "The export is ready. Sync it to index its records under their own access rules." };
    case "retry_sync":
      return { label: "Retry the sync", sentence: "The latest sync failed. Retry it once the cause is fixed." };
    case "resume":
      return { label: "Resume", sentence: "This source is paused: nothing syncs until it's resumed." };
    case "none":
      return null;
  }
}

/** Why Sync now is not available, from the server's `sync_blocked_by`. */
export function blockerSentence(code: string | null): string | null {
  switch (code) {
    case null:
      return null;
    case "source_paused":
      return "Paused: resume the source to sync it.";
    case "job_active":
      return "A check of the export is running; you can sync when it finishes.";
    case "import_required":
      return "Upload an export first.";
    case "import_invalid":
      return "The current export can't be synced. Upload a corrected one.";
    default:
      return "Syncing isn't available right now.";
  }
}

/** What a refused upload's code means (the backend's fixed codes, C8.3). */
export function rejectSentence(code: string): string {
  switch (code) {
    case "archive_invalid":
      return "The file isn't a readable zip archive (or uses encryption or an unsupported compression method). A Tickets source also takes a single .jsonl file.";
    case "too_large":
      return "The upload, its expanded contents or one of its files is over this deployment's limit.";
    case "too_many_entries":
      return "The archive has more entries than this deployment allows.";
    case "ratio_exceeded":
      return "An entry expands far more than real exports do, so the archive was refused as a possible compression bomb.";
    case "unsafe_path":
      return "An entry has an unsafe or invalid path (absolute, a drive letter, \"..\", a backslash or too deep).";
    case "link_refused":
      return "The archive contains a link or a special file. Exports never need them.";
    case "duplicate_name":
      return "Two entries have the same path (also ignoring letter case or Unicode form).";
    case "unsupported_layout":
      return "The archive doesn't have the structure this source type reads.";
    case "upload_incomplete":
      return "The upload ended before the whole file arrived.";
    case "upload_timeout":
      return "The upload took longer than this deployment allows.";
    case "storage_full":
      return "Lattice can't store this upload right now. Ask your operator, or remove other sources' old exports.";
    case "upload_abandoned":
      return "The upload was interrupted and never finished.";
    case "upload_failed":
      return "Lattice couldn't finish receiving the upload. Try again.";
    case "storage_unavailable":
      return "This deployment has no place to store uploads. Your operator needs to configure it.";
    default:
      return "The upload was refused.";
  }
}

/** A whole-import check failure (the check read nothing). */
export function checkErrorSentence(code: string): string {
  if (code.startsWith("source_unavailable")) return "The export is missing something this source type needs, so nothing in it can be synced.";
  return "The export can't be synced as a whole.";
}

export function importStatusLabel(status: string): string {
  return (
    {
      receiving: "Receiving",
      validating: "Validating",
      ready: "Ready",
      rejected: "Refused",
      superseded: "Replaced",
      deleted: "Removed",
    }[status] ?? status
  );
}

export function managedLabel(source: Pick<SourceDetail, "managed_by">): string {
  return source.managed_by === "organization" ? "Managed in Lattice" : "Managed by an operator";
}

/** What to tell an administrator when a source change is refused. */
export function sourceError(error: unknown, name = "this source"): string {
  if (!isApiError(error)) return "Something went wrong. Try again.";
  if (error.kind === "network") return error.message;
  const issue = (code: string) => error.issues.includes(code);
  if (error.status === 409) {
    if (error.fields.includes("key")) return "Another source already uses this key.";
    if (issue("sync_running")) return "A sync of this source is running. Try again when it finishes.";
    if (issue("upload_in_progress")) return "An upload to this source is already being received.";
    if (issue("job_active")) return "A check or sync of this source is already queued or running.";
    return `Someone changed ${name} after you opened this. Close this, review the latest, and try again.`;
  }
  if (error.status === 403) return "You no longer have administrator access.";
  if (error.status === 404) return "This source is no longer available. Reload the page.";
  if (error.isRateLimited) return `You're making changes quickly. Try again ${retryPhrase(error.retryAfter)}.`;
  if (error.status === 413) return rejectSentence("too_large");
  if (error.status === 503 && error.fields.includes("import")) return rejectSentence(error.issues[0] ?? "storage_unavailable");
  if (error.status === 422) {
    if (error.fields.includes("import")) return rejectSentence(error.issues[0] ?? "");
    if (issue("operator_managed")) return `${name} is managed by an operator's sources file: change it there.`;
    if (issue("source_paused") || issue("source_not_active")) return `${name} is paused. Resume it first.`;
    if (issue("source_not_paused")) return `${name} isn't paused.`;
    if (issue("import_required")) return "Upload an export first.";
    if (issue("import_invalid")) return "The current export can't be synced. Upload a corrected one.";
    if (issue("widening_not_acknowledged")) return "This change lets more people read records. Confirm that you've reviewed who gains access.";
    if (issue("confirmation_mismatch")) return "The key you typed doesn't match this source's key.";
    if (issue("no_change")) return "This doesn't change anything.";
    if (issue("unknown_department")) return "One of those departments doesn't exist. Use department keys exactly as Access shows them.";
    if (issue("channels_invalid")) return "Channel IDs are uppercase letters and digits, as the export names them.";
    if (issue("key_invalid")) return "Use lowercase letters, digits and hyphens, starting with a letter or digit.";
    return "Lattice couldn't apply this. Check each field and try again.";
  }
  return error.message;
}
