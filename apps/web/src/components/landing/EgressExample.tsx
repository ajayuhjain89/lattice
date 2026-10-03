"use client";

import { cellKey, Lattice, LatticeLegend, type LatticeAxis, type LatticeCell } from "@/components/primitives/Lattice";

/**
 * The egress lattice on Halden's example policy: data levels × the two models it has
 * approved. A client island only because the Lattice primitive is one; it is static here
 * (no activation), a table whose every cell is named.
 */
const LEVELS: readonly LatticeAxis[] = [
  { key: "internal", label: "Internal", name: "Internal data" },
  { key: "confidential", label: "Confidential", name: "Confidential data" },
  { key: "restricted", label: "Restricted", name: "Restricted data" },
];

const PROVIDERS: readonly LatticeAxis[] = [
  { key: "external", label: "External model API", name: "the external model API", detail: "approved up to Internal" },
  { key: "own", label: "Halden's own model server", name: "Halden's own model server", detail: "approved up to Confidential" },
];

const CELLS = new Map<string, LatticeCell>([
  [cellKey("internal", "external"), "filled"],
  [cellKey("confidential", "external"), "hatched"],
  [cellKey("restricted", "external"), "hatched"],
  [cellKey("internal", "own"), "filled"],
  [cellKey("confidential", "own"), "filled"],
  [cellKey("restricted", "own"), "hatched"],
]);

export function EgressExample() {
  return (
    <Lattice
      caption="Which data levels each model may receive, in Halden's example policy"
      corner="Data level"
      headers="interface"
      rows={LEVELS}
      cols={PROVIDERS}
      cells={CELLS}
      ground="sheet"
      cellLabel={(r, c, state) => (state === "filled" ? `${r.label} data may be sent to ${c.name}` : `${r.label} data isn't sent to ${c.name}`)}
      legend={
        <LatticeLegend
          items={[
            { state: "filled", label: "may be sent" },
            { state: "hatched", label: "not sent — kept inside Lattice" },
          ]}
        />
      }
    />
  );
}
