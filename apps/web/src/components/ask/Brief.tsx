"use client";

import { AlertTriangle, Clock, GitCompare, Info, Lock, Route, XCircle } from "lucide-react";
import { memo } from "react";

import type { Answer, EvidenceItem, Notice } from "@/api/types";
import { ButtonLink, Button } from "@/components/primitives/Button";
import { Callout } from "@/components/primitives/Callout";
import { Icon } from "@/components/primitives/icons";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import type { RenderConflict, RenderPosition } from "@/lib/answer-model";
import { presentStatus } from "@/lib/answer-status";
import { authorityLabel, formatCount, formatDay, formatDuration, levelLabel, sourceTypeLabel } from "@/lib/format";

import { useAnswer } from "./answer-context";
import styles from "./Brief.module.css";
import { ClaimText, Chips } from "./Citations";

/** "ANSWERED · 3 SOURCES CITED · NEWEST 18 SEP 2026 · 4.2 S"  [Trace] */
export function StatusLine({ answer, elapsedMs, traceHref }: { answer: Answer; elapsedMs?: number; traceHref?: string }) {
  const { model } = useAnswer();
  const p = presentStatus(answer.status, answer.status_reason);
  const cited = model.numberOf.size;
  const newest = formatDay(cited ? answer.freshness.newest_cited_at : answer.freshness.newest_evidence_at);
  const StatusIcon = p.tone === "error" ? XCircle : p.tone === "warning" ? AlertTriangle : p.tone === "info" ? Info : null;
  return (
    <div className={styles.statusLine}>
      <p className={styles.facts}>
        <span className={`${styles.fact} ${styles.status} ${p.tone !== "neutral" ? styles[`tone${p.tone.charAt(0).toUpperCase()}${p.tone.slice(1)}`] : ""}`}>
          {StatusIcon && <Icon as={StatusIcon} size={14} />}
          {p.label}
        </span>
        {cited > 0 ? (
          <span className={styles.fact}>{formatCount(cited, "source cited", "sources cited")}</span>
        ) : answer.evidence.length > 0 && answer.status !== "ambiguous" ? (
          <span className={styles.fact}>{formatCount(answer.evidence.length, "source found", "sources found")}</span>
        ) : null}
        {newest && <span className={styles.fact}>Newest {newest}</span>}
        {answer.question_sensitivity !== "internal" && (
          <span className={styles.fact}>Question {levelLabel(answer.question_sensitivity)}</span>
        )}
        {elapsedMs !== undefined && <span className={styles.fact}>{formatDuration(elapsedMs)}</span>}
      </p>
      {traceHref && (
        <div className={styles.statusActions}>
          <ButtonLink href={traceHref} size="sm" variant="quiet" icon={<Icon as={Route} />}>
            Trace
          </ButtonLink>
        </div>
      )}
    </div>
  );
}

const NOTICE_ICON: Record<Notice["code"], typeof Info> = {
  egress_excluded: Info,
  claims_removed: Info,
  stale_evidence: Clock,
  cites_superseded: GitCompare,
  semantic_degraded: Info,
};

/** API notices, in API order; the stale notice is the one tinted row. */
export function Notices({ notices, cited }: { notices: readonly Notice[]; cited: readonly EvidenceItem[] }) {
  const sensitive = cited.find((e) => e.classification !== "internal");
  if (!notices.length && !sensitive) return null;
  return (
    <div className={styles.notices} data-testid="notices">
      {notices.map((n, i) =>
        n.code === "stale_evidence" ? (
          <NoticeLine key={i} icon={<Icon as={Clock} />} tone="time">
            {n.message}
          </NoticeLine>
        ) : (
          <NoticeLine key={i} icon={<Icon as={NOTICE_ICON[n.code] ?? Info} />} tone={n.code === "egress_excluded" ? "boundary" : n.code === "cites_superseded" ? "disagreement" : "neutral"}>
            {n.message}
          </NoticeLine>
        ),
      )}
      {sensitive && (
        <NoticeLine icon={<Icon as={Lock} />} tone="neutral">
          Uses {levelLabel(sensitive.classification)} sources — share only with people who have access.
        </NoticeLine>
      )}
    </div>
  );
}

