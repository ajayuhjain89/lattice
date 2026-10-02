"use client";

import { AlertTriangle, CheckCircle2, CircleSlash, Loader2, RefreshCw, XCircle } from "lucide-react";
import { useState } from "react";

import { isApiError } from "@/api/errors";
import { useAdminSources, useRequestSync, useSourceFailures, useSources } from "@/api/queries";
import type { AdminSource, MemberSource, SyncFailure } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { SkeletonLines } from "@/components/primitives/Skeleton";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { connectorLabel, formatDay, formatDayTime, humanize, levelLabel, syncStatusLabel } from "@/lib/format";

import styles from "./SourcesScreen.module.css";

/**
 * Sources (security-model §10). Members: GET /v1/sources -- only sources they can read from,
 * with counts computed inside their access. Administrators: GET /v1/admin/sources --
 * every source with aggregate totals, the last sync, failure codes and requested syncs;
 * failed records by opaque reference unless the administrator can read the document.
 */
export function SourcesScreen() {
  const { isAdmin, me } = useWorkspace();
  return (
    <>
      <div className={styles.main}>
        <div className={styles.column}>
          <header className={styles.head}>
            <h1 className={styles.title}>Sources</h1>
            <p className={styles.deck}>
              {isAdmin
                ? `Every source ${me.organization.name} has connected: when it last synced, what failed and why, and totals only — never which documents, accounts or people are behind them.`
                : "What Lattice can search for you, and when each source last synced. Counts include only what you can read."}
            </p>
          </header>
          {isAdmin ? <AdminSources /> : <MemberSources />}
        </div>
      </div>
    </>
  );
}

/* --- members --------------------------------------------------------------------------- */

