"use client";

import { ChevronRight } from "lucide-react";
import { memo, useId, useMemo, useState } from "react";

import { Icon } from "@/components/primitives/icons";
import { Lattice, LatticeLegend, latticeAddsInformation, type LatticeAxis } from "@/components/primitives/Lattice";
import { record } from "@/components/record/RecordPage";
import { useWorkspace } from "@/components/shell/workspace-context";
import { provenanceCells, snippet } from "@/lib/answer-model";
import { formatCount } from "@/lib/format";
import { useStore } from "@/lib/store";
import { BREAKPOINTS, useMinWidth } from "@/lib/use-media";

import { NO_POINTER, useAnswer } from "./answer-context";
import styles from "./ProvenanceMap.module.css";

/**
 * The provenance lattice (design-direction §5.3), only when it says something the margin
 * markers don't: some claim rests on more than one exhibit, or some exhibit backs more
 * than one claim. A one-to-one answer never shows it. Rows are the claims on screen,
 * columns the exhibits they cite -- nothing else, so an empty cell only ever means "this
 * claim doesn't cite this exhibit", never "something you can't see".
 *
 * Hover or focus a cell and its claim, exhibit row and markers light together; activate
 * it to open the exhibit. On phones it starts folded to a one-line summary.
 */
export const ProvenanceMap = memo(function ProvenanceMap() {
  const { model, items, open, pointer } = useAnswer();
  const { state } = useWorkspace();
  const phone = !useMinWidth(BREAKPOINTS.mobile);
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const highlight = useStore(pointer, (p) => p);
  const selectedCol = useStore(state.inspector, (s) => s.evidenceId);
  const selectedRow = useStore(state.inspector, (s) => s.claim ?? null);

  const data = useMemo(() => {
    const { rows, cols, cells } = provenanceCells(model);
    const rowAxes: LatticeAxis[] = rows.map((c, i) => ({
      key: c.key,
      label: snippet(c.text, phone ? 26 : 44),
      name: `Claim ${i + 1}: ${c.text}`,
    }));
    const colAxes: LatticeAxis[] = cols.map((evidenceId) => {
      const n = model.numberOf.get(evidenceId)!;
      const item = items.get(evidenceId);
      return {
        key: evidenceId,
        label: String(n),
        name: `exhibit ${n}${item ? `, ${item.display_ref ?? item.title}` : ""}`,
        conflict: model.disputed.has(evidenceId),
      };
    });
    const short = new Map(rows.map((c, i) => [c.key, `Claim ${i + 1}`]));
    return { rowAxes, colAxes, cells, short, show: rows.length >= 2 && cols.length >= 2 && latticeAddsInformation(cells) };
  }, [model, items, phone]);

  if (!data.show) return null;
  const disputed = data.colAxes.filter((c) => c.conflict).length;
  const summary = [
    formatCount(data.rowAxes.length, "claim", "claims"),
    formatCount(data.colAxes.length, "exhibit", "exhibits"),
    disputed ? `${disputed} in a potential conflict` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const lattice = (
    <Lattice
      caption="Which claims cite which exhibits"
      showCaption={!phone}
      corner="Claim"
      rows={data.rowAxes}
      cols={data.colAxes}
      cells={data.cells}
      cellLabel={(r, c, s) => `${data.short.get(r.key)} ${s ? "cites" : "doesn't cite"} ${c.name}`}
      onActivate={(row, col, el) => open(col, el, row)}
      onHighlight={(cell) => pointer.set(cell ? { claim: cell.row, evidence: cell.col } : NO_POINTER)}
      highlight={{ row: highlight.claim, col: highlight.evidence }}
      selected={selectedCol ? { row: selectedRow, col: selectedCol } : undefined}
      legend={
        <LatticeLegend
          items={[
            { state: "filled", label: "cites" },
            ...(disputed ? [{ state: "conflict" as const, label: "underlined: in a potential conflict" }] : []),
          ]}
        />
      }
    />
  );

  if (!phone) {
    return (
      <div className={`${record.text} ${styles.map}`} data-testid="provenance-lattice">
        {lattice}
      </div>
    );
  }
  return (
    <div className={styles.map} data-testid="provenance-lattice">
      <button type="button" className={styles.summary} aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded((e) => !e)}>
        <Icon as={ChevronRight} size={14} />
        <span>
          <span className={styles.summaryLabel}>Claims and exhibits</span>
          <span className={styles.summaryCounts}>{summary}</span>
        </span>
      </button>
      <div id={id} hidden={!expanded} className={styles.folded}>
        {expanded && lattice}
      </div>
    </div>
  );
});
