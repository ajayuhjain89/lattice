"use client";

import { ArrowUpRight, GitCompare, Link2, Lock } from "lucide-react";
import { memo, useState } from "react";

import type { EvidenceDetail } from "@/api/types";
import { Button, ExternalLinkButton } from "@/components/primitives/Button";
import { Icon } from "@/components/primitives/icons";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { locateQuote, markRanges } from "@/lib/answer-model";
import { authorityLabel, documentKindLabel, formatDay, levelLabel, safeHttpUrl } from "@/lib/format";
import type { InspectorContext } from "@/lib/workspace-state";

import styles from "./EvidenceBody.module.css";
import { SensitivityTag } from "./SensitivityTag";

const NOT_USED: Record<string, string> = {
  egress_policy:
    "You can read this source, but your organization's AI data policy kept it out of what was sent to the AI model.",
  relevance_floor: "Retrieved as a loose match only, so it was not sent to the AI model.",
  prompt_budget: "Not sent to the AI model: the prompt's size limit was reached.",
};

export interface EvidenceBodyProps {
  detail: EvidenceDetail;
  context: InspectorContext | null;
  headingId: string;
  /** Open another evidence item of the same answer (supersession links). */
  onOpen?: (evidenceId: string) => void;
  /** Standalone page: show the full title (no clamp). */
  fullTitle?: boolean;
}

/**
 * Everything the API returned for one evidence item, and its relationship to the answer on
 * screen. Absent values are omitted; nothing is inferred (D-8: no account unless supplied).
 */
