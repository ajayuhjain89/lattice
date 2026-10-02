"use client";

import { memo } from "react";

import { useWorkspace } from "@/components/shell/workspace-context";
import { ExhibitButton } from "@/components/primitives/ExhibitMarker";
import { Tooltip } from "@/components/primitives/Tooltip";
import type { NumberedCitation, RenderClaim } from "@/lib/answer-model";
import { documentKindLabel, formatDay } from "@/lib/format";
import { useStore } from "@/lib/store";

import { useAnswer } from "./answer-context";
import styles from "./Citations.module.css";

/**
 * One validated claim followed by its citation chips. Only this claim re-renders when the
 * selected evidence changes to or from one it cites.
 */
export const ClaimText = memo(function ClaimText({ claim }: { claim: RenderClaim }) {
  const { state } = useWorkspace();
  const active = useStore(state.inspector, (s) => s.evidenceId !== null && claim.citations.some((c) => c.evidence_id === s.evidenceId));
  return (
    <>
      <span className={`${styles.claim} ${active ? styles.claimActive : ""}`} data-claim={claim.key}>
        {claim.text}
      </span>
      <Chips citations={claim.citations} />{" "}
    </>
  );
});

export function Chips({ citations }: { citations: readonly NumberedCitation[] }) {
  // One chip per distinct evidence item, in number order; never merged into ranges.
  const seen = new Set<number>();
  const unique = citations.filter((c) => (seen.has(c.number) ? false : (seen.add(c.number), true))).sort((a, b) => a.number - b.number);
  return (
    <span className={styles.chips}>
      {unique.map((c) => (
        <CitationChip key={c.number} evidenceId={c.evidence_id} number={c.number} />
      ))}
    </span>
  );
}

export const CitationChip = memo(function CitationChip({ evidenceId, number }: { evidenceId: string; number: number }) {
  const { state } = useWorkspace();
  const { items, open } = useAnswer();
  const selected = useStore(state.inspector, (s) => s.evidenceId === evidenceId);
  const item = items.get(evidenceId);
  const date = item ? formatDay(item.occurred_at ?? item.document_updated_at) : null;
  const label = item ? `Citation ${number}: ${item.display_ref ?? item.title}${date ? `, ${date}` : ""}` : `Citation ${number}`;

  const chip = (
    <ExhibitButton n={number} selected={selected} aria-label={label} data-citation={number} onClick={(e) => open(evidenceId, e.currentTarget)} />
  );
  if (!item) return chip;
  return (
    <Tooltip
      content={
        <>
          <span className={styles.tooltipTitle}>{item.title}</span>
          <span className={styles.tooltipMeta}>
            {documentKindLabel(item.document_kind)}
            {date ? ` · ${date}` : ""}
          </span>
        </>
      }
    >
      {chip}
    </Tooltip>
  );
});
