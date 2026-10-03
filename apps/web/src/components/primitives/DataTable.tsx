"use client";

/**
 * The data table (design-direction §7.2), one primitive for every list of records.
 *
 * - A real table with explicit ARIA roles, so it keeps its semantics when the layout
 *   stacks into label/value blocks below `stackBelow` (CSS display changes would
 *   otherwise strip them in some browsers).
 * - Header: interface type over an ink rule (a header of authority); rows are separated
 *   by hairlines. Rows only highlight on hover when they are interactive.
 * - Above the stack width, a table wider than its column scrolls inside itself with the
 *   primary column sticky; the page never scrolls sideways.
 * - Optional inline expansion under a row (failures, details), an empty state that spans
 *   the table, and static loading rows that hold the geometry. No sorting controls: the
 *   APIs don't sort, and a control that only reorders one page would mislead.
 */
/* eslint-disable jsx-a11y/no-redundant-roles, jsx-a11y/no-interactive-element-to-noninteractive-role --
   The explicit table roles are deliberate: the stacked layout changes the elements'
   display, which strips their implicit table semantics in some browsers. */
import { Fragment, type ReactNode } from "react";

import { scrollRegionProps, useOverflow } from "@/lib/use-overflow";

import styles from "./DataTable.module.css";

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** The label in stacked mode when `header` isn't plain text. */
  label?: string;
  cell: (row: T) => ReactNode;
  align?: "start" | "end";
  /** CSS width hint for the column ("12rem", "30%"). */
  width?: string;
  /** Ledger voice: identifiers, dates, counts. */
  mono?: boolean;
  /** The row's name: the stacked block's heading, and sticky while scrolling. */
  primary?: boolean;
  /** Visually hidden header (an actions column). */
  hideHeader?: boolean;
  /** A short value (a count, "None"): when stacked, it stays on its label's line even on narrow phones. */
  short?: boolean;
}

export interface DataTableProps<T> {
  caption: string;
  showCaption?: boolean;
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  density?: "regular" | "dense";
  stackBelow?: "1024" | "768" | "never";
  /** Rendered across the table when there are no rows (and not loading). */
  empty?: ReactNode;
  loading?: boolean;
  loadingRows?: number;
  /** Rows whose expansion is open, and what to show under them. */
  expanded?: ReadonlySet<string>;
  renderExpansion?: (row: T) => ReactNode;
  /** The row links somewhere: hover and a pointer cursor, without making the row a button. */
  interactiveRows?: boolean;
  /** Below the rows: "Load older", a page summary. */
  footer?: ReactNode;
  id?: string;
}

export function DataTable<T>({
  caption,
  showCaption = false,
  columns,
  rows,
  rowKey,
  density = "regular",
  stackBelow = "1024",
  empty,
  loading = false,
  loadingRows = 3,
  expanded,
  renderExpansion,
  interactiveRows = false,
  footer,
  id,
}: DataTableProps<T>) {
  const span = columns.length;
  const [scrollRef, overflows] = useOverflow<HTMLDivElement>();
  const label = (c: Column<T>) => c.label ?? (typeof c.header === "string" ? c.header : c.key);
  const cellClass = (c: Column<T>) =>
    [styles.cell, c.align === "end" && styles.end, c.mono && styles.mono, c.primary && styles.primary, c.short && styles.short].filter(Boolean).join(" ");

  return (
    <div className={styles.wrap} data-stack={stackBelow} data-density={density} id={id}>
      <div ref={scrollRef} className={styles.scroll} {...scrollRegionProps(overflows, caption)}>
        <table className={styles.table} role="table" aria-busy={loading || undefined}>
          <caption className={showCaption ? styles.caption : "visually-hidden"}>{caption}</caption>
          {columns.some((c) => c.width) && (
            <colgroup>
              {columns.map((c) => (
                <col key={c.key} style={c.width ? { width: c.width } : undefined} />
              ))}
            </colgroup>
          )}
          <thead className={styles.head} role="rowgroup">
            <tr role="row">
              {columns.map((c) => (
                <th key={c.key} role="columnheader" scope="col" className={[styles.th, c.align === "end" && styles.end, c.primary && styles.primary].filter(Boolean).join(" ")}>
                  {c.hideHeader ? <span className="visually-hidden">{c.header}</span> : c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody role="rowgroup">
            {loading
              ? Array.from({ length: loadingRows }, (_, i) => (
                  <tr key={`loading-${i}`} role="row" className={styles.row} aria-hidden="true">
                    {columns.map((c) => (
                      <td key={c.key} role="cell" className={cellClass(c)}>
                        <span className={styles.placeholder} style={{ width: c.primary ? "70%" : "45%" }} />
                      </td>
                    ))}
                  </tr>
                ))
              : rows.map((row) => {
                  const key = rowKey(row);
                  const open = expanded?.has(key) && renderExpansion;
                  return (
                    <Fragment key={key}>
                      <tr role="row" className={[styles.row, interactiveRows && styles.interactive, open && styles.open].filter(Boolean).join(" ")} data-row={key}>
                        {columns.map((c) => (
                          <td key={c.key} role="cell" className={cellClass(c)}>
                            {!c.primary && !c.hideHeader && (
                              <span className={styles.stackLabel} aria-hidden="true">
                                {label(c)}
                              </span>
                            )}
                            <span className={styles.value}>{c.cell(row)}</span>
                          </td>
                        ))}
                      </tr>
                      {open && (
                        <tr role="row" className={styles.expansion}>
                          <td role="cell" colSpan={span} className={styles.expansionCell}>
                            {renderExpansion(row)}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
            {!loading && rows.length === 0 && empty && (
              <tr role="row">
                <td role="cell" colSpan={span} className={styles.empty}>
                  {empty}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {footer && <div className={styles.footer}>{footer}</div>}
    </div>
  );
}
