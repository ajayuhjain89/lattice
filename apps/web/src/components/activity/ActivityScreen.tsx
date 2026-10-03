"use client";

import { CornerDownLeft, XCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { useActivity } from "@/api/queries";
import type { OwnActivity } from "@/api/types";
import { Button, ButtonLink } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { StateBlock } from "@/components/primitives/StateBlock";
import { OpsHead, OpsPage } from "@/components/ops/OpsPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { ErrorState } from "@/components/states/ErrorState";
import { RuleSkeleton } from "@/components/primitives/RuleSkeleton";
import { groupByDay } from "@/lib/chronology";
import { dayHeading, formatCount, formatDay, formatDayTime, formatTime } from "@/lib/format";
import { useNow } from "@/lib/use-now";

import styles from "./ActivityScreen.module.css";
import { ActivityViews } from "./ActivityViews";
import { questionStatus, traceHref } from "./questions";

/**
 * Activity (design-direction §6.7): the questions you asked, as a chronology -- grouped by
 * day, newest first, each at its time. GET /v1/activity returns only the viewer's own
 * questions: the question, its status and when. Opening one loads its trace, which the API
 * checks again against current access (an answer that relied on something no longer
 * readable stays hidden there); nothing of the answer is shown or kept here.
 */
export function ActivityScreen() {
  return (
    <OpsPage aria-labelledby="page-title">
      <OpsHead title="Activity" deck="Every question you've asked, newest first. Opening one shows its trace, checked again against what you can read now.">
        <ActivityViews />
      </OpsHead>
      <Questions />
    </OpsPage>
  );
}


function Questions() {
  const query = useActivity();
  const now = useNow();
  const { state } = useWorkspace();
  const router = useRouter();
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  const askAgain = (question: string) => {
    state.prefill.set(question);
    router.push("/ask");
  };

  if (query.isError) {
    return <ErrorState error={query.error} title="Couldn't load your questions" onRetry={() => query.refetch()} retrying={query.isFetching} />;
  }
  if (!query.data) return <RuleSkeleton variant="ledger" rows={3} label="Loading your questions" />;
  if (items.length === 0) {
    return (
      <StateBlock family="quiet" heading="You haven't asked anything yet" actions={<ButtonLink href="/ask">Ask a question</ButtonLink>}>
        <p>
          Activity keeps each question you ask with its status and time. Opening one shows how it was answered — the scope, the
          steps and the evidence — re-checked against what you can read at that moment.
        </p>
      </StateBlock>
    );
  }
  return (
    <section aria-labelledby="questions-heading" className={styles.questions}>
      <h2 className="visually-hidden" id="questions-heading">
        Your questions
      </h2>
      <ol className={styles.days}>
        {groupByDay(items, (q) => q.created_at).map((day) => {
          const heading = dayHeading(day.first, now);
          const relative = heading === "Today" || heading === "Yesterday";
          return (
          <li key={day.key} className={styles.day}>
            <h3 className={styles.dayHead}>
              <span>{heading}</span>
              <span className={styles.dayMeta}>
                {/* The absolute date beside Today / Yesterday, and the day's count. */}
                {relative ? `${formatDay(day.first)} · ` : ""}
                {formatCount(day.items.length, "question", "questions")}
              </span>
            </h3>
            <ol className={styles.entries}>
              {day.items.map((q) => (
                <Entry key={q.trace_id} q={q} onAskAgain={askAgain} />
              ))}
            </ol>
          </li>
          );
        })}
      </ol>
      <div className={styles.footer}>
        {query.hasNextPage ? (
          <Button onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
            Show older questions
          </Button>
        ) : (
          <p className={styles.end}>No older questions.</p>
        )}
      </div>
    </section>
  );
}

function Entry({ q, onAskAgain }: { q: OwnActivity; onAskAgain: (question: string) => void }) {
  const failed = q.status === "failed";
  return (
    <li className={styles.entry}>
      <time className={styles.time} dateTime={q.created_at} title={formatDayTime(q.created_at) ?? undefined}>
        {formatTime(q.created_at)}
      </time>
      <span className={[styles.status, failed && styles.failed].filter(Boolean).join(" ")}>
        {failed && <Icon as={XCircle} size={14} />}
        {questionStatus(q)}
      </span>
      <Link className={styles.question} href={traceHref(q)}>
        {q.question}
      </Link>
      <Button
        size="sm"
        variant="quiet"
        className={styles.again}
        icon={<Icon as={CornerDownLeft} />}
        aria-label={`Ask again: ${q.question}`}
        title="Put this question back in the box"
        onClick={() => onAskAgain(q.question)}
      />
    </li>
  );
}

