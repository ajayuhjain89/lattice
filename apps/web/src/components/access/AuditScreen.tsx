"use client";

import { KeyRound, Lock, SquareTerminal, UserRound, type LucideIcon } from "lucide-react";
import Link from "next/link";

import { isApiError } from "@/api/errors";
import { useAuditEvents } from "@/api/queries";
import type { AuditEvent } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { StateBlock } from "@/components/primitives/StateBlock";
import { AdminGate, OpsHead, OpsPage } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { groupByDay } from "@/lib/chronology";
import { dayHeading, formatCount, formatDay, formatDayTime, formatTime, humanize } from "@/lib/format";
import { useNow } from "@/lib/use-now";

import styles from "./Audit.module.css";
import { AccessViews } from "./AccessViews";
import { actionLabel, actorLabel, auditPhrase } from "./copy";
import { DocumentLine } from "./DocumentSearch";

/**
 * The audit log (GET /v1/admin/audit-events) as an immutable ledger: every access,
 * identity and AI-provider change -- when, what, to whom, by whom, and why -- newest
 * first, ruled by day. An event about a document is listed only while you can read that
 * document: the API leaves the others out entirely, before paging, so nothing here (a
 * gap, a count, a page boundary) stands for an event you can't see.
 */
export function AuditScreen() {
  const { isAdmin } = useWorkspace();
  const query = useAuditEvents(isAdmin);
  const now = useNow();
  const forbidden = query.isError && isApiError(query.error) && query.error.isForbidden;
  const events = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <OpsPage aria-labelledby="page-title">
      <AdminGate forbidden={forbidden} body="Access management is available to your organization's administrators. Being an administrator never gives access to content.">
        <OpsHead title="Audit log" deck="Every change to who can read what, to identities and to the AI data policy: who made it, when and why.">
          <AccessViews />
        </OpsHead>
        <p className={styles.immutable}>
          <Icon as={Lock} size={14} />
          <span>Entries can&apos;t be edited or deleted. Changes about documents you can&apos;t read aren&apos;t listed.</span>
        </p>
        {query.isError ? (
          <ErrorState error={query.error} title="Couldn't load the audit log" onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : !query.data ? (
          <RuleSkeleton variant="ledger" rows={4} label="Loading the audit log" />
        ) : events.length === 0 ? (
          <StateBlock family="quiet" heading="No changes recorded yet">
            <p>Access, identity and AI-provider changes appear here as they are made, each with who made it and why.</p>
          </StateBlock>
        ) : (
          <section aria-labelledby="ledger-heading" className={styles.ledger}>
            <h2 className="visually-hidden" id="ledger-heading">
              Changes, newest first
            </h2>
            <ol className={styles.days}>
              {groupByDay(events, (e) => e.created_at).map((day) => {
                const heading = dayHeading(day.first, now);
                const relative = heading === "Today" || heading === "Yesterday";
                return (
                  <li key={day.key} className={styles.day}>
                    <h3 className={styles.dayHead}>
                      <span>{heading}</span>
                      <span className={styles.dayMeta}>
                        {relative ? `${formatDay(day.first)} · ` : ""}
                        {formatCount(day.items.length, "change", "changes")}
                      </span>
                    </h3>
                    <ol className={styles.entries}>
                      {day.items.map((e) => (
                        <Entry key={e.id} event={e} />
                      ))}
                    </ol>
                  </li>
                );
              })}
            </ol>
            <div className={styles.footer}>
              {query.hasNextPage ? (
                <Button onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
                  Show older changes
                </Button>
              ) : (
                <p className={styles.end}>No older changes.</p>
              )}
            </div>
          </section>
        )}
      </AdminGate>
    </OpsPage>
  );
}


const ACTOR_ICON: Record<AuditEvent["actor"]["kind"], LucideIcon> = { member: UserRound, operator: SquareTerminal, identity_provider: KeyRound };

function Entry({ event: e }: { event: AuditEvent }) {
  return (
    <li className={styles.entry} data-testid="audit-entry">
      <time className={styles.time} dateTime={e.created_at} title={formatDayTime(e.created_at) ?? undefined}>
        {formatTime(e.created_at, { seconds: true })}
      </time>
      <p className={styles.what}>
        <span className={styles.action}>{actionLabel(e.action)}</span>
        {e.member &&
          (e.member.name ? (
            <Link href={`/access/members/${e.member.id}`} className={styles.subject}>
              {e.member.name}
            </Link>
          ) : (
            <span className={styles.former}>A former member</span>
          ))}
      </p>
      <div className={styles.detail}>
        <Subject event={e} />
      </div>
      <p className={styles.by}>
        <Icon as={ACTOR_ICON[e.actor.kind]} size={14} />
        <span>
          By {actorLabel(e)}
          <span className={styles.reason}> · “{e.reason}”</span>
        </span>
      </p>
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
  if (e.document) return <DocumentLine doc={e.document} />;
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
      <dl className={styles.fields}>
        {e.details.map((d) => (
          <div key={d.field} className={styles.field}>
            <dt>{humanize(d.field)}</dt>
            <dd>
              {d.before !== null && (
                <>
                  <span className={styles.value}>{detailValue(d.before)}</span>
                  <span aria-hidden="true"> → </span>
                  <span className="visually-hidden"> changed to </span>
                </>
              )}
              <span className={styles.value}>{detailValue(d.after)}</span>
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return null;
}

