"use client";

import { CornerDownLeft } from "lucide-react";
import Link from "next/link";

import { useAccounts, useActivity, useSources } from "@/api/queries";
import type { MemberSource } from "@/api/types";
import { Button } from "@/components/primitives/Button";
import { DocumentGlyph, SOURCE_GLYPHS } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { StateBlock } from "@/components/primitives/StateBlock";
import { record } from "@/components/record/RecordPage";
import { shortStatus } from "@/components/shell/status-labels";
import { formatCount, formatDay } from "@/lib/format";

import styles from "./AskDesk.module.css";

const TYPE_OF: Record<string, keyof typeof SOURCE_GLYPHS> = { document_repository: "doc", ticketing: "ticket", chat_export: "chat" };

/** The desk's heading: what Ask is, in one line, before the composer. */
export function DeskHeading({ organization }: { organization: string }) {
  return (
    <header className={`${record.text} ${styles.heading}`}>
      <h1 className={styles.display}>Ask what {organization} knows.</h1>
      <p className={styles.deck}>
        Answers are drafted only from the docs, tickets and conversations you&apos;re allowed to read — every statement dated, cited and checked
        against its source, or not shown at all.
      </p>
    </header>
  );
}

/**
 * Under the composer on the desk: what Lattice will search for this person (their reach,
 * as GET /v1/sources and GET /v1/accounts report it) and their own recent questions (GET
 * /v1/activity). Clearance isn't shown: /v1/me doesn't return it, and the UI doesn't guess.
 */
export function DeskContext({ isAdmin, onAskAgain }: { isAdmin: boolean; onAskAgain: (question: string) => void }) {
  const sources = useSources();
  const empty = sources.data?.sources.length === 0;
  if (empty) {
    return (
      <div className={record.text}>
        <StateBlock family="quiet" heading="Lattice hasn't indexed any sources you can access yet." headingLevel="h2" compact role="status">
          <p>
            {isAdmin ? (
              <>
                Check which sources have synced on the <Link href="/sources">Sources</Link> page.
              </>
            ) : (
              "Once an administrator connects a source holding content you can read, you can ask about it here."
            )}
          </p>
        </StateBlock>
      </div>
    );
  }
  return (
    <div className={`${record.text} ${styles.context}`}>
      <Reach sources={sources.data?.sources} failed={sources.isError} />
      <Recent onAskAgain={onAskAgain} />
    </div>
  );
}

function Reach({ sources, failed }: { sources: readonly MemberSource[] | undefined; failed: boolean }) {
  const accounts = useAccounts();
  return (
    <section className={styles.panel} aria-labelledby="reach-heading" data-testid="your-reach" aria-busy={(!sources && !failed) || undefined}>
      <h2 className={styles.panelHeading} id="reach-heading">
        Your reach
        {sources && <span className={styles.panelCount}>{formatCount(sources.length, "source", "sources")}</span>}
      </h2>
      {failed ? (
        <p className={styles.quiet}>Your sources couldn&apos;t be listed just now. Asking still searches everything you can read.</p>
      ) : !sources ? (
        <ul className={styles.rows} aria-hidden="true">
          {[0, 1].map((i) => (
            <li key={i} className={styles.skeletonRow} />
          ))}
        </ul>
      ) : (
        <ul className={styles.rows}>
          {sources.map((s) => {
            const Glyph = SOURCE_GLYPHS[TYPE_OF[s.connector_type] ?? "doc"] ?? DocumentGlyph;
            const synced = formatDay(s.last_succeeded_at);
            return (
              <li key={s.id} className={styles.source}>
                <span className={styles.glyph}>
                  <Glyph size={14} aria-hidden="true" focusable="false" />
                </span>
                <span className={styles.sourceName}>{s.name}</span>
                <span className={styles.sourceMeta}>
                  {formatCount(s.readable_documents, "document", "documents")} you can read · {synced ? `synced ${synced}` : "not synced yet"}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {accounts.data && (
        <p className={styles.accounts}>
          <span className={styles.accountsLabel}>Accounts</span>{" "}
          {accounts.data.account_scope === "all"
            ? "Every account"
            : accounts.data.accounts.length === 0
              ? "None assigned — content not tied to an account"
              : accounts.data.accounts.map((a) => a.name).join(", ")}
        </p>
      )}
    </section>
  );
}

const RECENT = 5;

/** The person's own last questions; each opens its trace (re-authorized) or goes back in the box. */
function Recent({ onAskAgain }: { onAskAgain: (question: string) => void }) {
  const activity = useActivity();
  const items = activity.data?.pages[0]?.items.slice(0, RECENT) ?? [];
  if (!items.length) return null;
  return (
    <section className={styles.panel} aria-labelledby="recent-heading" data-testid="recent-questions">
      <h2 className={styles.panelHeading} id="recent-heading">
        Your recent questions
      </h2>
      <ol className={styles.rows}>
        {items.map((q) => (
          <li key={q.trace_id} className={styles.recent}>
            <span className={styles.recentStatus}>{shortStatus(q.status)}</span>
            <Link className={styles.recentQuestion} href={`/traces/${q.trace_id}`} title={q.question}>
              {q.question}
            </Link>
            <span className={styles.recentDate}>{formatDay(q.created_at)}</span>
            <Button size="sm" variant="quiet" icon={<Icon as={CornerDownLeft} />} aria-label={`Ask again: ${q.question}`} title="Put this question back in the box" onClick={() => onAskAgain(q.question)} />
          </li>
        ))}
      </ol>
    </section>
  );
}