export const EvidenceBody = memo(function EvidenceBody({ detail, context, headingId, onOpen, fullTitle = false }: EvidenceBodyProps) {
  const id = detail.evidence_id;
  const citing = context?.citedBy.get(id) ?? [];
  const item = context?.items.get(id);
  const number = context?.numberOf.get(id);
  const date = formatDay(detail.occurred_at);
  const updated = formatDay(detail.document_updated_at);
  const href = safeHttpUrl(detail.uri);

  const ranges = citing.flatMap((claim) =>
    claim.citations
      .filter((c) => c.evidence_id === id)
      .map((c) => locateQuote(detail.text, c.quote, c.quote_start, c.quote_end))
      .filter((r): r is { start: number; end: number } => r !== null),
  );
  const segments = markRanges(unwrap(detail.text), ranges);

  const byDocument = (documentId: string) =>
    context ? [...context.items.values()].find((e) => e.document_id === documentId) : undefined;
  const predecessor = detail.supersedes_document_id ? byDocument(detail.supersedes_document_id) : undefined;
  const successors = detail.superseded_by_document_ids.map((d) => ({ id: d, item: byDocument(d) }));

  return (
    <div className={styles.body}>
      <div className={styles.titleBlock}>
        <h2 id={headingId} className={`${styles.title} ${fullTitle ? styles.titleFull : ""}`} title={detail.title}>
          {detail.title}
        </h2>
        <p className={styles.source}>
          {detail.source_name}
          {detail.display_ref ? ` · ${detail.display_ref}` : ""}
        </p>
      </div>

      <dl className={styles.meta}>
        <dt>Type</dt>
        <dd>{documentKindLabel(detail.document_kind)}</dd>
        {date && (
          <>
            <dt>Date</dt>
            <dd className={styles.mono}>{date}</dd>
          </>
        )}
        {updated && updated !== date && (
          <>
            <dt>Updated</dt>
            <dd className={styles.mono}>{updated}</dd>
          </>
        )}
        {detail.author && (
          <>
            <dt>Author</dt>
            <dd>{detail.author}</dd>
          </>
        )}
        {detail.account && (
          <>
            <dt>Account</dt>
            <dd>{detail.account.name}</dd>
          </>
        )}
        <dt>Sensitivity</dt>
        <dd>
          {detail.classification === "internal" ? levelLabel(detail.classification) : <SensitivityTag level={detail.classification} />}
        </dd>
        <dt>Authority</dt>
        <dd>{authorityLabel(detail.authority)}</dd>
        {detail.heading_path.length > 0 && (
          <>
            <dt>Section</dt>
            <dd>{detail.heading_path.join(" › ")}</dd>
          </>
        )}
        <dt>Location</dt>
        <dd className={styles.mono}>
          v{detail.version} · chars {detail.char_start.toLocaleString("en-GB")}–{detail.char_end.toLocaleString("en-GB")}
        </dd>
      </dl>

      {(detail.supersedes_document_id || successors.length > 0 || detail.classification !== "internal") && (
        <div className={styles.notes}>
          {successors.map((s) => (
            <NoticeLine key={s.id} icon={<Icon as={GitCompare} />} tone="neutral">
              Replaced by{" "}
              {s.item && onOpen ? (
                <button type="button" className={styles.linkButton} onClick={() => onOpen(s.item!.evidence_id)}>
                  {s.item.title}
                </button>
              ) : (
                "a newer document you can access"
              )}
              , as recorded by the source.
            </NoticeLine>
          ))}
          {detail.supersedes_document_id && (
            <NoticeLine icon={<Icon as={GitCompare} />} tone="neutral">
              Replaces{" "}
              {predecessor && onOpen ? (
                <button type="button" className={styles.linkButton} onClick={() => onOpen(predecessor.evidence_id)}>
                  {predecessor.title}
                </button>
              ) : (
                "an earlier document"
              )}
              , as recorded by the source.
            </NoticeLine>
          )}
          {detail.classification !== "internal" && (
            <NoticeLine icon={<Icon as={Lock} />} tone="neutral">
              {levelLabel(detail.classification)} source — share only with people who have access.
            </NoticeLine>
          )}
        </div>
      )}

      {context && (
        <section className={styles.section} aria-labelledby={`${headingId}-rel`}>
          <SectionLabel as="h3" id={`${headingId}-rel`} meta={number ? `citation ${number}` : undefined}>
            In this answer
          </SectionLabel>
          {citing.length > 0 ? (
            <ol className={styles.claims}>
              {citing.map((claim) => (
                <li key={claim.key} className={styles.claim}>
                  <span className={styles.claimNumber}>[{number}]</span>
                  <span>{claim.text}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className={styles.relation}>
              {item?.not_used_reason
                ? NOT_USED[item.not_used_reason]
                : item?.used_for_answer
                  ? "Sent to the AI model, but no statement in the answer cites it."
                  : "Retrieved for this question; not cited."}
            </p>
          )}
        </section>
      )}

      <section className={styles.section} aria-labelledby={`${headingId}-passage`}>
        <SectionLabel as="h3" id={`${headingId}-passage`}>
          {ranges.length > 0 ? "Cited passage" : "Passage"}
        </SectionLabel>
        <p className={styles.passage}>
          {segments.map((s, i) => (s.marked ? <mark key={i}>{s.text}</mark> : <span key={i}>{s.text}</span>))}
        </p>
      </section>

      <EvidenceActions href={href} evidenceId={id} />
    </div>
  );
});

/**
 * Source text keeps its hard line wraps; a single newline inside a paragraph becomes a
 * space, while blank lines, list items and headings keep their breaks. One character for one, so quote offsets
 * into the original text stay exact.
 */
export function unwrap(text: string): string {
  // Lines that start a list item or heading keep their break.
  return text.replace(/(?<!\n)\n(?!\n|[ \t]*(?:[-*+•]|\d+[.)])\s|#)/g, " ");
}

function EvidenceActions({ href, evidenceId }: { href: string | null; evidenceId: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/evidence/${evidenceId}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className={styles.actions}>
      {href && (
        <ExternalLinkButton href={href} size="sm" iconEnd={<Icon as={ArrowUpRight} />}>
          Open in source
        </ExternalLinkButton>
      )}
      <Button size="sm" icon={<Icon as={Link2} />} onClick={copy} aria-live="polite">
        {copied ? "Link copied" : "Copy link"}
      </Button>
    </div>
  );
}
