"use client";

import { AlertTriangle, Check, Clock, Copy, GitCompare, Info, Lock, Route, XCircle } from "lucide-react";
import { memo, useState } from "react";

import type { Answer, EvidenceItem, Notice } from "@/api/types";
import { Button, ButtonLink } from "@/components/primitives/Button";
import { DisagreementAxis, type AxisPosition } from "@/components/primitives/DisagreementAxis";
import { EgressGlyph } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { useToast } from "@/components/primitives/Toast";
import { record } from "@/components/record/RecordPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { statements, type AnswerModel, type RenderClaim, type RenderConflict } from "@/lib/answer-model";
import { presentStatus } from "@/lib/answer-status";
import { authorityLabel, formatCount, formatDay, formatDuration, levelLabel, sourceTypeLabel } from "@/lib/format";
import { useStore } from "@/lib/store";
import { BREAKPOINTS, useMinWidth } from "@/lib/use-media";

import { pointerHandlers, useAnswer, useClaimLit } from "./answer-context";
import styles from "./Brief.module.css";
import { distinctCitations, Markers } from "./Markers";

/**
 * The brief's ledger line (design-direction §5.6), the one place tracked uppercase mono
 * remains: ANSWERED · 3 CLAIMS · 3 EXHIBITS · NEWEST 18 SEP 2026 · 4.2 S, then Copy · Trace.
 * "Answered" carries no icon and no colour: only states that need attention do.
 */
export function StatusLine({ answer, elapsedMs, traceHref, copy }: { answer: Answer; elapsedMs?: number; traceHref?: string; copy?: () => string }) {
  const { model } = useAnswer();
  const p = presentStatus(answer.status, answer.status_reason);
  const claims = statements(model).length;
  const exhibits = model.numberOf.size;
  const newest = formatDay(exhibits ? answer.freshness.newest_cited_at : answer.freshness.newest_evidence_at);
  const StatusIcon = p.tone === "error" ? XCircle : p.tone === "warning" ? AlertTriangle : p.tone === "info" ? Info : null;
  const facts = [
    claims > 0 ? formatCount(claims, "claim", "claims") : null,
    exhibits > 0
      ? formatCount(exhibits, "exhibit", "exhibits")
      : answer.evidence.length > 0 && answer.status !== "ambiguous"
        ? formatCount(answer.evidence.length, "source found", "sources found")
        : null,
    newest ? `Newest ${newest}` : null,
    answer.question_sensitivity !== "internal" ? `Question ${levelLabel(answer.question_sensitivity)}` : null,
    elapsedMs !== undefined ? formatDuration(elapsedMs) : null,
  ].filter((f): f is string => Boolean(f));

  return (
    <div className={styles.statusLine} data-testid="status-line">
      <p className={styles.facts}>
        <span className={styles.keyword} data-tone={p.tone}>
          {StatusIcon && <Icon as={StatusIcon} size={12} />}
          {p.label}
        </span>
        {facts.map((f) => (
          <span key={f} className={styles.fact}>
            {f}
          </span>
        ))}
      </p>
      {(copy || traceHref) && (
        <div className={styles.actions}>
          {copy && <CopyButton copy={copy} />}
          {traceHref && (
            <ButtonLink href={traceHref} size="sm" variant="quiet" icon={<Icon as={Route} />}>
              Trace
            </ButtonLink>
          )}
        </div>
      )}
    </div>
  );
}

function CopyButton({ copy }: { copy: () => string }) {
  const notify = useToast();
  const [done, setDone] = useState(false);
  const run = async () => {
    try {
      await navigator.clipboard.writeText(copy());
      setDone(true);
      notify({ title: "Answer copied", description: "With its exhibit list, so every claim stays traceable." });
      window.setTimeout(() => setDone(false), 1600);
    } catch {
      notify({ title: "Copying isn't available here", description: "Select the text to copy it instead." });
    }
  };
  return (
    <Button size="sm" variant="quiet" icon={<Icon as={done ? Check : Copy} />} onClick={run}>
      Copy
    </Button>
  );
}

/** The answer as plain text: each claim with its exhibit numbers, then the exhibits. */
export function answerText(model: AnswerModel, items: ReadonlyMap<string, EvidenceItem>): string {
  const line = (c: RenderClaim) => `${c.text} ${distinctCitations(c.citations).map((x) => `[${x.number}]`).join("")}`;
  const exhibits = model.cited.map((id) => {
    const e = items.get(id);
    if (!e) return null;
    const date = formatDay(e.occurred_at ?? e.document_updated_at);
    return `[${model.numberOf.get(id)}] ${e.title}${e.display_ref && !e.title.includes(e.display_ref) ? ` (${e.display_ref})` : ""} — ${e.source_name}${date ? `, ${date}` : ""}`;
  });
  return [model.lead.map(line).join(" "), model.supporting.map(line).join(" "), "", "Exhibits", ...exhibits.filter(Boolean)]
    .filter((s, i) => s !== "" || i === 2)
    .join("\n")
    .trim();
}

