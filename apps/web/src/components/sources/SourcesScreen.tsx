"use client";

import { ChevronDown, ChevronRight, Clock, Hourglass, RefreshCw, ShieldAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { isApiError } from "@/api/errors";
import { useAdminSources, useRequestSync, useSourceFailures, useSources } from "@/api/queries";
import type { AdminSource, MemberSource, SyncFailure, SyncJob } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { DataTable, type Column } from "@/components/primitives/DataTable";
import { SOURCE_GLYPHS } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { StateBlock } from "@/components/primitives/StateBlock";
import { StatusMark } from "@/components/primitives/StatusMark";
import { useToast } from "@/components/primitives/Toast";
import { OpsHead, OpsPage, OpsSection } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { ageLabel, connectorLabel, formatCount, formatDay, formatDayTime, formatTime, humanize, levelLabel } from "@/lib/format";
import { useNow } from "@/lib/use-now";

import styles from "./SourcesScreen.module.css";
import { failedCounts, isOld, jobActive, readRun, registerSummary, requestMessage, requestState, sourceFailed, sourceKind, type SyncRequest } from "./sync-model";

/**
 * Sources (design-direction §6.6; security-model §10): a live register of what Lattice
 * synchronizes. Members: GET /v1/sources -- only sources they can read from, counted inside
 * their access. Administrators: GET /v1/admin/sources -- every source with aggregate
 * totals, the last run, failure codes and the requested sync, polled while one is queued
 * or running; failed records open inline, by opaque reference unless the administrator
 * can read the document.
 */
export function SourcesScreen() {
  const { isAdmin, me } = useWorkspace();
  return (
    <OpsPage aria-labelledby="page-title">
      {isAdmin ? <AdminRegister organization={me.organization.name} /> : <MemberRegister />}
    </OpsPage>
  );
}

/* --- members --------------------------------------------------------------------------- */

function MemberRegister() {
  const query = useSources();
  const now = useNow();
  const sources = query.data?.sources ?? [];
  const columns: Column<MemberSource>[] = [
    { key: "source", header: "Source", primary: true, cell: (s) => <SourceName name={s.name} connector={s.connector_type} /> },
    {
      key: "readable",
      header: "You can read",
      align: "end",
      mono: true,
      short: true,
      width: "9rem",
      cell: (s) => formatCount(s.readable_documents, "document", "documents"),
    },
    {
      key: "sync",
      header: "Last sync",
      width: "17rem",
      cell: (s) => <SyncReading status={s.last_run_status} at={s.last_synced_at} succeededAt={s.last_succeeded_at} now={now} />,
    },
  ];
  return (
    <>
      <OpsHead
        title="Sources"
        deck="What Lattice can search for you, and when each source last synced. Counts include only what you can read."
      />
      {query.isError ? (
        <ErrorState error={query.error} title="Couldn't load sources" onRetry={() => query.refetch()} retrying={query.isFetching} />
      ) : query.data && sources.length === 0 ? (
        <StateBlock family="quiet" heading="Lattice hasn't indexed any sources you can access yet">
          <p>When an administrator connects a source that holds content you can read, it appears here with its last sync.</p>
        </StateBlock>
      ) : (
        <DataTable
          caption="Sources you can read from"
          columns={columns}
          rows={sources}
          rowKey={(s) => s.id}
          loading={!query.data}
          loadingRows={2}
          stackBelow="768"
        />
      )}
    </>
  );
}

/* --- administrators -------------------------------------------------------------------- */

function AdminRegister({ organization }: { organization: string }) {
  const query = useAdminSources(true);
  const now = useNow();
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const announce = useSyncAnnouncer(query.data?.sources);
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const sources = query.data?.sources ?? [];
  const toggle = (id: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const columns: Column<AdminSource>[] = [
    {
      key: "source",
      header: "Source",
      primary: true,
      cell: (s) => (
        <SourceName name={s.name} connector={s.connector_type} sourceKey={s.key} inactive={s.status !== "active" ? humanize(s.status) : null} />
      ),
    },
    {
      key: "sync",
      header: "Last sync",
      cell: (s) => (
        <SyncReading
          status={s.last_run?.status ?? null}
          at={s.last_run?.finished_at ?? s.last_synced_at}
          succeededAt={s.last_succeeded_at}
          now={now}
          code={sourceFailed(s) ? s.last_run?.error_code : null}
          guard={s.last_run?.deletion_guard_tripped ?? false}
        />
      ),
    },
    { key: "access", header: "Permissions", cell: (s) => <AccessReading s={s} now={now} /> },
    { key: "documents", header: "Documents", align: "end", mono: true, short: true, width: "7rem", cell: (s) => s.documents.toLocaleString("en-GB") },
    { key: "records", header: "Failed records", short: true, cell: (s) => <RecordsReading s={s} open={open.has(s.id)} onToggle={() => toggle(s.id)} /> },
    { key: "action", header: "Sync", hideHeader: true, cell: (s) => <SyncControl s={s} announce={announce} /> },
  ];

  return (
    <>
      <OpsHead
        title="Sources"
        deck={`Every source ${organization} has connected: when it last synced, whether access was applied, which records failed and why. Totals only — never which documents, accounts or people are behind them.`}
      >
        {query.data && sources.length > 0 && (
          <p className={styles.summary} data-testid="register-summary">
            {registerSummary(sources, now)}
          </p>
        )}
      </OpsHead>
      {forbidden ? (
        <StateBlock family="boundary" label="Administrators only" heading="You no longer have administrator access">
          <p>The sources register is available to your organization&apos;s administrators.</p>
        </StateBlock>
      ) : query.isError ? (
        <ErrorState error={query.error} title="Couldn't load sources" onRetry={() => query.refetch()} retrying={query.isFetching} />
      ) : query.data && sources.length === 0 ? (
        <StateBlock family="quiet" heading="No sources are connected">
          <p>
            An operator connects a source with the <code>lattice sync</code> command. Once it has synced, it appears here and can be
            synced again from this page.
          </p>
        </StateBlock>
      ) : (
        <OpsSection id="register-heading" title="Connected sources" count={query.data ? sources.length.toLocaleString("en-GB") : undefined}>
          <DataTable
            caption="Connected sources and their sync status"
            columns={columns}
            rows={sources}
            rowKey={(s) => s.id}
            loading={!query.data}
            loadingRows={3}
            expanded={open}
            renderExpansion={(s) => <FailureDetail s={s} />}
          />
          <p className={styles.note}>
            A failed record is named by its document only if you can read that document; otherwise by an opaque reference. Record
            contents, titles and file names never appear here.
          </p>
        </OpsSection>
      )}
      <span className="visually-hidden" role="status" aria-live="polite" data-testid="sync-announcer">
        {announce.message}
      </span>
    </>
  );
}

/* --- cells ----------------------------------------------------------------------------- */

function SourceName({ name, connector, sourceKey, inactive = null }: { name: string; connector: string; sourceKey?: string; inactive?: string | null }) {
  const Glyph = SOURCE_GLYPHS[sourceKind(connector)];
  return (
    <span className={styles.source}>
      <span className={styles.glyph}>
        <Glyph size={16} aria-hidden="true" focusable="false" />
      </span>
      <span className={styles.sourceName}>{name}</span>
      <span className={styles.sourceMeta}>
        {sourceKey && <span className={styles.key}>{sourceKey}</span>}
        {sourceKey && <span aria-hidden="true">·</span>}
        <span>{connectorLabel(connector)}</span>
        {inactive && <span className={styles.inactive}>{inactive}: not synced</span>}
      </span>
    </span>
  );
}

function When({ iso, withTime = false }: { iso: string | null | undefined; withTime?: boolean }) {
  if (!iso) return null;
  return (
    <time className={styles.when} dateTime={iso} title={formatDayTime(iso) ?? undefined}>
      {formatDay(iso)}
      {withTime && `, ${formatTime(iso)}`}
    </time>
  );
}

/**
 * The last run in words, when it finished, and how long ago the source last fully synced.
 * Age is ink under a week and ochre (time) after; a failed run is the only fault.
 */
function SyncReading({
  status,
  at,
  succeededAt,
  now,
  code = null,
  guard = false,
}: {
  status: string | null;
  at: string | null | undefined;
  succeededAt: string | null;
  now: number;
  code?: string | null;
  guard?: boolean;
}) {
  const run = readRun(status);
  const succeeded = status === "succeeded";
  const old = isOld(succeededAt, now);
  return (
    <span className={styles.stack}>
      <span className={styles.line}>
        <StatusMark tone={run.tone} icon={run.icon}>
          {run.label}
        </StatusMark>
        {status && <When iso={at} withTime />}
      </span>
      {code && (
        <span className={styles.code}>
          <Code value={code} />
        </span>
      )}
      {/* The age of the last full sync, when it adds something: it's old, or the last run wasn't one. */}
      {status && (!succeeded || old) && (
        <span className={[styles.age, old && styles.old].filter(Boolean).join(" ")} data-old={old || undefined}>
          {old && <Icon as={Clock} size={14} />}
          {!succeededAt ? (
            "Never fully synced"
          ) : succeeded ? (
            capitalize(ageLabel(succeededAt, now) ?? "")
          ) : (
            <>
              <span>Last full sync</span>{" "}
              <span className={styles.nowrap}>
                <When iso={succeededAt} /> · {ageLabel(succeededAt, now)}
              </span>
            </>
          )}
        </span>
      )}
      {guard && (
        <span className={styles.guard}>
          <Icon as={ShieldAlert} size={14} /> Deletion guard held back deletions
        </span>
      )}
    </span>
  );
}

function AccessReading({ s, now }: { s: AdminSource; now: number }) {
  const old = isOld(s.acl_synced_at, now);
  return (
    <span className={styles.stack}>
      <span>{s.account_scoped ? "Follows the source, by account" : "Follows the source's sharing"}</span>
      <span className={[styles.age, old && styles.old].filter(Boolean).join(" ")} data-old={old || undefined}>
        {old && <Icon as={Clock} size={14} />}
        {s.acl_synced_at ? (
          <>
            Access applied <When iso={s.acl_synced_at} />
          </>
        ) : (
          "Not applied yet"
        )}
      </span>
      <span className={styles.secondary}>Default {levelLabel(s.default_sensitivity)}</span>
    </span>
  );
}

function RecordsReading({ s, open, onToggle }: { s: AdminSource; open: boolean; onToggle: () => void }) {
  if (!s.last_run) return <span className={styles.secondary}>—</span>;
  const { records, embeddings } = failedCounts(s);
  if (records === 0 && embeddings === 0) {
    // A run that failed as a whole says nothing about its records.
    return <span className={styles.secondary}>{sourceFailed(s) ? "—" : "None"}</span>;
  }
  const label = [records ? formatCount(records, "record", "records") : null, embeddings ? formatCount(embeddings, "embedding", "embeddings") : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className={styles.stack}>
      <span className={styles.failed}>{label}</span>
      <Button
        size="sm"
        variant="quiet"
        className={styles.disclosure}
        icon={<Icon as={open ? ChevronDown : ChevronRight} />}
        aria-expanded={open}
        aria-label={`${open ? "Hide" : "Show"} failed records: ${s.name}`}
        onClick={onToggle}
      >
        {open ? "Hide" : "Show"}
      </Button>
    </span>
  );
}

/**
 * Sync now: the real request (POST /v1/admin/sources/{id}/sync). While a sync is queued or
 * running the control stays focusable but inert, and the state is written beside it; the
 * outcome is announced to assistive tech and kept in the register (the toast only echoes).
 */
function SyncControl({ s, announce }: { s: AdminSource; announce: SyncAnnouncer }) {
  const request = useRequestSync();
  const toast = useToast();
  const active = jobActive(s.job);
  const inactive = s.status !== "active";
  const pending = request.isPending;
  const inert = active || inactive || pending;
  const stateId = `sync-state-${s.id}`;
  const failedRequest = request.isError && !pending ? request.error : null;
  const submit = () => {
    if (inert) return;
    request.mutate(s.id, {
      onSuccess: (job) => {
        announce.requested(s, job);
        toast({ title: "Sync requested", description: s.name });
      },
    });
  };
  return (
    <span className={styles.control}>
      <Button
        size="sm"
        variant="secondary"
        icon={<Icon as={RefreshCw} />}
        loading={pending}
        onClick={submit}
        aria-disabled={inert || undefined}
        aria-describedby={active || inactive || s.job?.status === "failed" ? stateId : undefined}
        aria-label={`Sync now: ${s.name}`}
      >
        Sync now
      </Button>
      <JobLine id={stateId} job={s.job} inactive={inactive ? humanize(s.status) : null} />
      {failedRequest && (
        <span className={styles.requestError} role="alert">
          Couldn&apos;t request a sync: {isApiError(failedRequest) ? failedRequest.message.replace(/\.$/, "") : "something went wrong"}. Try again.
        </span>
      )}
    </span>
  );
}

function JobLine({ id, job, inactive }: { id: string; job: SyncJob | null; inactive: string | null }) {
  if (inactive) {
    return (
      <span id={id} className={styles.secondary}>
        {inactive}: syncing is off
      </span>
    );
  }
  if (job?.status === "queued") {
    return (
      <span id={id} className={styles.job}>
        <Icon as={Hourglass} size={14} />
        Queued <span className={styles.when}>since {formatTime(job.created_at)}</span>
      </span>
    );
  }
  if (job?.status === "running") {
    return (
      <span id={id} className={styles.job}>
        <Icon as={RefreshCw} size={14} />
        Syncing <span className={styles.when}>since {formatTime(job.started_at ?? job.created_at)}</span>
      </span>
    );
  }
  if (job?.status === "failed") {
    return (
      <span id={id} className={styles.jobFailed}>
        Requested sync failed{job.error_code ? " · " : ""}
        {job.error_code && (
          <span className={styles.code}>
            <Code value={job.error_code} />
          </span>
        )}
      </span>
    );
  }
  return null;
}

/* --- failed records (inline expansion) ------------------------------------------------- */

function FailureDetail({ s }: { s: AdminSource }) {
  return (
    <div className={styles.detail}>
      {s.failure_codes.length > 0 && (
        <ul className={styles.codes} aria-label={`Failure codes in the last sync of ${s.name}`}>
          {s.failure_codes.map((c) => (
            <li key={`${c.stage}-${c.code}`}>
              <span className={styles.code}>
                {c.stage} · <Code value={c.code} />
              </span>{" "}
              <span className={styles.secondary}>× {c.count.toLocaleString("en-GB")}</span>
            </li>
          ))}
        </ul>
      )}
      <FailureRecords source={s} />
    </div>
  );
}

function FailureRecords({ source }: { source: AdminSource }) {
  const query = useSourceFailures(source.id);
  if (query.isError) {
    return <ErrorState error={query.error} title="Couldn't load failed records" onRetry={() => query.refetch()} retrying={query.isFetching} headingLevel="h3" />;
  }
  const columns: Column<SyncFailure>[] = [
    { key: "record", header: "Record", cell: (f) => <RecordRef f={f} /> },
    {
      key: "code",
      header: "Stage · code",
      mono: true,
      cell: (f) => (
        <>
          {f.stage} · <Code value={f.code} />
        </>
      ),
    },
    { key: "params", header: "Details", cell: (f) => <Params f={f} /> },
  ];
  return (
    <DataTable
      caption={`Failed records in the last sync of ${source.name}`}
      columns={columns}
      rows={query.data?.failures ?? []}
      rowKey={(f) => `${f.record}-${f.stage}-${f.code}`}
      loading={!query.data}
      loadingRows={1}
      density="dense"
      stackBelow="768"
      empty={<p className={styles.secondary}>No failed records in the last sync.</p>}
    />
  );
}

function RecordRef({ f }: { f: SyncFailure }) {
  return f.record_kind === "identifier" ? (
    <span className={styles.code}>{f.record}</span>
  ) : (
    <span className={styles.opaque}>
      <span className={styles.code}>{f.record}</span>
      <span className={styles.secondary}>opaque reference</span>
    </span>
  );
}

function Params({ f }: { f: SyncFailure }) {
  const entries = Object.entries(f.params);
  if (entries.length === 0) return <>—</>;
  return <>{entries.map(([k, v]) => `${humanize(k)}: ${String(v)}`).join(" · ")}</>;
}

/** A fixed code that may wrap only after its ":" separators, never inside a word. */
function Code({ value }: { value: string }) {
  const parts = value.split(":");
  return (
    <>
      {parts.map((part, i) => (
        <span key={i}>
          {part}
          {i < parts.length - 1 && (
            <>
              :<wbr />
            </>
          )}
        </span>
      ))}
    </>
  );
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/* --- announcements --------------------------------------------------------------------- */

interface SyncAnnouncer {
  message: string;
  requested: (source: AdminSource, job: SyncJob) => void;
}

/**
 * Speaks the progress of the sync requested last (queued → running → finished), derived
 * from the polled register itself, and echoes the outcome in a toast once.
 */
function useSyncAnnouncer(sources: readonly AdminSource[] | undefined): SyncAnnouncer {
  const [request, setRequest] = useState<SyncRequest>();
  const toasted = useRef(new Set<string>());
  const toast = useToast();
  const message = requestMessage(request, sources);
  const state = requestState(request, sources);

  useEffect(() => {
    if (!request || toasted.current.has(request.jobId) || !(state === "succeeded" || state === "superseded" || state === "failed")) return;
    toasted.current.add(request.jobId);
    const run = readRun(sources?.find((x) => x.id === request.sourceId)?.last_run?.status);
    toast({ title: state === "failed" ? "Sync failed" : `Sync finished: ${run.label}`, description: request.name });
  }, [request, state, sources, toast]);

  return {
    message,
    requested: (source, job) => setRequest({ sourceId: source.id, jobId: job.id, name: source.name, createdAt: job.created_at }),
  };
}
