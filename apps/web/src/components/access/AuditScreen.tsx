"use client";

import Link from "next/link";

import { isApiError } from "@/api/errors";
import { useAuditEvents } from "@/api/queries";
import type { AuditEvent } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { connectorLabel, formatDayTime, humanize } from "@/lib/format";

import styles from "./Access.module.css";
import { AccessFrame, AccessTabs, Loading } from "./AccessFrame";
import { actionLabel, actorLabel, auditPhrase } from "./copy";

/**
 * The audit log (GET /v1/admin/audit-events): every access and AI-provider change, who
 * made it and why, newest first. An event about a document is listed only while you can
 * read that document: the API leaves out the others entirely.
 */
export function AuditScreen() {
  const { isAdmin } = useWorkspace();
  const query = useAuditEvents(isAdmin);
  const forbidden = query.isError && isApiError(query.error) && query.error.status === 403;
  const events = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <AccessFrame trail={[{ label: "Audit log" }]} forbidden={forbidden}>
      <header className={styles.head}>
        <h1 className={styles.title}>Audit log</h1>
        <p className={styles.deck}>
          Every change to who can read what, and to the AI data policy: who made it, when and why. Changes about
          documents you can&apos;t read aren&apos;t listed. Entries can&apos;t be edited or deleted.
        </p>
      </header>
      <AccessTabs />
      <section className={styles.section} aria-labelledby="events-label">
        <SectionLabel id="events-label">Changes, newest first</SectionLabel>
        {query.isError && !forbidden ? (
          <ErrorState error={query.error} title="Couldn't load the audit log" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : !query.data ? (
          <Loading label="Loading the audit log" />
        ) : events.length === 0 ? (
          <div className={styles.empty}>
            <p className={styles.emptyTitle}>No changes recorded yet</p>
            <p>Access and AI-provider changes appear here as they are made.</p>
          </div>
        ) : (
          <>
            <ol className={styles.events}>
              {events.map((e) => (
                <Event key={e.id} event={e} />
              ))}
            </ol>
            {query.hasNextPage && (
              <div className={styles.more}>
                <Button onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
                  Show older changes
                </Button>
              </div>
            )}
          </>
        )}
      </section>
    </AccessFrame>
  );
}

function Event({ event: e }: { event: AuditEvent }) {
  return (
    <li className={styles.event}>
      <time className={styles.eventWhen} dateTime={e.created_at}>
        {formatDayTime(e.created_at)}
      </time>
      <div className={styles.eventBody}>
        <p className={styles.eventHead}>
          <span className={styles.primary}>{actionLabel(e.action)}</span>
          {e.member && (
            <span>
              {e.member.name ? <Link href={`/access/members/${e.member.id}`}>{e.member.name}</Link> : <span className={styles.off}>A former member</span>}
            </span>
          )}
        </p>
        <Subject event={e} />
        <p className={styles.secondary}>
          By {actorLabel(e)} · <span className={styles.reason}>“{e.reason}”</span>
        </p>
      </div>
    </li>
  );
}

/** Settings values as recorded; booleans in words. */
function detailValue(value: string | null): string {
  if (value === null) return "—";
  if (value === "true") return "Yes";
  if (value === "false") return "No";
  return value;
}

function Subject({ event: e }: { event: AuditEvent }) {
  if (e.document) {
    return (
      <p className={styles.reason}>
        {e.document.title}
        <span className={styles.secondary}>
          {[e.document.display_ref, e.document.source_name, connectorLabel(e.document.connector_type)].filter(Boolean).join(" · ")}
        </span>
      </p>
    );
  }
  if (e.effects.length > 0) {
    return (
      <ul className={styles.changes}>
        {e.effects.map((x, i) => (
          <li key={`${x.kind}-${x.subject?.id ?? i}`}>{auditPhrase(x)}</li>
        ))}
      </ul>
    );
  }
  if (e.details.length > 0) {
    return (
      <ul className={styles.changes}>
        {e.details.map((d) => (
          <li key={d.field}>
            {humanize(d.field)}: {d.before !== null ? `${detailValue(d.before)} → ` : ""}
            {detailValue(d.after)}
          </li>
        ))}
      </ul>
    );
  }
  return null;
}
