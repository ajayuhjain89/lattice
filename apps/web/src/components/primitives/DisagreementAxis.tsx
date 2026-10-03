/**
 * The disagreement axis (design-direction §5.5): positions laid out oldest → newest on a
 * date axis, with equal weight. Chronology is shown, never crowned: the order and the
 * interval ("35 days later") are facts, and the conclusion sentence -- the API's, e.g.
 * "Lattice can't confirm which is current" -- is the only verdict, unchanged.
 *
 * Positions are spaced evenly (a proportional scale would turn dates into distances and
 * imply weight); below 768 px, or with more than three positions, the axis turns vertical. `superseded` is the neutral form,
 * used only when a source records the replacement.
 */
import { GitCompare, GitFork } from "lucide-react";
import type { ReactNode } from "react";

import styles from "./DisagreementAxis.module.css";
import { Icon } from "./icons";

export interface AxisPosition {
  key: string;
  /** ISO timestamp, or null when the source is undated. */
  at: string | null;
  /** The formatted date ("14 Aug 2026") or "Undated". */
  dateLabel: string;
  /** Source kind and authority ("Ticket · Working document"). */
  source: ReactNode;
  title?: ReactNode;
  /** The position itself: the claim text and its exhibit markers. */
  children: ReactNode;
}

const DAY = 86_400_000;

function interval(from: string | null, to: string | null): string | null {
  if (!from || !to) return null;
  const days = Math.round((Date.parse(to) - Date.parse(from)) / DAY);
  if (!Number.isFinite(days) || days < 0) return null;
  if (days === 0) return "Same day";
  return `${days.toLocaleString("en-GB")} ${days === 1 ? "day" : "days"} later`;
}

export function DisagreementAxis({
  kind = "potential_conflict",
  topic,
  positions,
  conclusion,
  actions,
  headingLevel: Heading = "h3",
  id,
}: {
  kind?: "potential_conflict" | "superseded";
  topic?: ReactNode;
  positions: readonly AxisPosition[];
  conclusion: ReactNode;
  actions?: ReactNode;
  headingLevel?: "h2" | "h3" | "h4";
  id?: string;
}) {
  const label = kind === "superseded" ? "Superseded" : "Potential conflict";
  const headingId = id ? `${id}-heading` : undefined;
  return (
    <section
      className={[styles.axis, kind === "superseded" && styles.superseded, positions.length > 3 && styles.vertical]
        .filter(Boolean)
        .join(" ")}
      aria-labelledby={headingId}
      id={id}
      data-kind={kind}
      style={{ "--positions": Math.max(positions.length, 1) } as React.CSSProperties}
    >
      <Heading className={styles.heading} id={headingId}>
        <Icon as={kind === "superseded" ? GitCompare : GitFork} size={14} />
        <span className={styles.kind}>{label}</span>
        {topic && (
          <>
            <span className={styles.sep} aria-hidden="true">
              ·
            </span>
            <span className="visually-hidden">:</span> <span className={styles.topic}>{topic}</span>
          </>
        )}
      </Heading>
      {positions.length > 0 && (
      <ol className={styles.positions}>
        {positions.map((p, i) => {
          const later = i > 0 ? interval(positions[i - 1]!.at, p.at) : null;
          return (
            <li key={p.key} className={styles.position}>
              <span className={styles.dot} aria-hidden="true" />
              <p className={styles.when}>
                {p.at ? <time dateTime={p.at}>{p.dateLabel}</time> : <span>{p.dateLabel}</span>}
                {later && <span className={styles.later}>{later}</span>}
              </p>
              <p className={styles.source}>{p.source}</p>
              {p.title && <p className={styles.title}>{p.title}</p>}
              <div className={styles.claim}>{p.children}</div>
            </li>
          );
        })}
      </ol>
      )}
      <div className={styles.conclusion}>{conclusion}</div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </section>
  );
}
