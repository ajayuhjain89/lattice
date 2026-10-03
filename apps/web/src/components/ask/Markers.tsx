"use client";

import { memo } from "react";

import { ExhibitButton } from "@/components/primitives/ExhibitMarker";
import { Tooltip } from "@/components/primitives/Tooltip";
import { useWorkspace } from "@/components/shell/workspace-context";
import type { NumberedCitation, RenderClaim } from "@/lib/answer-model";
import { documentKindLabel, formatDay } from "@/lib/format";
import { useStore } from "@/lib/store";

import { markerId, pointerHandlers, useAnswer } from "./answer-context";
import styles from "./Markers.module.css";

/** One marker per distinct exhibit a statement cites, in number order; never merged into ranges. */
export function distinctCitations(citations: readonly NumberedCitation[]): NumberedCitation[] {
  const seen = new Set<number>();
  return citations.filter((c) => (seen.has(c.number) ? false : (seen.add(c.number), true))).sort((a, b) => a.number - b.number);
}

/** The exhibit markers of one statement: in the margin (≥768 px) or after the text (phones). */
export function Markers({ claim, placement }: { claim: RenderClaim; placement: "margin" | "inline" }) {
  return (
    <span className={placement === "margin" ? styles.margin : styles.inline}>
      {distinctCitations(claim.citations).map((c) => (
        <CitationMarker key={c.number} claimKey={claim.key} evidenceId={c.evidence_id} number={c.number} />
      ))}
    </span>
  );
}

/**
 * The marker of one relationship: this statement cites this exhibit. Hover or focus points
 * at the relationship (the statement, its exhibit row and its lattice cell light together);
 * activating it opens the exhibit. It is selected (marker fill) while its exhibit is open,
 * wherever it was opened from.
 */
export const CitationMarker = memo(function CitationMarker({ claimKey, evidenceId, number }: { claimKey: string; evidenceId: string; number: number }) {
  const { state } = useWorkspace();
  const { items, open, pointer } = useAnswer();
  const selected = useStore(state.inspector, (s) => s.evidenceId === evidenceId);
  const linked = useStore(pointer, (p) => p.evidence === evidenceId && (p.claim === null || p.claim === claimKey));
  const item = items.get(evidenceId);
  const date = item ? formatDay(item.occurred_at ?? item.document_updated_at) : null;
  const label = item ? `Citation ${number}: ${item.display_ref ?? item.title}${date ? `, ${date}` : ""}` : `Citation ${number}`;

  const marker = (
    <ExhibitButton
      id={markerId(claimKey, number)}
      n={number}
      selected={selected}
      linked={linked && !selected}
      aria-label={label}
      data-citation={number}
      data-evidence={evidenceId}
      onClick={(e) => open(evidenceId, e.currentTarget, claimKey)}
      {...pointerHandlers(pointer, { claim: claimKey, evidence: evidenceId })}
    />
  );
  if (!item) return marker;
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
      {marker}
    </Tooltip>
  );
});
