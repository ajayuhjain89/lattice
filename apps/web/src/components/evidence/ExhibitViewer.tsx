"use client";

import { ArrowUpRight, CornerUpLeft, GitCompare, Link2, Lock } from "lucide-react";
import { memo, useState } from "react";

import type { EvidenceDetail } from "@/api/types";
import { Button, ExternalLinkButton } from "@/components/primitives/Button";
import { ExhibitMarker } from "@/components/primitives/ExhibitMarker";
import { EgressGlyph } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { NoticeLine } from "@/components/primitives/NoticeLine";
import { ProvenanceTrail } from "@/components/primitives/ProvenanceTrail";
import { useToast } from "@/components/primitives/Toast";
import { locateQuote, paragraphs, type RenderClaim } from "@/lib/answer-model";
import { authorityLabel, documentKindLabel, formatDay, levelLabel, safeHttpUrl } from "@/lib/format";
import type { InspectorContext } from "@/lib/workspace-state";

import styles from "./ExhibitViewer.module.css";
import { SensitivityTag } from "./SensitivityTag";

const NOT_USED: Record<string, string> = {
  egress_policy: "You can read this source, but your organization's AI data policy kept it out of what was sent to the AI model.",
  relevance_floor: "Retrieved as a loose match only, so it was not sent to the AI model.",
  prompt_budget: "Not sent to the AI model: the prompt's size limit was reached.",
};

export interface ExhibitViewerProps {
  detail: EvidenceDetail;
  context: InspectorContext | null;
  headingId: string;
  headingLevel?: "h1" | "h2";
  /** Open another evidence item of the same answer (supersession links). */
  onOpen?: (evidenceId: string) => void;
  /** Take the reader to a claim's marker in the answer ("Show in answer"). */
  onShowClaim?: (claim: RenderClaim) => void;
}

/**
 * One exhibit, as the API returned it (GET /v1/evidence/{id}), and its place in the
 * answer on screen. The provenance chain reads top to bottom: source › reference ›
 * section › version › characters › quote verified, then the title, then the passage set
 * as a page with the verified quotes marked. Absent values are omitted; nothing is
 * inferred (D-8: no account unless the API supplies one).
 */
