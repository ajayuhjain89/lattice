"use client";

import { ChevronRight, GitCompare, GitFork } from "lucide-react";
import { memo, useId, useState } from "react";

import type { EvidenceItem } from "@/api/types";
import { SensitivityTag } from "@/components/evidence/SensitivityTag";
import { ExhibitMarker } from "@/components/primitives/ExhibitMarker";
import { EgressGlyph, DocumentGlyph, SOURCE_GLYPHS } from "@/components/primitives/glyphs";
import { Icon } from "@/components/primitives/icons";
import { record } from "@/components/record/RecordPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { excerpt, firstQuote, groupEvidence, locateQuote } from "@/lib/answer-model";
import { formatCount, formatDay, sourceTypeLabel } from "@/lib/format";
import { useStore } from "@/lib/store";

import { pointerHandlers, useAnswer, useExhibitLit } from "./answer-context";
import styles from "./Exhibits.module.css";

const REASON: Record<string, string> = {
  relevance_floor: "Loose match · not sent to the AI model",
  prompt_budget: "Not sent to the AI model · prompt size limit",
};

/**
 * The response's evidence as exhibits, and only it (design-direction §6.5): cited items,
 * numbered in citation order; items the AI data policy kept from the model (readable by
 * the viewer, so they may matter -- expanded, under one hatched edge: a known boundary
 * that wasn't crossed); and the rest, collapsed. With no claims, everything is one list.
 * Every fact on a row is a field of the item; nothing is inferred (D-8: an account only
 * when the API supplies it).
 */
export const Exhibits = memo(function Exhibits({
  evidence,
  collapsed = false,
  showReasons = true,
}: {
  evidence: readonly EvidenceItem[];
  collapsed?: boolean;
  /** Per-item "not sent" reasons; off when no model call was in play (the status says why). */
  showReasons?: boolean;
}) {
  const { model } = useAnswer();
  const shown = showReasons ? evidence : evidence.map((e) => ({ ...e, not_used_reason: null }));
  const groups = groupEvidence(shown, model);
  const hasClaims = model.numberOf.size > 0;
  if (!evidence.length) return null;
  const counts = hasClaims
    ? [`${groups.cited.length} cited`, groups.policyExcluded.length ? `${groups.policyExcluded.length} not sent` : null, groups.other.length ? `${groups.other.length} also retrieved` : null]
        .filter(Boolean)
        .join(" · ")
    : formatCount(evidence.length, "source", "sources");

  return (
    <section className={styles.section} aria-labelledby="evidence-label" data-testid="evidence">
      <div className={`${record.text} ${styles.head}`}>
        <h2 className={styles.heading} id="evidence-label">
          {hasClaims ? "Exhibits" : "Sources found"}
        </h2>
        {!(collapsed && !hasClaims) && <span className={styles.count}>{counts}</span>}
      </div>
      {hasClaims ? (
        <>
          <Rows items={groups.cited} />
          {groups.policyExcluded.length > 0 && <Withheld items={groups.policyExcluded} />}
          {groups.other.length > 0 && <Collapsible label="Also retrieved, not cited" items={groups.other} />}
        </>
      ) : collapsed ? (
        <Collapsible label="Sources retrieved for this question" items={[...shown]} />
      ) : (
        <>
          {groups.policyExcluded.length > 0 && <Withheld items={groups.policyExcluded} />}
          <Rows items={groups.other} />
        </>
      )}
    </section>
  );
});

/** Readable evidence the AI data policy kept from the model: one hatched edge for the group. */
function Withheld({ items }: { items: readonly EvidenceItem[] }) {
  const id = useId();
  return (
    <div className={styles.withheld} role="group" aria-labelledby={id}>
      <h3 className={`${record.text} ${styles.groupHeader}`} id={id}>
        <Icon as={EgressGlyph} size={14} />
        <span>
          Not sent to the AI model <span className={styles.groupReason}>· AI data policy · you can read {items.length === 1 ? "it" : "them"}</span>
        </span>
        <span className={styles.groupCount}>{items.length}</span>
      </h3>
      <Rows items={items} />
    </div>
  );
}