function MemberSources() {
  const query = useSources();
  if (query.isError) {
    return <ErrorState error={query.error} title="Couldn't load sources" onRetry={() => query.refetch()} retrying={query.isFetching} />;
  }
  if (!query.data) return <Loading label="Loading sources" />;
  const sources = query.data.sources;
  return (
    <section className={styles.section} aria-labelledby="sources-label">
      <SectionLabel id="sources-label" meta={`${sources.length} ${sources.length === 1 ? "source" : "sources"}`}>
        Your sources
      </SectionLabel>
      {sources.length === 0 ? (
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>Lattice hasn&apos;t indexed any sources you can access yet</p>
          <p>When an administrator connects a source that holds content you can read, it appears here.</p>
        </div>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className="visually-hidden">Sources you can read from</caption>
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">Type</th>
                  <th scope="col" className={styles.num}>
                    Documents you can read
                  </th>
                  <th scope="col">Last sync</th>
                </tr>
              </thead>
              <tbody>
                {sources.map((s) => (
                  <tr key={s.id}>
                    <th scope="row" className={styles.rowHead}>
                      {s.name}
                    </th>
                    <td>{connectorLabel(s.connector_type)}</td>
                    <td className={styles.num}>{s.readable_documents.toLocaleString("en-GB")}</td>
                    <td>
                      <MemberSync s={s} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className={styles.cards} aria-label="Sources you can read from">
            {sources.map((s) => (
              <li key={s.id} className={styles.card}>
                <span className={styles.primary}>{s.name}</span>
                <dl>
                  <dt>Type</dt>
                  <dd>{connectorLabel(s.connector_type)}</dd>
                  <dt>You can read</dt>
                  <dd>{s.readable_documents.toLocaleString("en-GB")} documents</dd>
                  <dt>Last sync</dt>
                  <dd>
                    <MemberSync s={s} />
                  </dd>
                </dl>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function MemberSync({ s }: { s: MemberSource }) {
  return (
    <>
      <RunStatus status={s.last_run_status} />
      <When iso={s.last_synced_at} />
    </>
  );
}

/* --- administrators -------------------------------------------------------------------- */

function AdminSources() {
  const query = useAdminSources(true);
  const [open, setOpen] = useState<string | null>(null);
  const forbidden = query.isError && isApiError(query.error) && query.error.status === 403;
  if (query.isError && !forbidden) {
    return <ErrorState error={query.error} title="Couldn't load sources" onRetry={() => query.refetch()} retrying={query.isFetching} />;
  }
  if (!query.data) return <Loading label="Loading sources" />;
  const sources = query.data.sources;
  const failing = sources.filter((s) => s.failure_codes.length > 0);
  return (
    <>
      <section className={styles.section} aria-labelledby="sources-label">
        <SectionLabel id="sources-label" meta={`${sources.length} ${sources.length === 1 ? "source" : "sources"}`}>
          Connected sources
        </SectionLabel>
        {sources.length === 0 ? (
          <div className={styles.empty}>
            <p className={styles.emptyTitle}>No sources are connected</p>
            <p>
              Sources are connected with the operator command <code>lattice sync</code>; once one has synced, it can be
              re-synced from here.
            </p>
          </div>
        ) : (
          <>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption className="visually-hidden">Connected sources and their sync status</caption>
                <thead>
                  <tr>
                    <th scope="col">Source</th>
                    <th scope="col">Permissions</th>
                    <th scope="col" className={styles.num}>
                      Documents
                    </th>
                    <th scope="col">Last sync</th>
                    <th scope="col">Failed records</th>
                    <th scope="col">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map((s) => (
                    <tr key={s.id}>
                      <th scope="row" className={styles.rowHead}>
                        <span className={styles.primary}>{s.name}</span>
                        <span className={`${styles.secondary} ${styles.mono}`}>
                          {s.key} · {connectorLabel(s.connector_type)}
                        </span>
                      </th>
                      <td>
                        <Permissions s={s} />
                      </td>
                      <td className={styles.num}>{s.documents.toLocaleString("en-GB")}</td>
                      <td>
                        <AdminSync s={s} />
                      </td>
                      <td>
                        <FailureSummary s={s} />
                      </td>
                      <td className={styles.action}>
                        <SyncButton s={s} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className={styles.cards} aria-label="Connected sources and their sync status">
              {sources.map((s) => (
                <li key={s.id} className={styles.card}>
                  <span>
                    <span className={styles.primary}>{s.name}</span>
                    <span className={`${styles.secondary} ${styles.mono}`}>
                      {s.key} · {connectorLabel(s.connector_type)}
                    </span>
                  </span>
                  <dl>
                    <dt>Permissions</dt>
                    <dd>
                      <Permissions s={s} />
                    </dd>
                    <dt>Documents</dt>
                    <dd>{s.documents.toLocaleString("en-GB")}</dd>
                    <dt>Last sync</dt>
                    <dd>
                      <AdminSync s={s} />
                    </dd>
                    <dt>Failed</dt>
                    <dd>
                      <FailureSummary s={s} />
                    </dd>
                  </dl>
                  <div>
                    <SyncButton s={s} />
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
      {failing.length > 0 && (
        <section className={styles.section} aria-labelledby="failures-label">
          <SectionLabel id="failures-label">Failed records in the last sync</SectionLabel>
          <p className={styles.note}>
            Each failure is a stage and a fixed code. A record is named only if you can read its document; otherwise it is
            shown by an opaque reference. Record contents, titles and file names are never shown here.
          </p>
          {failing.map((s) => (
            <SourceFailures key={s.id} s={s} open={open === s.id} onToggle={() => setOpen(open === s.id ? null : s.id)} />
          ))}
        </section>
      )}
    </>
  );
}

function Permissions({ s }: { s: AdminSource }) {
  return (
    <>
      <span className={styles.primary}>{s.account_scoped ? "Account-scoped" : "From source metadata"}</span>
      <span className={styles.secondary}>Default {levelLabel(s.default_sensitivity)}</span>
    </>
  );
}

function AdminSync({ s }: { s: AdminSource }) {
  const run = s.last_run;
  return (
    <>
      <RunStatus status={run?.status ?? null} code={run?.error_code ?? null} guard={run?.deletion_guard_tripped ?? false} />
      <When iso={run?.finished_at ?? s.last_synced_at} />
      {s.acl_synced_at && <span className={styles.secondary}>Permissions synced {formatDay(s.acl_synced_at)}</span>}
    </>
  );
}

function FailureSummary({ s }: { s: AdminSource }) {
  const total = s.last_run?.counts["failed"] ?? 0;
  const embedding = s.last_run?.counts["embedding_failures"] ?? 0;
  if (!s.last_run) return <span className={styles.off}>—</span>;
  if (total === 0 && embedding === 0) return <span className={styles.off}>None</span>;
  const top = s.failure_codes[0];
  return (
    <>
      <span className={styles.primary}>
        {total > 0 ? `${total} ${total === 1 ? "record" : "records"}` : `${embedding} embedding ${embedding === 1 ? "failure" : "failures"}`}
      </span>
      {top && (
        <span className={`${styles.secondary} ${styles.mono}`}>
          <Code value={top.code} />
        </span>
      )}
    </>
  );
}

function SyncButton({ s }: { s: AdminSource }) {
  const request = useRequestSync();
  const active = s.job?.status === "queued" || s.job?.status === "running";
  const inactive = s.status !== "active";
  const failedRequest = request.isError && request.variables === s.id;
  const label = active ? (s.job?.status === "running" ? "Syncing…" : "Queued") : "Sync now";
  return (
    <span className={styles.syncCell}>
      <Button
        size="sm"
        variant="secondary"
        icon={<Icon as={active ? Loader2 : RefreshCw} />}
        onClick={() => request.mutate(s.id)}
        disabled={active || inactive || (request.isPending && request.variables === s.id)}
        aria-label={`${label}: ${s.name}`}
      >
        {label}
      </Button>
      {inactive && <span className={styles.secondary}>{humanize(s.status)}</span>}
      {!active && s.job?.status === "failed" && (
        <span className={`${styles.secondary} ${styles.warn}`}>Last request failed · {s.job.error_code ?? "error"}</span>
      )}
      {failedRequest && (
        <span className={`${styles.secondary} ${styles.warn}`} role="alert">
          Couldn&apos;t request a sync. Try again.
        </span>
      )}
    </span>
  );
}

function SourceFailures({ s, open, onToggle }: { s: AdminSource; open: boolean; onToggle: () => void }) {
  const panel = `failures-${s.id}`;
  return (
    <div className={styles.failureGroup}>
      <div className={styles.failureHead}>
        <span>
          <span className={styles.primary}>{s.name}</span>
          <span className={`${styles.secondary} ${styles.codes}`}>
            {s.failure_codes.map((c) => (
              <span key={`${c.stage}-${c.code}`} className={styles.code}>
                {c.stage} · {c.code} × {c.count}
              </span>
            ))}
          </span>
        </span>
        <Button size="sm" variant="quiet" aria-expanded={open} aria-controls={panel} onClick={onToggle}>
          {open ? "Hide records" : "Show records"}
        </Button>
      </div>
      <div id={panel} hidden={!open}>
        {open && <FailureRecords sourceId={s.id} />}
      </div>
    </div>
  );
}

function FailureRecords({ sourceId }: { sourceId: string }) {
  const query = useSourceFailures(sourceId);
  if (query.isError) {
    return <ErrorState error={query.error} title="Couldn't load failed records" onRetry={() => query.refetch()} retrying={query.isFetching} />;
  }
  if (!query.data) return <Loading label="Loading failed records" />;
  const failures = query.data.failures;
  if (failures.length === 0) return <p className={styles.note}>No failed records in the last sync.</p>;
  return (
    <ul className={styles.records}>
      {failures.map((f, i) => (
        <li key={`${f.record}-${f.code}-${i}`} className={styles.record}>
          <span className={styles.mono}>{f.stage}</span>
          <span className={styles.mono}>{f.code}</span>
          <RecordRef f={f} />
          <Params f={f} />
        </li>
      ))}
    </ul>
  );
}

function RecordRef({ f }: { f: SyncFailure }) {
  return f.record_kind === "identifier" ? (
    <span className={styles.mono}>{f.record}</span>
  ) : (
    <span className={`${styles.mono} ${styles.off}`} title="Opaque reference: you can't read this record's document">
      {f.record}
    </span>
  );
}

function Params({ f }: { f: SyncFailure }) {
  const entries = Object.entries(f.params);
  if (entries.length === 0) return <span />;
  return (
    <span className={styles.secondary}>
      {entries.map(([k, v]) => `${humanize(k)}: ${String(v)}`).join(" · ")}
    </span>
  );
}

/* --- shared ---------------------------------------------------------------------------- */

const STATUS_ICON = { succeeded: CheckCircle2, partial: AlertTriangle, failed: XCircle, aborted: XCircle } as const;

function RunStatus({ status, code = null, guard = false }: { status: string | null; code?: string | null; guard?: boolean }) {
  const tone = status === "succeeded" ? styles.ok : status === "partial" ? styles.warn : status ? styles.bad : styles.off;
  const icon = status ? (STATUS_ICON[status as keyof typeof STATUS_ICON] ?? CircleSlash) : CircleSlash;
  return (
    <span className={styles.statusLine}>
      <span className={`${styles.state} ${tone}`}>
        <Icon as={icon} size={14} />
        {syncStatusLabel(status)}
      </span>
      {code && (
        <span className={`${styles.secondary} ${styles.mono}`}>
          <Code value={code} />
        </span>
      )}
      {guard && <span className={`${styles.secondary} ${styles.warn}`}>Deletion guard held back deletions</span>}
    </span>
  );
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

function When({ iso }: { iso: string | null | undefined }) {
  const day = formatDay(iso);
  if (!day || !iso) return null;
  return (
    <time className={styles.secondary} dateTime={iso} title={formatDayTime(iso) ?? undefined}>
      {day}
    </time>
  );
}

function Loading({ label }: { label: string }) {
  return (
    <div aria-busy="true">
      <span className="visually-hidden" role="status">
        {label}
      </span>
      <SkeletonLines widths={["100%", "100%", "70%"]} />
    </div>
  );
}
