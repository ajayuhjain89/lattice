"use client";

import { EyeOff } from "lucide-react";
import Link from "next/link";

import { useActivity, useAdminActivity } from "@/api/queries";
import type { OperationalActivity, OwnActivity } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { SkeletonLines } from "@/components/primitives/Skeleton";
import { PageHeader } from "@/components/shell/PageHeader";
import { shortStatus } from "@/components/shell/status-labels";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { formatDayTime, formatDuration, humanize } from "@/lib/format";

import styles from "./ActivityScreen.module.css";

/**
 * Activity (design-system B11). Everyone: their own questions (GET /v1/activity), each
 * opening its trace, which the API re-checks against current access. Administrators also
 * see the organization's Asks as operational rows (GET /v1/admin/activity): member, time,
 * status, provider, duration -- never a question, answer, evidence, count or scope (D-5).
 */
export function ActivityScreen() {
  const { isAdmin } = useWorkspace();
  return (
    <>
      <PageHeader trail={[{ label: "Activity" }]} />
      <main id="main" className={styles.main} tabIndex={-1}>
        <div className={styles.column}>
          <header className={styles.head}>
            <h1 className={styles.title}>Activity</h1>
            <p className={styles.deck}>
              Your recent questions. Each opens its trace, checked again against your current access: an answer that relied
              on something you can no longer read stays hidden.
            </p>
          </header>
          <OwnQuestions />
          {isAdmin && <OrganizationActivity />}
        </div>
      </main>
    </>
  );
}

function OwnQuestions() {
  const query = useActivity();
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <section className={styles.section} aria-labelledby="own-label">
      <SectionLabel id="own-label">Your questions</SectionLabel>
      {query.isError ? (
        <ErrorState error={query.error} title="Couldn't load your questions" onRetry={() => query.refetch()} retrying={query.isFetching} />
      ) : !query.data ? (
        <Loading label="Loading your questions" />
      ) : items.length === 0 ? (
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>You haven&apos;t asked anything yet</p>
          <p>
            Questions you ask appear here. <Link href="/ask">Ask a question</Link>
          </p>
        </div>
      ) : (
        <>
          <ol className={styles.list}>
            {items.map((item) => (
              <OwnRow key={item.trace_id} item={item} />
            ))}
          </ol>
          <More query={query} />
        </>
      )}
    </section>
  );
}

function OwnRow({ item }: { item: OwnActivity }) {
  return (
    <li className={styles.row}>
      <Link href={`/traces/${item.trace_id}`} className={styles.question} title={item.question}>
        {item.question}
      </Link>
      <span className={styles.meta}>
        <span>{shortStatus(item.status)}</span>
        <time dateTime={item.created_at}>{formatDayTime(item.created_at)}</time>
      </span>
    </li>
  );
}

function OrganizationActivity() {
  const query = useAdminActivity(true);
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <section className={styles.section} aria-labelledby="org-label">
      <SectionLabel id="org-label">Organization activity</SectionLabel>
      <p className={styles.notice}>
        <span className={styles.noticeIcon}>
          <Icon as={EyeOff} />
        </span>
        Content of other members&apos; questions isn&apos;t visible to administrators: this view shows who asked, when, and how
        the request ran — never the question, the answer or what it found.
      </p>
      {query.isError ? (
        <ErrorState error={query.error} title="Couldn't load activity" onRetry={() => query.refetch()} retrying={query.isFetching} />
      ) : !query.data ? (
        <Loading label="Loading activity" />
      ) : items.length === 0 ? (
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>No questions have been asked yet</p>
        </div>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className="visually-hidden">Organization activity (operational view)</caption>
              <thead>
                <tr>
                  <th scope="col">Member</th>
                  <th scope="col">Time</th>
                  <th scope="col">Status</th>
                  <th scope="col">Provider · model</th>
                  <th scope="col" className={styles.num}>
                    Duration
                  </th>
                  <th scope="col" className={styles.num}>
                    Tokens in / out
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.trace_id}>
                    <th scope="row" className={styles.rowHead}>
                      <Link href={`/traces/${item.trace_id}`}>{item.member_name}</Link>
                    </th>
                    <td>
                      <time dateTime={item.created_at}>{formatDayTime(item.created_at)}</time>
                    </td>
                    <td>
                      <OperationalStatus item={item} />
                    </td>
                    <td>
                      <Provider item={item} />
                    </td>
                    <td className={styles.num}>{formatDuration(item.duration_ms) ?? "—"}</td>
                    <td className={styles.num}>
                      {item.input_tokens.toLocaleString("en-GB")} / {item.output_tokens.toLocaleString("en-GB")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className={styles.cards} aria-label="Organization activity (operational view)">
            {items.map((item) => (
              <li key={item.trace_id} className={styles.card}>
                <Link href={`/traces/${item.trace_id}`} className={styles.primary}>
                  {item.member_name}
                </Link>
                <dl>
                  <dt>Time</dt>
                  <dd>
                    <time dateTime={item.created_at}>{formatDayTime(item.created_at)}</time>
                  </dd>
                  <dt>Status</dt>
                  <dd>
                    <OperationalStatus item={item} />
                  </dd>
                  <dt>Provider</dt>
                  <dd>
                    <Provider item={item} />
                  </dd>
                  <dt>Duration</dt>
                  <dd>{formatDuration(item.duration_ms) ?? "—"}</dd>
                </dl>
              </li>
            ))}
          </ul>
          <More query={query} />
        </>
      )}
    </section>
  );
}

function OperationalStatus({ item }: { item: OperationalActivity }) {
  return (
    <>
      <span className={styles.primary}>{shortStatus(item.status)}</span>
      {(item.error_code ?? item.status_reason) && (
        <span className={`${styles.secondary} ${styles.mono}`}>{item.error_code ?? humanize(item.status_reason ?? "")}</span>
      )}
    </>
  );
}

function Provider({ item }: { item: OperationalActivity }) {
  // A configured provider that was not called (policy, kill switch, no evidence) is not
  // shown as if it had answered.
  if (!item.provider || item.generation_calls === 0) return <span className={styles.off}>No model call</span>;
  return (
    <>
      <span>{item.provider}</span>
      <span className={`${styles.secondary} ${styles.mono}`}>
        {item.model}
        {item.generation_calls > 1 ? ` · ${item.generation_calls} calls` : ""}
      </span>
    </>
  );
}

function More({ query }: { query: { hasNextPage: boolean; isFetchingNextPage: boolean; fetchNextPage: () => unknown } }) {
  if (!query.hasNextPage) return null;
  return (
    <div>
      <Button size="sm" variant="secondary" onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
        Show older
      </Button>
    </div>
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
