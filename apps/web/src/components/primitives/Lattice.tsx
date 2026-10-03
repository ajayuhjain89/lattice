"use client";

/**
 * The lattice (design-direction §2, §5.3): rows × columns where a filled cell is a
 * relationship that actually holds -- claims × exhibits on an answer, data levels ×
 * providers in the AI policy. Built only from data on screen; never decoration.
 *
 * It is a real table: row and column headers, and every cell has a name ("Claim 2 cites
 * exhibit 3"). With `onActivate`, filled cells are buttons: the lattice is one Tab stop,
 * arrow keys move between related cells (skipping empty ones), Home / End jump to the
 * first / last in the row, and hover and focus report the cell through `onHighlight`
 * so the matching claim and exhibit can light with it.
 *
 * `focusable="all"` makes every non-empty cell a stop (a hatched "not sent" cell says as
 * much as a filled one: the egress lattice explains both), and `colGroups` adds a header
 * row grouping the columns (Generation · Embedding).
 */
import { useCallback, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { scrollRegionProps, useOverflow } from "@/lib/use-overflow";

import styles from "./Lattice.module.css";

export interface LatticeAxis {
  key: string;
  /** Short visible header ("Lead", "2", "ollama"). */
  label: ReactNode;
  /** Full name for assistive tech and tooltips ("Claim 2", "Exhibit 3"). */
  name: string;
  /** Part of a potential conflict (columns): a plum underline plus a spoken note. */
  conflict?: boolean;
  /** A second header line (e.g. a provider's purpose). */
  detail?: ReactNode;
}

export type LatticeCell = "filled" | "hatched";

export const cellKey = (row: string, col: string) => `${row}|${col}`;

/**
 * Whether a claims × exhibits lattice says anything the margin markers don't: only when
 * the relationship isn't one-to-one (some row or column holds more than one filled cell).
 */
export function latticeAddsInformation(cells: ReadonlyMap<string, LatticeCell>): boolean {
  const rows = new Map<string, number>();
  const cols = new Map<string, number>();
  for (const [key, state] of cells) {
    if (state !== "filled") continue;
    const [r, c] = key.split("|") as [string, string];
    rows.set(r, (rows.get(r) ?? 0) + 1);
    cols.set(c, (cols.get(c) ?? 0) + 1);
  }
  return [...rows.values(), ...cols.values()].some((n) => n > 1);
}

export interface LatticeColGroup {
  key: string;
  label: ReactNode;
  /** How many consecutive columns the group spans, in column order. */
  span: number;
}

export interface LatticeProps {
  /** The table's accessible name; shown when `showCaption`. */
  caption: string;
  showCaption?: boolean;
  /** Header of the row-label column (visually hidden when empty). */
  corner?: string;
  rows: readonly LatticeAxis[];
  cols: readonly LatticeAxis[];
  cells: ReadonlyMap<string, LatticeCell>;
  /** The accessible name (and tooltip) of a cell, including empty ones. */
  cellLabel: (row: LatticeAxis, col: LatticeAxis, state: LatticeCell | null) => string;
  onActivate?: (row: string, col: string, element: HTMLElement) => void;
  onHighlight?: (cell: { row: string; col: string } | null) => void;
  /** Driven from outside: a hovered claim lights its row, a hovered exhibit its column. */
  highlight?: { row?: string | null; col?: string | null };
  /** The open exhibit (column) and, when known, the claim (row). */
  selected?: { row?: string | null; col?: string | null };
  legend?: ReactNode;
  /** Header voice: ledger for numbered axes (claims, exhibits), interface for words. */
  headers?: "ledger" | "interface";
  /** Which cells are keyboard stops when `onActivate` is set: filled only, or every non-empty cell. */
  focusable?: "filled" | "all";
  colGroups?: readonly LatticeColGroup[];
  /** The surface the lattice sits on, so sticky row headers match it while scrolling. */
  ground?: "paper" | "sheet";
}

export function Lattice({
  caption,
  showCaption = false,
  corner = "",
  rows,
  cols,
  cells,
  cellLabel,
  onActivate,
  onHighlight,
  highlight,
  selected,
  legend,
  headers = "ledger",
  focusable = "filled",
  colGroups,
  ground = "paper",
}: LatticeProps) {
  const interactive = Boolean(onActivate);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const isStop = useCallback((state: LatticeCell | null | undefined) => (focusable === "all" ? state != null : state === "filled"), [focusable]);
  const filled = useMemo(
    () => rows.flatMap((r, ri) => cols.flatMap((c, ci) => (isStop(cells.get(cellKey(r.key, c.key))) ? [{ r: r.key, c: c.key, ri, ci }] : []))),
    [rows, cols, cells, isStop],
  );
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const tabKey = focusKey && filled.some((f) => cellKey(f.r, f.c) === focusKey) ? focusKey : filled[0] ? cellKey(filled[0].r, filled[0].c) : null;

  const move = useCallback(
    (from: { ri: number; ci: number }, key: string): string | null => {
      const same = (pred: (f: (typeof filled)[number]) => boolean, pick: "min" | "max", by: (f: (typeof filled)[number]) => number) => {
        const options = filled.filter(pred);
        if (!options.length) return null;
        const best = options.reduce((a, b) => ((pick === "min" ? by(b) < by(a) : by(b) > by(a)) ? b : a));
        return cellKey(best.r, best.c);
      };
      const nearest = (pred: (f: (typeof filled)[number]) => boolean, primary: (f: (typeof filled)[number]) => number, cross: (f: (typeof filled)[number]) => number) => {
        const options = filled.filter(pred);
        if (!options.length) return null;
        const best = options.reduce((a, b) => {
          const da = [Math.abs(primary(a)), Math.abs(cross(a))];
          const db = [Math.abs(primary(b)), Math.abs(cross(b))];
          // Grid convention: the nearest row (or column) first, then the closest cell in it.
          return db[0]! < da[0]! || (db[0] === da[0] && db[1]! < da[1]!) ? b : a;
        });
        return cellKey(best.r, best.c);
      };
      switch (key) {
        case "ArrowRight":
          return nearest((f) => f.ci > from.ci, (f) => f.ci - from.ci, (f) => f.ri - from.ri);
        case "ArrowLeft":
          return nearest((f) => f.ci < from.ci, (f) => f.ci - from.ci, (f) => f.ri - from.ri);
        case "ArrowDown":
          return nearest((f) => f.ri > from.ri, (f) => f.ri - from.ri, (f) => f.ci - from.ci);
        case "ArrowUp":
          return nearest((f) => f.ri < from.ri, (f) => f.ri - from.ri, (f) => f.ci - from.ci);
        case "Home":
          return same((f) => f.ri === from.ri, "min", (f) => f.ci);
        case "End":
          return same((f) => f.ri === from.ri, "max", (f) => f.ci);
        default:
          return null;
      }
    },
    [filled],
  );

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, ri: number, ci: number) => {
    const next = move({ ri, ci }, event.key);
    if (!next) return;
    event.preventDefault();
    setFocusKey(next);
    buttons.current.get(next)?.focus();
  };

  const [scrollRef, overflows] = useOverflow<HTMLDivElement>();
  const rowLit = (key: string) => highlight?.row === key || selected?.row === key;
  const colLit = (key: string) => highlight?.col === key || selected?.col === key;

  return (
    <figure className={styles.figure}>
      <div ref={scrollRef} className={styles.scroll} {...scrollRegionProps(overflows, caption)}>
        <table className={styles.lattice} data-interactive={interactive || undefined} data-headers={headers} data-ground={ground}>
          <caption className={showCaption ? styles.caption : "visually-hidden"}>{caption}</caption>
          <thead>
            {colGroups && colGroups.length > 0 && (
              <tr>
                <td className={styles.corner} />
                {colGroups.map((g) => (
                  <th key={g.key} scope="colgroup" colSpan={g.span} className={styles.groupHead}>
                    {g.label}
                  </th>
                ))}
              </tr>
            )}
            <tr>
              <td className={styles.corner}>{corner ? <span className={styles.cornerLabel}>{corner}</span> : null}</td>
              {cols.map((c) => (
                <th
                  key={c.key}
                  scope="col"
                  className={[styles.colHead, c.conflict && styles.conflict, colLit(c.key) && styles.lit].filter(Boolean).join(" ")}
                  title={c.name}
                >
                  <span className={styles.headLabel} aria-hidden="true">
                    {c.label}
                  </span>
                  {c.detail && (
                    <span className={styles.detail} aria-hidden="true">
                      {c.detail}
                    </span>
                  )}
                  <span className="visually-hidden">{`${c.name}${c.conflict ? ", in a potential conflict" : ""}`}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={r.key}>
                <th scope="row" className={[styles.rowHead, rowLit(r.key) && styles.lit].filter(Boolean).join(" ")} title={r.name}>
                  <span className={styles.headLabel} aria-hidden="true">
                    {r.label}
                  </span>
                  {r.detail && (
                    <span className={styles.detail} aria-hidden="true">
                      {r.detail}
                    </span>
                  )}
                  <span className="visually-hidden">{r.name}</span>
                </th>
                {cols.map((c, ci) => {
                  const key = cellKey(r.key, c.key);
                  const state = cells.get(key) ?? null;
                  const label = cellLabel(r, c, state);
                  const lit = isStop(state) && (rowLit(r.key) || colLit(c.key));
                  const chosen = isStop(state) && selected?.col === c.key && (!selected.row || selected.row === r.key);
                  const square = [styles.square, state && styles[state], lit && styles.cellLit, chosen && styles.cellSelected].filter(Boolean).join(" ");
                  if (interactive && isStop(state)) {
                    return (
                      <td key={c.key} className={styles.cell}>
                        <button
                          ref={(el) => {
                            if (el) buttons.current.set(key, el);
                            else buttons.current.delete(key);
                          }}
                          type="button"
                          className={`${square} ${styles.cellButton}`}
                          aria-label={label}
                          aria-pressed={chosen}
                          title={label}
                          tabIndex={key === tabKey ? 0 : -1}
                          onClick={(e) => onActivate?.(r.key, c.key, e.currentTarget)}
                          onKeyDown={(e) => onKeyDown(e, ri, ci)}
                          onFocus={() => {
                            setFocusKey(key);
                            onHighlight?.({ row: r.key, col: c.key });
                          }}
                          onBlur={() => onHighlight?.(null)}
                          onMouseEnter={() => onHighlight?.({ row: r.key, col: c.key })}
                          onMouseLeave={() => onHighlight?.(null)}
                        />
                      </td>
                    );
                  }
                  return (
                    <td key={c.key} className={styles.cell} title={label}>
                      <span className={square} aria-hidden="true" />
                      <span className="visually-hidden">{label}</span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {legend && <figcaption className={styles.legend}>{legend}</figcaption>}
    </figure>
  );
}

/** Legend swatches, so every lattice explains its cells the same way. */
export function LatticeLegend({ items }: { items: readonly { state: LatticeCell | "empty" | "conflict"; label: ReactNode }[] }) {
  return (
    <span className={styles.legendItems}>
      {items.map((item, i) => (
        <span key={i} className={styles.legendItem}>
          <span className={[styles.swatch, styles[`swatch-${item.state}`]].join(" ")} aria-hidden="true" />
          {item.label}
        </span>
      ))}
    </span>
  );
}
