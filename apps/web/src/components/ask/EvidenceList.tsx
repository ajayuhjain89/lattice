"use client";

import { ChevronRight, ShieldCheck } from "lucide-react";
import { memo, useId, useState } from "react";

import type { EvidenceItem } from "@/api/types";
import { SensitivityTag } from "@/components/evidence/SensitivityTag";
import { SourceTypeTag } from "@/components/evidence/SourceTypeTag";
import { Icon } from "@/components/primitives/icons";
import { SectionLabel } from "@/components/primitives/SectionLabel";
import { useWorkspace } from "@/components/shell/workspace-context";
import { excerpt, firstQuote, groupEvidence, locateQuote } from "@/lib/answer-model";
import { formatDay } from "@/lib/format";
import { useStore } from "@/lib/store";

import { useAnswer } from "./answer-context";
import styles from "./EvidenceList.module.css";

const REASON: Record<string, string> = {
  egress_policy: "Not sent to the AI model · AI data policy",
  relevance_floor: "Loose match · not sent to the AI model",
  prompt_budget: "Not sent · prompt size limit",
};

/**
 * The response's evidence, and only it: cited items (numbered, in citation order), items
 * the AI data policy kept from the model (expanded: the person may read them and they may
 * matter), and the rest (collapsed). With no claims, everything is one list.
 */
export const EvidenceList = memo(function EvidenceList({ evidence, collapsed = false }: { evidence: readonly EvidenceItem[]; collapsed?: boolean }) {
  const { model } = useAnswer();
  const groups = groupEvidence(evidence, model);
  const hasClaims = model.numberOf.size > 0;
  if (!evidence.length) return null;

  const counts = hasClaims
    ? [`${groups.cited.length} cited`, groups.policyExcluded.length ? `${groups.policyExcluded.length} not sent` : null, groups.other.length ? `${groups.other.length} also retrieved` : null]
        .filter(Boolean)
        .join(" · ")
    : `${evidence.length} ${evidence.length === 1 ? "source" : "sources"}`;

  return (
    <section className={styles.section} aria-labelledby="evidence-label" data-testid="evidence">
      <SectionLabel id="evidence-label" meta={counts}>
        Evidence
      </SectionLabel>
      {hasClaims ? (
        <>
          <Rows items={groups.cited} />
          {groups.policyExcluded.length > 0 && (
            <div className={styles.group}>
              <h3 className={styles.groupHeader}>
                Not used to draft this answer · AI data policy
                <span className={styles.groupCount}>{groups.policyExcluded.length}</span>
              </h3>
              <Rows items={groups.policyExcluded} />
            </div>
          )}
          {groups.other.length > 0 && <Collapsible label="Also retrieved, not cited" items={groups.other} />}
        </>
      ) : collapsed ? (
        <Collapsible label="Sources retrieved for this question" items={[...evidence]} />
      ) : (
        <Rows items={[...evidence]} />
      )}
    </section>
  );
});

function Collapsible({ label, items }: { label: string; items: EvidenceItem[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={styles.group}>
      <button type="button" className={styles.disclosure} aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <span className={styles.disclosureLabel}>
          <ChevronRight size={14} strokeWidth={1.5} className={styles.chevron} aria-hidden="true" />
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
          <EvidenceRow item={e} />
        </li>
      ))}
    </ol>
  );
}

const EvidenceRow = memo(function EvidenceRow({ item }: { item: EvidenceItem }) {
  const { model, open } = useAnswer();
  const { state } = useWorkspace();
  const selected = useStore(state.inspector, (s) => s.evidenceId === item.evidence_id);
  const number = model.numberOf.get(item.evidence_id);
  const quote = firstQuote(model, item.evidence_id);
  const at = quote ? locateQuote(item.text, quote.quote, quote.quote_start, quote.quote_end) : null;
  const text = excerpt(item.text, at);
  const date = formatDay(item.occurred_at ?? item.document_updated_at);
  const meta = [item.source_name, item.display_ref, item.account?.name, item.author].filter(Boolean).join(" · ");

  return (
    <button
      type="button"
      className={styles.row}
      aria-current={selected || undefined}
      onClick={(e) => open(item.evidence_id, e.currentTarget)}
      aria-label={`${number ? `Evidence ${number}: ` : ""}${item.title}. Open in inspector`}
      data-evidence={item.evidence_id}
    >
      <span className={`${styles.number} ${number ? "" : styles.unnumbered}`} aria-hidden="true">
        {number ?? "–"}
      </span>
      <span className={styles.tagCell}>
        <SourceTypeTag sourceType={item.source_type} />
      </span>
      <span className={styles.main}>
        <span className={styles.title}>{item.title}</span>
        <span className={styles.meta}>{meta}</span>
        <span className={styles.excerpt}>
          {text.before}
          {text.quote && <mark className={styles.quote}>{text.quote}</mark>}
          {text.after}
        </span>
        {(item.not_used_reason || item.classification !== "internal") && (
          <span className={styles.reason}>
            <SensitivityTag level={item.classification} />
            {item.not_used_reason && (
              <>
                {item.not_used_reason === "egress_policy" && <Icon as={ShieldCheck} size={12} />}
                {REASON[item.not_used_reason]}
              </>
            )}
          </span>
        )}
      </span>
      <span className={styles.date}>{date}</span>
    </button>
  );
});