function Collapsible({ label, items }: { label: string; items: readonly EvidenceItem[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={styles.collapsible}>
      <button type="button" className={`${record.text} ${styles.disclosure}`} aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <span className={styles.disclosureLabel}>
          <Icon as={ChevronRight} size={14} />
          {label}
        </span>
        <span className={styles.groupCount}>{items.length}</span>
      </button>
      <div id={id} hidden={!open}>
        {open && <Rows items={items} />}
      </div>
    </div>
  );
}

function Rows({ items }: { items: readonly EvidenceItem[] }) {
  if (!items.length) return null;
  return (
    <ol className={styles.list}>
      {items.map((e) => (
        <li key={e.evidence_id}>
          <ExhibitRow item={e} />
        </li>
      ))}
    </ol>
  );
}

const ExhibitRow = memo(function ExhibitRow({ item }: { item: EvidenceItem }) {
  const { model, open, pointer } = useAnswer();
  const { state } = useWorkspace();
  const selected = useStore(state.inspector, (s) => s.evidenceId === item.evidence_id);
  const lit = useExhibitLit(item.evidence_id);
  const number = model.numberOf.get(item.evidence_id);
  const quote = firstQuote(model, item.evidence_id);
  const at = quote ? locateQuote(item.text, quote.quote, quote.quote_start, quote.quote_end) : null;
  const text = excerpt(item.text, at);
  // The source's own date; without one, the document's last update -- labelled as such.
  const date = item.occurred_at ? formatDay(item.occurred_at) : formatDay(item.document_updated_at) && `Updated ${formatDay(item.document_updated_at)}`;
  const meta = [item.source_name, item.display_ref, item.account?.name, item.author].filter(Boolean).join(" · ");
  const citing = model.citedBy.get(item.evidence_id) ?? [];
  const statements = citing.filter((c) => !c.key.startsWith("conflict-")).length;
  const disputed = model.disputed.has(item.evidence_id);
  const Glyph = SOURCE_GLYPHS[item.source_type as keyof typeof SOURCE_GLYPHS] ?? DocumentGlyph;

  const notes = [
    statements > 1 ? <span key="cites">Cited by {statements} claims</span> : null,
    disputed ? (
      <span key="conflict" className={styles.disputed}>
        <Icon as={GitFork} size={12} />
        In a potential conflict
      </span>
    ) : null,
    item.superseded_by_document_ids.length > 0 ? (
      <span key="replaced">
        <Icon as={GitCompare} size={12} />
        Replaced by a newer document, as recorded by the source
      </span>
    ) : item.supersedes_document_id ? (
      <span key="replaces">
        <Icon as={GitCompare} size={12} />
        Replaces an earlier document, as recorded by the source
      </span>
    ) : null,
    // Policy-withheld items sit in their own group, whose header already says why.
    item.not_used_reason && item.not_used_reason !== "egress_policy" ? <span key="reason">{REASON[item.not_used_reason]}</span> : null,
  ].filter(Boolean);

  return (
    <button
      type="button"
      className={`${record.row} ${styles.row}`}
      aria-current={selected || undefined}
      data-lit={lit || undefined}
      onClick={(e) => open(item.evidence_id, e.currentTarget)}
      aria-label={`${number ? `Evidence ${number}: ` : ""}${item.title}. Open in inspector`}
      data-evidence={item.evidence_id}
      {...pointerHandlers(pointer, { claim: null, evidence: item.evidence_id })}
    >
      <span className={styles.marker}>
        <ExhibitMarker n={number ?? null} state={selected ? "selected" : "rest"} linked={lit && !selected} size="lg" />
      </span>
      <span className={styles.body}>
        <span className={styles.titleLine}>
          <span className={styles.type} title={sourceTypeLabel(item.source_type)}>
            <Glyph size={14} aria-hidden="true" focusable="false" />
          </span>
          <span className={styles.title}>{item.title}</span>
          {date && <span className={styles.date}>{date}</span>}
        </span>
        <span className={styles.meta}>
          {sourceTypeLabel(item.source_type)} · {meta}
        </span>
        <span className={styles.excerpt}>
          {text.before}
          {text.quote && <mark>{text.quote}</mark>}
          {text.after}
        </span>
        {(notes.length > 0 || item.classification !== "internal") && (
          <span className={styles.notes}>
            <SensitivityTag level={item.classification} />
            {notes}
          </span>
        )}
      </span>
    </button>
  );
});