/** The validated claims: direct answers as the lead, supporting claims beneath. */
export const Prose = memo(function Prose() {
  const { model } = useAnswer();
  if (!model.lead.length && !model.supporting.length) return null;
  return (
    <div className={styles.prose} data-testid="answer-prose">
      {model.lead.length > 0 && (
        <p className={styles.lead}>
          {model.lead.map((c) => (
            <ClaimText key={c.key} claim={c} />
          ))}
        </p>
      )}
      {model.supporting.length > 0 && (
        <p className={styles.support}>
          {model.supporting.map((c) => (
            <ClaimText key={c.key} claim={c} />
          ))}
        </p>
      )}
    </div>
  );
});

export function Unknowns({ unknowns }: { unknowns: readonly string[] }) {
  if (!unknowns.length) return null;
  return (
    <section className={styles.unknowns} aria-labelledby="unknowns-label">
      <SectionLabel as="h3" id="unknowns-label">
        What the evidence doesn&apos;t establish
      </SectionLabel>
      <ul className={styles.unknownList}>
        {unknowns.map((u, i) => (
          <li key={i}>{u}</li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Potential conflicts keep every position, oldest first, with equal weight: chronology is
 * not a verdict. Supersession appears only when a source records it.
 */
export const Conflicts = memo(function Conflicts() {
  const { model } = useAnswer();
  if (!model.conflicts.length) return null;
  return (
    <div className={styles.conflicts}>
      {model.conflicts.map((c) => (c.kind === "potential_conflict" ? <PotentialConflict key={c.key} conflict={c} /> : <Superseded key={c.key} conflict={c} />))}
    </div>
  );
});

function PotentialConflict({ conflict }: { conflict: RenderConflict }) {
  return (
    <Callout tone="disagreement" label="Potential conflict" icon={<Icon as={GitCompare} />} labelAs="h3" id={conflict.key}>
      {conflict.topic && <p className={styles.topic}>{conflict.topic}</p>}
      <Positions positions={conflict.positions} />
      <p className={styles.summary}>{conflict.summary}</p>
    </Callout>
  );
}

function Superseded({ conflict }: { conflict: RenderConflict }) {
  const { items, open } = useAnswer();
  const byDocument = (id: string | undefined) => (id ? [...items.values()].find((e) => e.document_id === id) : undefined);
  const older = byDocument(conflict.supersession?.superseded_document_id);
  const newer = byDocument(conflict.supersession?.superseding_document_id);
  return (
    <Callout tone="neutral" label="Superseded" icon={<Icon as={GitCompare} />} labelAs="h3" id={conflict.key}>
      {conflict.topic && <p className={styles.topic}>{conflict.topic}</p>}
      {conflict.positions.length > 0 && <Positions positions={conflict.positions} neutral />}
      <p className={styles.summary}>{conflict.summary}</p>
      {(older || newer) && (
        <div className={styles.docLinks}>
          {older && (
            <Button size="sm" onClick={(e) => open(older.evidence_id, e.currentTarget)}>
              Inspect earlier
            </Button>
          )}
          {newer && (
            <Button size="sm" onClick={(e) => open(newer.evidence_id, e.currentTarget)}>
              Inspect replacement
            </Button>
          )}
        </div>
      )}
    </Callout>
  );
}

function Positions({ positions, neutral = false }: { positions: readonly RenderPosition[]; neutral?: boolean }) {
  return (
    <ol className={`${styles.positions} ${neutral ? styles.neutralPositions : ""}`}>
      {positions.map((p) => (
        <li key={p.key} className={styles.position}>
          <span className={styles.positionDate}>{formatDay(p.occurredAt) ?? "Undated"}</span>
          <span className={styles.positionSource}>
            {sourceTypeLabel(p.sourceType)} · {authorityLabel(p.authority)}
            <span className={styles.positionTitle}>{p.title}</span>
          </span>
          <span className={styles.positionClaim}>
            {p.claim.text}
            <Chips citations={p.claim.citations} />
          </span>
        </li>
      ))}
    </ol>
  );
}