export const ExhibitViewer = memo(function ExhibitViewer({ detail, context, headingId, headingLevel: Heading = "h2", onOpen, onShowClaim }: ExhibitViewerProps) {
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
  const blocks = paragraphs(unwrap(detail.text), ranges);
  const verified = ranges.length > 0;

  const byDocument = (documentId: string) => (context ? [...context.items.values()].find((e) => e.document_id === documentId) : undefined);
  const predecessor = detail.supersedes_document_id ? byDocument(detail.supersedes_document_id) : undefined;
  const successors = detail.superseded_by_document_ids.map((d) => ({ id: d, item: byDocument(d) }));
  const start = detail.char_start + (ranges[0]?.start ?? 0);
  const end = ranges[0] ? detail.char_start + ranges[0].end : detail.char_end;

  return (
    <article className={styles.viewer} aria-labelledby={headingId}>
      <ProvenanceTrail
        label="Provenance"
        segments={[
          { key: "source", kind: "Source", label: detail.source_name },
          { key: "reference", kind: "Reference", label: detail.display_ref ?? documentKindLabel(detail.document_kind) },
          detail.heading_path.length > 0 && { key: "section", kind: "Section", label: detail.heading_path.join(" › ") },
          { key: "version", kind: "Version", label: `v${detail.version}` },
          { key: "range", kind: "Characters", label: `chars ${start.toLocaleString("en-GB")}–${end.toLocaleString("en-GB")}` },
        ]}
        verified={verified ? "quote verified" : undefined}
      />

      <Heading id={headingId} className={Heading === "h1" ? styles.pageTitle : styles.title}>
        {detail.title}
      </Heading>

      {(item?.not_used_reason === "egress_policy" || successors.length > 0 || detail.supersedes_document_id || detail.classification !== "internal") && (
        <div className={styles.notes}>
          {item?.not_used_reason === "egress_policy" && (
            <NoticeLine icon={<Icon as={EgressGlyph} />} tone="boundary">
              {NOT_USED.egress_policy}
            </NoticeLine>
          )}
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

      <section className={styles.passageSection} aria-labelledby={`${headingId}-passage`}>
        <h3 className={styles.sectionHeading} id={`${headingId}-passage`}>
          {verified ? "Cited passage" : "Passage"}
          {verified && <span className={styles.sectionMeta}>{ranges.length === 1 ? "1 verified quote" : `${ranges.length} verified quotes`}</span>}
        </h3>
        <div className={styles.passage} data-testid="passage">
          {blocks.map((b, i) => (
            <p key={i} className={b.cited ? styles.cited : undefined}>
              {b.segments.map((s, j) => (s.marked ? <mark key={j}>{s.text}</mark> : <span key={j}>{s.text}</span>))}
            </p>
          ))}
        </div>
      </section>

      {context && (
        <section className={styles.section} aria-labelledby={`${headingId}-rel`}>
          <h3 className={styles.sectionHeading} id={`${headingId}-rel`}>
            {citing.length ? "Cited by" : "In this answer"}
            {number !== undefined && <span className={styles.sectionMeta}>exhibit {number}</span>}
          </h3>
          {citing.length > 0 ? (
            <ol className={styles.claims}>
              {citing.map((claim) => (
                <li key={claim.key} className={styles.claim}>
                  <ExhibitMarker n={number ?? null} />
                  <span className={styles.claimText}>{claim.text}</span>
                  {onShowClaim && (
                    <Button size="sm" variant="quiet" icon={<Icon as={CornerUpLeft} />} onClick={() => onShowClaim(claim)} aria-label={`Show in answer: ${claim.text}`}>
                      Show
                    </Button>
                  )}
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

      <section className={styles.section} aria-labelledby={`${headingId}-meta`}>
        <h3 className={styles.sectionHeading} id={`${headingId}-meta`}>
          Source details
        </h3>
        <dl className={styles.meta}>
          <div>
            <dt>Type</dt>
            <dd>{documentKindLabel(detail.document_kind)}</dd>
          </div>
          {date && (
            <div>
              <dt>Date</dt>
              <dd className={styles.ledger}>{date}</dd>
            </div>
          )}
          {updated && updated !== date && (
            <div>
              <dt>Updated</dt>
              <dd className={styles.ledger}>{updated}</dd>
            </div>
          )}
          {detail.author && (
            <div>
              <dt>Author</dt>
              <dd>{detail.author}</dd>
            </div>
          )}
          {detail.account && (
            <div>
              <dt>Account</dt>
              <dd>{detail.account.name}</dd>
            </div>
          )}
          <div>
            <dt>Sensitivity</dt>
            <dd>{detail.classification === "internal" ? levelLabel(detail.classification) : <SensitivityTag level={detail.classification} />}</dd>
          </div>
          <div>
            <dt>Authority</dt>
            <dd>{authorityLabel(detail.authority)}</dd>
          </div>
          <div>
            <dt>Location</dt>
            <dd className={styles.ledger}>
              v{detail.version} · chars {detail.char_start.toLocaleString("en-GB")}–{detail.char_end.toLocaleString("en-GB")}
            </dd>
          </div>
        </dl>
      </section>

      <ExhibitActions href={href} evidenceId={id} />
    </article>
  );
});

/**
 * Source text keeps its hard line wraps; a single newline inside a paragraph becomes a
 * space, while blank lines, list items and headings keep their breaks. One character for
 * one, so quote offsets into the original text stay exact.
 */
export function unwrap(text: string): string {
  return text.replace(/(?<!\n)\n(?!\n|[ \t]*(?:[-*+•]|\d+[.)])\s|#)/g, " ");
}

function ExhibitActions({ href, evidenceId }: { href: string | null; evidenceId: string }) {
  const notify = useToast();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/evidence/${evidenceId}`);
      setCopied(true);
      notify({ title: "Link copied", description: "Anyone who opens it sees this evidence only if they can read it." });
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
      <Button size="sm" icon={<Icon as={Link2} />} onClick={copy}>
        {copied ? "Link copied" : "Copy link"}
      </Button>
    </div>
  );
}
