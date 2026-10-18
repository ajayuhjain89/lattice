"use client";

import { FileUp, Link2, Pause, ServerCog, Pencil, Play, RefreshCw, ScanSearch, Trash2, Upload, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { isApiError } from "@/api/errors";
import { sourceBusy, useAdminSources, useCheckImport, useRequestSync, useSource, useUploadSettled } from "@/api/queries";
import type { AdminSource, ImportCheck, SourceDetail, SourceImport, SourceJob, UnresolvedReference, UploadResult } from "@/api/types";
import { AppliedLine, type AppliedChange } from "@/components/access/MemberScreen";
import { Button, ButtonLink } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { SOURCE_GLYPHS } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { StatusMark } from "@/components/primitives/StatusMark";
import { useToast } from "@/components/primitives/Toast";
import { AdminGate, OpsPage, OpsSection } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { NotAvailable } from "@/components/states/NotAvailable";
import { authorityLabel, formatCount, formatDay, formatDayTime, formatTime, isUuid, levelLabel } from "@/lib/format";

import { DisconnectDialog, EditSourceDialog, LifecycleDialog, scopingLabel } from "./SourceDialogs";
import styles from "./SourceRecord.module.css";
import { blockerSentence, checkErrorSentence, exportFormat, formatBytes, importStatusLabel, limitsSentence, managedLabel, nextStep, rejectSentence, sourceError } from "./source-copy";
import { Code, FailureRecords } from "./SourcesScreen";
import { readRun, sourceKind } from "./sync-model";
import { uploadExport } from "./upload";

/**
 * One source's durable record (GET /v1/admin/sources/{id}; Phase 12, P12.6): its settings,
 * its current export and that export's check, its sync, the account references it is
 * waiting on, and the operations that change it -- each one the backend's, previewed where
 * the backend previews, given a reason and audited. Operational facts only: counts, codes,
 * sizes, times and IDs. Never a document, a ticket, a message, a file name from an export
 * or a private channel's name -- configuring a source is not permission to read it.
 */
export function SourceScreen({ id }: { id: string }) {
  const valid = isUuid(id);
  const router = useRouter();
  // Once disconnected the record is gone: stop asking for it while the page moves on.
  const [gone, setGone] = useState(false);
  const { isAdmin } = useWorkspace();
  const query = useSource(id, valid && isAdmin && !gone);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const missing = !valid || (query.isError && isApiError(query.error) && query.error.isNotFound);
  const name = query.data?.settings.name;

  return (
    <OpsPage
      trail={[
        { label: "Sources", href: "/sources" },
        { label: name ?? "Source" },
      ]}
    >
      <AdminGate forbidden={forbidden} body="Source administration is available to your organization's administrators. Configuring a source never gives access to its content.">
        {missing ? (
          <NotAvailable label="Not available" title="This source isn't available" body="The link may be wrong, or the source was disconnected." action={{ href: "/sources", label: "Back to Sources" }} />
        ) : query.isError ? (
          <ErrorState error={query.error} title="Couldn't load this source" onRetry={() => query.refetch()} retrying={query.isFetching} headingLevel="h1" />
        ) : !query.data ? (
          <RuleSkeleton variant="ledger" rows={5} label="Loading this source" />
        ) : (
          <Source
            source={query.data}
            onDisconnected={() => {
              setGone(true);
              router.push("/sources");
            }}
          />
        )}
      </AdminGate>
    </OpsPage>
  );
}

function Source({ source, onDisconnected }: { source: SourceDetail; onDisconnected: () => void }) {
  const [editing, setEditing] = useState(false);
  const [lifecycle, setLifecycle] = useState(false);
  // Fixed when the dialog opens: the record reloads underneath it once the change applies.
  const [pausing, setPausing] = useState(true);
  const [disconnecting, setDisconnecting] = useState(false);
  const [applied, setApplied] = useState<AppliedChange | null>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const listing = useAdminSources(true);
  const row = listing.data?.sources.find((s) => s.id === source.id);
  const organization = source.managed_by === "organization";
  const paused = source.status === "paused";
  const name = source.settings.name;
  const Glyph = SOURCE_GLYPHS[sourceKind(source.connector_type)];
  const unresolved = source.current_import?.check?.unresolved_references.filter((r) => r.state === "unmapped").length ?? 0;
  const step = nextStep(source.next_action, unresolved);
  const openLifecycle = (pause: boolean) => {
    setPausing(pause);
    setLifecycle(true);
  };

  return (
    <>
      <header className={styles.head}>
        <h1 className={styles.name}>
          <span className={styles.glyph}>
            <Glyph size={20} aria-hidden="true" focusable="false" />
          </span>
          {name}
        </h1>
        <p className={styles.identity}>
          <span className={styles.identifier} data-testid="source-key">
            {source.key}
          </span>
          <span>{exportFormat(source.connector_type).title}</span>
          <span>{managedLabel(source)}</span>
        </p>
        <p className={styles.facts}>
          {paused ? <StatusMark tone="neutral" icon={Pause}>Paused</StatusMark> : <StatusMark tone="healthy">Active</StatusMark>}
          <span>{formatCount(source.documents, "document", "documents")}</span>
          <span>Added {formatDay(source.created_at)}</span>
          <span>{source.last_succeeded_at ? `Last synced ${formatDay(source.last_succeeded_at)}` : "Never synced"}</span>
        </p>
      </header>

      {step && (
        <div className={styles.next} data-testid="next-step" data-action={source.next_action}>
          <p className={styles.nextText}>
            <strong>{step.label}.</strong> {step.sentence}
          </p>
          <NextControl source={source} onUpload={() => uploadRef.current?.focus()} onResume={() => openLifecycle(false)} />
        </div>
      )}

      <div className={styles.actions}>
        <SyncButton source={source} />
        {organization && source.current_import && <CheckButton source={source} />}
        {organization && (
          <Button icon={<Icon as={Pencil} />} onClick={() => setEditing(true)}>
            Edit settings
          </Button>
        )}
        <Button icon={<Icon as={paused ? Play : Pause} />} onClick={() => openLifecycle(!paused)}>
          {paused ? "Resume" : "Pause"}
        </Button>
        <Button variant="quiet" icon={<Icon as={Trash2} />} onClick={() => setDisconnecting(true)}>
          Disconnect
        </Button>
        <p className={styles.note}>Managing this source gives you no access to its content.</p>
      </div>

      {applied && <AppliedLine change={applied} onDismiss={() => setApplied(null)} />}

      {organization ? (
        <ExportSection source={source} inputRef={uploadRef} />
      ) : (
        <NoticeLine icon={<Icon as={ServerCog} />}>
          <span data-testid="operator-managed">
            An operator syncs this source from an export folder on the server with <code>lattice sync</code>, and its settings come from the operator&apos;s sources
            file. You can sync, pause, resume or disconnect it here.
          </span>
        </NoticeLine>
      )}
      {organization && source.current_import && <CheckSection source={source} current={source.current_import} />}
      <SyncSection source={source} row={row} />
      <SettingsSection source={source} />

      {organization && <EditSourceDialog source={source} open={editing} onOpenChange={setEditing} onApplied={setApplied} />}
      <LifecycleDialog source={source} pause={pausing} open={lifecycle} onOpenChange={setLifecycle} onApplied={setApplied} />
      <DisconnectDialog source={source} open={disconnecting} onOpenChange={setDisconnecting} onDisconnected={onDisconnected} />
    </>
  );
}

/* --- the next action ---------------------------------------------------------------------- */

function NextControl({ source, onUpload, onResume }: { source: SourceDetail; onUpload: () => void; onResume: () => void }) {
  switch (source.next_action) {
    case "upload_import":
    case "replace_import":
      return source.upload.available ? (
        <Button size="sm" variant="primary" icon={<Icon as={FileUp} />} onClick={onUpload}>
          Choose an export
        </Button>
      ) : null;
    case "map_references":
      return (
        <ButtonLink size="sm" variant="primary" href="/access/accounts/references" icon={<Icon as={Link2} />}>
          Map references in Access
        </ButtonLink>
      );
    case "resume":
      return (
        <Button size="sm" variant="primary" icon={<Icon as={Play} />} onClick={onResume}>
          Resume
        </Button>
      );
    default:
      return null;
  }
}

/** Sync now / Retry: the existing request; refused states come from the server. */
function SyncButton({ source }: { source: SourceDetail }) {
  const request = useRequestSync();
  const toast = useToast();
  const syncing = source.job?.kind === "source_sync" && (source.job.status === "queued" || source.job.status === "running");
  const blocker = syncing ? null : blockerSentence(source.sync_blocked_by);
  const inert = !source.can_sync || syncing || request.isPending;
  const describedBy = useId();
  const retry = source.next_action === "retry_sync";
  return (
    <span className={styles.control}>
      <Button
        variant={source.next_action === "start_sync" || retry ? "primary" : "secondary"}
        icon={<Icon as={RefreshCw} />}
        loading={request.isPending}
        aria-disabled={inert || undefined}
        aria-describedby={blocker || syncing ? describedBy : undefined}
        onClick={() => {
          if (inert) return;
          request.mutate(source.id, { onSuccess: () => toast({ title: retry ? "Sync requested again" : "Sync requested", description: source.settings.name }) });
        }}
      >
        {retry ? "Retry sync" : "Sync now"}
      </Button>
      {(blocker || syncing) && (
        <span id={describedBy} className={styles.secondary}>
          {syncing ? "A sync is queued or running." : blocker}
        </span>
      )}
      {request.isError && !request.isPending && (
        <span className={styles.problem} role="alert">
          Couldn&apos;t request a sync: {sourceError(request.error, source.settings.name)}
        </span>
      )}
    </span>
  );
}

/** Check the current export again (the dry run, as a job): useful after mapping references. */
function CheckButton({ source }: { source: SourceDetail }) {
  const check = useCheckImport(source.id);
  const active = source.job?.status === "queued" || source.job?.status === "running";
  return (
    <span className={styles.control}>
      <Button icon={<Icon as={ScanSearch} />} loading={check.isPending} aria-disabled={active || undefined} onClick={() => !active && !check.isPending && check.mutate()}>
        Check export again
      </Button>
      {check.isError && !check.isPending && (
        <span className={styles.problem} role="alert">
          Couldn&apos;t start a check: {sourceError(check.error, source.settings.name)}
        </span>
      )}
    </span>
  );
}

/* --- the export: upload and the current import ---------------------------------------------- */

type UploadState =
  | { phase: "idle" }
  | { phase: "sending"; sent: number; total: number }
  | { phase: "validating" }
  | { phase: "accepted"; result: UploadResult }
  | { phase: "refused"; error: unknown }
  | { phase: "cancelled" };

function ExportSection({ source, inputRef }: { source: SourceDetail; inputRef: React.RefObject<HTMLInputElement | null> }) {
  const format = exportFormat(source.connector_type);
  const limits = source.upload;
  const settled = useUploadSettled();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [state, setState] = useState<UploadState>({ phase: "idle" });
  const [tried, setTried] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const inputId = useId();
  const helpId = `${inputId}-help`;
  const errorId = `${inputId}-error`;
  const busy = state.phase === "sending" || state.phase === "validating";
  const receiving = source.latest_upload?.status === "receiving" || source.latest_upload?.status === "validating";
  const accept = limits.formats.includes("jsonl") ? ".zip,.jsonl,application/zip,application/x-ndjson" : ".zip,application/zip";
  const tooLarge = file !== null && file.size > limits.max_upload_bytes;
  const problem = tried && !file ? "Choose an export file first." : tooLarge ? `This file is ${formatBytes(file.size)}: the limit is ${formatBytes(limits.max_upload_bytes)}.` : undefined;

  const choose = (event: ChangeEvent<HTMLInputElement>) => {
    setFile(event.target.files?.[0] ?? null);
    setState({ phase: "idle" });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (!file || tooLarge || busy) return inputRef.current?.focus();
    const abort = new AbortController();
    controller.current = abort;
    setState({ phase: "sending", sent: 0, total: file.size });
    uploadExport(source.id, file, {
      signal: abort.signal,
      // The last byte sent means the server is validating, even before the load event.
      onProgress: (sent, total) => setState(total > 0 && sent >= total ? { phase: "validating" } : { phase: "sending", sent, total }),
      onSent: () => setState({ phase: "validating" }),
    })
      .then((result) => {
        setState({ phase: "accepted", result });
        setFile(null);
        setTried(false);
        if (inputRef.current) inputRef.current.value = "";
        toast({ title: "Export accepted", description: source.settings.name });
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") setState({ phase: "cancelled" });
        else setState({ phase: "refused", error });
      })
      .finally(() => {
        controller.current = null;
        void settled();
      });
  };

  return (
    <OpsSection id="export-heading" title="Export">
      <div className={styles.exportGrid}>
        <div className={styles.expect}>
          <p className={styles.subhead}>What to upload</p>
          <ul className={styles.effects} data-testid="export-structure">
            {format.structure.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className={styles.secondary} data-testid="upload-limits">
            {limitsSentence(limits)}
          </p>
          <p className={styles.secondary}>
            {source.current_import
              ? "A new export replaces the current one only once it passes validation; until then the current one stays. "
              : ""}
            Each upload is validated entry by entry, then checked automatically — a dry run that writes nothing and sends nothing to an AI provider. Nothing
            syncs until you start a sync.
          </p>
        </div>

        {!limits.available ? (
          <Callout tone="boundary" label="Uploads aren't available here">
            <p>{rejectSentence("storage_unavailable")}</p>
          </Callout>
        ) : (
          <form className={styles.upload} onSubmit={submit} noValidate>
            <label htmlFor={inputId} className={styles.subhead}>
              Export file
            </label>
            <input
              ref={inputRef}
              id={inputId}
              type="file"
              accept={accept}
              onChange={choose}
              disabled={busy}
              className={styles.file}
              aria-describedby={[problem ? errorId : null, helpId].filter(Boolean).join(" ")}
              aria-invalid={problem ? true : undefined}
            />
            <p id={helpId} className={styles.secondary}>
              {limits.formats.includes("jsonl") ? "A .zip archive or one .jsonl file" : "A .zip archive"}, up to {formatBytes(limits.max_upload_bytes)}. Its name
              isn&apos;t sent or kept.
            </p>
            {problem && (
              <p id={errorId} className={styles.problem}>
                {problem}
              </p>
            )}
            <div className={styles.uploadActions}>
              <Button type="submit" variant={source.current_import ? "secondary" : "primary"} icon={<Icon as={Upload} />} loading={busy} aria-disabled={receiving || undefined}>
                {source.current_import ? "Upload a replacement" : "Upload"}
              </Button>
              {state.phase === "sending" && (
                <Button type="button" variant="quiet" icon={<Icon as={X} />} onClick={() => controller.current?.abort()}>
                  Cancel upload
                </Button>
              )}
            </div>
            <UploadProgress state={state} />
            {receiving && !busy && <p className={styles.secondary}>Another upload to this source is being received or validated.</p>}
          </form>
        )}
      </div>

      {source.latest_upload && source.latest_upload.status === "rejected" && (
        <Callout tone="fault" label="The latest upload was refused" role="status">
          <p data-testid="rejected-upload">
            {rejectSentence(source.latest_upload.rejected_code ?? "")} <span className={styles.code}>{source.latest_upload.rejected_code}</span>
          </p>
          <p className={styles.secondary}>{source.current_import ? "The current export is unchanged." : "Nothing was kept from it."}</p>
        </Callout>
      )}

      {source.current_import ? (
        <ImportFacts current={source.current_import} />
      ) : (
        <p className={styles.secondary} data-testid="no-import">
          No export yet: this source holds nothing until one is uploaded and synced.
        </p>
      )}
    </OpsSection>
  );
}

/** Bytes sent (real, from the browser), then an honest "validating" with no number. */
function UploadProgress({ state }: { state: UploadState }) {
  const percent = state.phase === "sending" && state.total > 0 ? Math.min(100, Math.floor((state.sent / state.total) * 100)) : 0;
  return (
    <div className={styles.progressArea}>
      {state.phase === "sending" && (
        <div className={styles.progress}>
          <progress className={styles.bar} max={state.total} value={state.sent} aria-label="Upload progress" aria-valuetext={`${percent}% sent`} />
          <span className={styles.secondary}>
            Sending {formatBytes(state.sent)} of {formatBytes(state.total)} ({percent}%)
          </span>
        </div>
      )}
      <p role="status" className={state.phase === "refused" ? styles.problem : styles.secondary} data-testid="upload-status">
        {state.phase === "sending" && percent === 0 && "Upload started."}
        {state.phase === "validating" && "Received. Lattice is validating the archive — this can take a minute for a large export."}
        {state.phase === "accepted" &&
          `Export accepted: ${formatCount(state.result.import.entries ?? 0, "file kept", "files kept")}, ${formatCount(state.result.import.ignored_entries ?? 0, "ignored", "ignored")}. ${state.result.check_queued ? "A check is queued." : "It will be checked when the current work finishes."}`}
        {state.phase === "refused" && `Not accepted: ${sourceError(state.error)}`}
        {state.phase === "cancelled" && "Upload cancelled. Nothing was kept."}
      </p>
    </div>
  );
}

function ImportFacts({ current }: { current: SourceImport }) {
  return (
    <dl className={styles.ledger} data-testid="current-import">
      <Fact label="Current export">
        <StatusMark tone={current.status === "ready" ? "healthy" : "neutral"}>{importStatusLabel(current.status)}</StatusMark>{" "}
        <span className={styles.when}>received {formatDayTime(current.received_at ?? current.created_at)}</span>
      </Fact>
      <Fact label="Size">
        {formatBytes(current.size_bytes)} {current.format === "zip" ? "zip archive" : "JSON Lines file"}
        {current.expanded_bytes !== null && current.format === "zip" && <span className={styles.secondary}> · {formatBytes(current.expanded_bytes)} expanded</span>}
      </Fact>
      <Fact label="Files">
        {formatCount(current.entries ?? 0, "file read", "files read")}
        {(current.ignored_entries ?? 0) > 0 && <span className={styles.secondary}> · {formatCount(current.ignored_entries ?? 0, "other entry ignored", "other entries ignored")}</span>}
      </Fact>
      {current.sha256 && (
        <Fact label="SHA-256">
          <span className={styles.digest}>{current.sha256}</span>
        </Fact>
      )}
    </dl>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.fact}>
      <dt className={styles.factLabel}>{label}</dt>
      <dd className={styles.factValue}>{children}</dd>
    </div>
  );
}

/* --- the check of the current export ----------------------------------------------------- */

function CheckSection({ source, current }: { source: SourceDetail; current: SourceImport }) {
  const checking = source.job?.kind === "import_check" && (source.job.status === "queued" || source.job.status === "running");
  const check = current.check;
  return (
    <OpsSection id="check-heading" title="Check">
      <p className={styles.note}>
        A check reads the export the way a sync would — access rules, accounts, identities — but writes nothing and sends nothing to an AI provider. It reports
        counts and codes only.
      </p>
      {checking && (
        <p className={styles.job} role="status">
          <Icon as={ScanSearch} size={14} /> Checking the export{source.job?.status === "queued" ? " (queued)" : ""}…
        </p>
      )}
      {!check ? (
        !checking && <p className={styles.secondary}>Not checked yet.</p>
      ) : check.error_code ? (
        <Callout tone="fault" label="This export can't be synced" role="status">
          <p>
            {checkErrorSentence(check.error_code)} <span className={styles.code}>{check.error_code}</span>
          </p>
        </Callout>
      ) : (
        <CheckResult check={check} />
      )}
    </OpsSection>
  );
}

function CheckResult({ check }: { check: ImportCheck }) {
  const counts: { label: string; value: number; attention?: boolean }[] = [
    { label: "Records read", value: check.records_seen },
    { label: "New", value: check.would_create },
    { label: "Changed", value: check.would_update },
    { label: "Access changed", value: check.would_update_access },
    { label: "Unchanged", value: check.unchanged },
    { label: "Removed", value: check.would_delete },
    { label: "Held back", value: check.would_quarantine, attention: check.would_quarantine > 0 },
    { label: "Invalid", value: check.invalid_records, attention: check.invalid_records > 0 },
  ];
  return (
    <div className={styles.check} data-testid="check-result">
      <p className={styles.secondary}>
        Checked <time dateTime={check.checked_at}>{formatDayTime(check.checked_at)}</time>. At a sync now:
      </p>
      <dl className={styles.counts}>
        {counts.map((c) => (
          <div key={c.label} className={styles.count} data-attention={c.attention || undefined}>
            <dt className={styles.countLabel}>{c.label}</dt>
            <dd className={styles.countValue}>{c.value.toLocaleString("en-GB")}</dd>
          </div>
        ))}
      </dl>
      {check.deletion_guard && (
        <p className={styles.secondary}>
          The deletion guard would hold back removals (<span className={styles.code}>{check.deletion_guard}</span>): the export is missing much of what was
          synced before.
        </p>
      )}
      {check.failure_codes.length > 0 && (
        <ul className={styles.codes} aria-label="Why records would be held back or are invalid">
          {check.failure_codes.map((f) => (
            <li key={f.code}>
              <span className={styles.code}>
                <Code value={f.code} />
              </span>{" "}
              <span className={styles.secondary}>× {f.count.toLocaleString("en-GB")}</span>
            </li>
          ))}
        </ul>
      )}
      {check.unresolved_total > 0 && <References references={check.unresolved_references} total={check.unresolved_total} />}
    </div>
  );
}

function References({ references, total }: { references: readonly UnresolvedReference[]; total: number }) {
  const columns: Column<UnresolvedReference>[] = [
    { key: "reference", header: "Reference", primary: true, cell: (r) => <code className={styles.identifier}>{r.external_ref}</code> },
    { key: "records", header: "Records", align: "end", mono: true, short: true, cell: (r) => r.records.toLocaleString("en-GB") },
    {
      key: "state",
      header: "Account",
      cell: (r) => (r.state === "mapped" ? <span>Mapped — applies at the next sync</span> : <span className={styles.attention}>No account: held back</span>),
    },
  ];
  return (
    <div className={styles.references}>
      <p className={styles.subhead}>Account references without an account</p>
      <p className={styles.secondary}>
        Records that name these references are held back — never indexed without their account — until each reference is mapped to an account in Access. Shown
        exactly as the export names them.
      </p>
      <DataTable
        caption="Account references in the current export and whether each has an account"
        columns={columns}
        rows={references}
        rowKey={(r) => r.external_ref}
        density="dense"
        stackBelow="768"
      />
      {total > references.length && <p className={styles.secondary}>And {(total - references.length).toLocaleString("en-GB")} more.</p>}
      <p>
        <ButtonLink size="sm" href="/access/accounts/references" icon={<Icon as={Link2} />}>
          Map references in Access
        </ButtonLink>
      </p>
    </div>
  );
}

/* --- sync ---------------------------------------------------------------------------------- */

function SyncSection({ source, row }: { source: SourceDetail; row: AdminSource | undefined }) {
  const run = row?.last_run ?? null;
  const reading = readRun(run?.status);
  return (
    <OpsSection id="sync-heading" title="Sync">
      <JobFacts job={source.job} />
      <dl className={styles.ledger}>
        <Fact label="Last sync">
          <StatusMark tone={reading.tone} icon={reading.icon}>
            {reading.label}
          </StatusMark>
          {run?.finished_at && <span className={styles.when}> {formatDayTime(run.finished_at)}</span>}
          {run?.error_code && (
            <span className={styles.code}>
              {" "}
              <Code value={run.error_code} />
            </span>
          )}
        </Fact>
        {run && (
          <Fact label="Records">
            <span data-testid="run-counts">
              {[
                `${run.counts["records_seen"] ?? 0} read`,
                `${run.counts["created"] ?? 0} new`,
                `${run.counts["updated"] ?? 0} changed`,
                `${run.counts["unchanged"] ?? 0} unchanged`,
                `${run.counts["deleted"] ?? 0} removed`,
                `${run.counts["failed"] ?? 0} failed`,
              ].join(" · ")}
            </span>
          </Fact>
        )}
        <Fact label="Last full sync">{source.last_succeeded_at ? formatDayTime(source.last_succeeded_at) : "Never"}</Fact>
      </dl>
      {row && run && (row.failure_codes.length > 0 || (run.counts["failed"] ?? 0) > 0) && (
        <div className={styles.failures}>
          <p className={styles.subhead}>Failed records in the last sync</p>
          <FailureRecords source={row} />
        </div>
      )}
    </OpsSection>
  );
}

function JobFacts({ job }: { job: SourceJob | null }) {
  if (!job) return null;
  const what = job.kind === "import_check" ? "Check" : "Sync";
  if (job.status === "queued" || job.status === "running") {
    return (
      <p className={styles.job} role="status" data-testid="job-state">
        <Icon as={RefreshCw} size={14} />
        {what} {job.status === "queued" ? "queued" : "running"} <span className={styles.when}>since {formatTime(job.started_at ?? job.created_at)}</span>
      </p>
    );
  }
  if (job.status === "failed") {
    return (
      <p className={styles.jobFailed} data-testid="job-state">
        The latest requested {what.toLowerCase()} failed
        {job.error_code && (
          <>
            {" · "}
            <span className={styles.code}>
              <Code value={job.error_code} />
            </span>
          </>
        )}
        {job.attempts > 1 && <span className={styles.secondary}> after {job.attempts} attempts</span>}
      </p>
    );
  }
  return null;
}

/* --- settings ------------------------------------------------------------------------------ */

function SettingsSection({ source }: { source: SourceDetail }) {
  const s = source.settings;
  const channels = Object.entries(s.channel_sensitivities);
  return (
    <OpsSection id="settings-heading" title="Settings">
      <dl className={styles.ledger} data-testid="settings">
        <Fact label="Name">{s.name}</Fact>
        <Fact label="Default sensitivity">{levelLabel(s.default_sensitivity)}</Fact>
        <Fact label="Default departments">{s.default_departments.length ? s.default_departments.map((d) => d.name ?? d.key).join(", ") : "None"}</Fact>
        <Fact label="Authority">{authorityLabel(s.authority)}</Fact>
        <Fact label="Account scoping">{scopingLabel(s.account_scoped)}</Fact>
        {source.connector_type === "chat_export" && (
          <Fact label="Channel sensitivities">
            {channels.length === 0
              ? "None: every channel uses the default"
              : channels.map(([id, level]) => (
                  <span key={id} className={styles.channelLine}>
                    <code className={styles.identifier}>{id}</code> {levelLabel(level)}
                  </span>
                ))}
          </Fact>
        )}
      </dl>
      <p className={styles.note}>
        Each record keeps the access its export declares; these settings fill in only what a record leaves out. A change that would let more people read
        records says so before it applies.
      </p>
      {sourceBusy(source) && <span className="visually-hidden">This page updates while Lattice works on this source.</span>}
    </OpsSection>
  );
}