const NOTICE_ICON: Record<Notice["code"], typeof Info> = {
  egress_excluded: Info,
  claims_removed: Info,
  stale_evidence: Clock,
  cites_superseded: GitCompare,
  semantic_degraded: Info,
};

/**
 * API notices, in API order and wording. The stale notice is the one tinted line; the AI
 * data policy notice is a boundary (readable evidence kept from the model), so it gets
 * the hatched edge.
 */
export function Notices({ notices, cited }: { notices: readonly Notice[]; cited: readonly EvidenceItem[] }) {
  const sensitive = cited.find((e) => e.classification !== "internal");
  if (!notices.length && !sensitive) return null;
  return (
    <div className={styles.notices} data-testid="notices">
      {notices.map((n, i) =>
        n.code === "egress_excluded" ? (
          <NoticeLine key={i} icon={<Icon as={EgressGlyph} />} tone="boundary">
            {n.message}
          </NoticeLine>
        ) : (
          <NoticeLine key={i} icon={<Icon as={NOTICE_ICON[n.code] ?? Info} />} tone={n.code === "stale_evidence" ? "time" : n.code === "cites_superseded" ? "disagreement" : "neutral"}>
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

/**
 * The validated claims, one per row: direct answers as the lead (record voice, 21 px),
 * supporting claims beneath (17 px). Each row carries its exhibit markers in the margin,
 * aligned to its first line (inline after the text on phones), and -- at ≥1440 px with
 * nothing docked -- a sidenote naming its exhibits.
 */
export const Statements = memo(function Statements() {
  const { model } = useAnswer();
  const { state } = useWorkspace();
  const margin = useMinWidth(BREAKPOINTS.mobile);
  const wide = useMinWidth(BREAKPOINTS.wide);
  const docked = useStore(state.inspector, (s) => s.evidenceId !== null);
  const notes = wide && !docked;
  if (!model.lead.length && !model.supporting.length) return null;
  return (
    <div className={styles.statements} data-testid="answer-prose">
      {model.lead.map((c) => (
        <Statement key={c.key} claim={c} lead margin={margin} notes={notes} />
      ))}
      {model.supporting.map((c) => (
        <Statement key={c.key} claim={c} margin={margin} notes={notes} />
      ))}
    </div>
  );
});

const Statement = memo(function Statement({ claim, lead = false, margin, notes }: { claim: RenderClaim; lead?: boolean; margin: boolean; notes: boolean }) {
  return (
    <div className={[margin && record.row, styles.statement, lead && styles.lead, notes && styles.withNotes].filter(Boolean).join(" ")} data-claim={claim.key}>
      <ClaimText claim={claim} inline={!margin} className={lead ? styles.leadText : styles.supportText} />
      {margin && (
        <div className={styles.claimMargin}>
          <Markers claim={claim} placement="margin" />
        </div>
      )}
      {notes && <Sidenote claim={claim} />}
    </div>
  );
});

/**
 * A claim's text. Hovering it points at the claim (its exhibits light); it is marked
 * while one of its exhibits is open.
 */
export const ClaimText = memo(function ClaimText({ claim, inline, className, as: Tag = "p" }: { claim: RenderClaim; inline: boolean; className?: string; as?: "p" | "span" }) {
  const { pointer } = useAnswer();
  const { state } = useWorkspace();
  const lit = useClaimLit(claim.key);
  const open = useStore(state.inspector, (s) => s.evidenceId !== null && claim.citations.some((c) => c.evidence_id === s.evidenceId));
  const hover = pointerHandlers(pointer, { claim: claim.key, evidence: null });
  return (
    <Tag className={[styles.claim, className].filter(Boolean).join(" ")} data-lit={lit || undefined} data-open={open || undefined}>
      {/* Hover only adds emphasis: the claim's markers give keyboard and touch the same link. */}
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
      <span className={styles.claimWords} onMouseEnter={hover.onMouseEnter} onMouseLeave={hover.onMouseLeave}>
        {claim.text}
      </span>
      {inline && <Markers claim={claim} placement="inline" />}
    </Tag>
  );
});

/** Which exhibits the claim rests on, beside it (decorative: the markers carry the names). */
function Sidenote({ claim }: { claim: RenderClaim }) {
  const { items } = useAnswer();
  return (
    <ul className={styles.sidenote} aria-hidden="true">
      {distinctCitations(claim.citations).map((c) => {
        const item = items.get(c.evidence_id);
        if (!item) return null;
        const date = item.occurred_at ? formatDay(item.occurred_at) : `updated ${formatDay(item.document_updated_at)}`;
        return (
          <li key={c.number}>
            <span className={styles.sidenoteNumber}>{c.number}</span>
            <span className={styles.sidenoteRef}>{item.display_ref ?? item.title}</span>
            {date && <span className={styles.sidenoteDate}>{date}</span>}
          </li>
        );
      })}
    </ul>
  );
}

export function Unknowns({ unknowns }: { unknowns: readonly string[] }) {
  if (!unknowns.length) return null;
  return (
    <section className={`${record.text} ${styles.unknowns}`} aria-labelledby="unknowns-label">
      <h3 className={styles.sectionHeading} id="unknowns-label">
        What the evidence doesn&apos;t establish
      </h3>
      <ul className={styles.unknownList}>
        {unknowns.map((u, i) => (
          <li key={i}>{u}</li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Disagreement (design-direction §5.5). A potential conflict lays every position on the
 * date axis with equal weight; the API's summary is the only conclusion ("Lattice can't
 * confirm which is current"). A supersession is the neutral form, shown only when a source
 * records the replacement -- never inferred from dates or authority.
 */
export const Disagreements = memo(function Disagreements() {
  const { model } = useAnswer();
  if (!model.conflicts.length) return null;
  return (
    <div className={`${record.text} ${styles.disagreements}`}>
      {model.conflicts.map((c) => (c.kind === "potential_conflict" ? <Conflict key={c.key} conflict={c} /> : <Supersession key={c.key} conflict={c} />))}
    </div>
  );
});

function positionSource(sourceType: string, authority: string) {
  return `${sourceTypeLabel(sourceType)} · ${authorityLabel(authority)}`;
}

function Conflict({ conflict }: { conflict: RenderConflict }) {
  const positions: AxisPosition[] = conflict.positions.map((p) => ({
    key: p.key,
    at: p.occurredAt,
    dateLabel: formatDay(p.occurredAt) ?? "Undated",
    source: positionSource(p.sourceType, p.authority),
    title: p.title,
    children: <ClaimText claim={p.claim} inline as="span" className={styles.positionText} />,
  }));
  return <DisagreementAxis id={conflict.key} topic={conflict.topic ?? undefined} positions={positions} conclusion={<p>{conflict.summary}</p>} />;
}

function Supersession({ conflict }: { conflict: RenderConflict }) {
  const { items, open, model } = useAnswer();
  const byDocument = (id: string | undefined) => (id ? [...items.values()].find((e) => e.document_id === id) : undefined);
  const older = byDocument(conflict.supersession?.superseded_document_id);
  const newer = byDocument(conflict.supersession?.superseding_document_id);

  const fromItem = (e: EvidenceItem | undefined, role: string): AxisPosition | null => {
    if (!e) return null;
    const n = model.numberOf.get(e.evidence_id);
    return {
      key: e.evidence_id,
      at: e.occurred_at,
      dateLabel: formatDay(e.occurred_at) ?? "Undated",
      source: positionSource(e.source_type, e.authority),
      title: e.title,
      children: (
        <span className={styles.positionRole}>
          {role}
          {n !== undefined && <span className={styles.positionNumber}> · exhibit {n}</span>}
        </span>
      ),
    };
  };

  const positions: AxisPosition[] = conflict.positions.length
    ? conflict.positions.map((p) => ({
        key: p.key,
        at: p.occurredAt,
        dateLabel: formatDay(p.occurredAt) ?? "Undated",
        source: positionSource(p.sourceType, p.authority),
        title: p.title,
        children: <ClaimText claim={p.claim} inline as="span" className={styles.positionText} />,
      }))
    : older && newer
      ? [fromItem(older, "Earlier document"), fromItem(newer, "Replacement, as recorded by the source")].filter((x): x is AxisPosition => x !== null)
      : [];

  return (
    <DisagreementAxis
      kind="superseded"
      id={conflict.key}
      topic={conflict.topic ?? undefined}
      positions={positions}
      conclusion={<p>{conflict.summary}</p>}
      actions={
        (older || newer) && (
          <>
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
          </>
        )
      }
    />
  );
}
