/**
 * Layout primitives (design-direction §4.3). Spacing comes only from the 4 px scale, set
 * as a gap on the parent -- components don't carry their own outer margins.
 *
 *   Stack      vertical rhythm
 *   Cluster    a wrapping row (chips, facts, actions)
 *   Inline     a non-wrapping row whose middle child shrinks (label · value · action)
 *   Split      two columns that stack below a breakpoint
 *   Page       the page container: gutter, and the admin width or the full canvas
 *   MarginGrid the reading grammar: [margin][measure][notes] -- exhibit numbers sit in
 *              the margin, sidenotes in the notes column (which folds away when narrow)
 */
import type { CSSProperties, ElementType, ReactNode } from "react";

import styles from "./Layout.module.css";

export type Space = "0" | "0-5" | "1" | "2" | "3" | "4" | "5" | "6" | "8" | "10" | "12" | "16";

const gap = (space: Space) => `var(--space-${space})`;

interface Box {
  as?: ElementType;
  className?: string;
  children?: ReactNode;
  id?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
}

function cx(...names: (string | false | undefined)[]) {
  return names.filter(Boolean).join(" ");
}

export function Stack({ as: Tag = "div", space = "4", className, children, ...rest }: Box & { space?: Space }) {
  return (
    <Tag className={cx(styles.stack, className)} style={{ "--gap": gap(space) } as CSSProperties} {...rest}>
      {children}
    </Tag>
  );
}

export function Cluster({
  as: Tag = "div",
  space = "2",
  align = "center",
  justify = "start",
  className,
  children,
  ...rest
}: Box & { space?: Space; align?: "start" | "center" | "baseline" | "end"; justify?: "start" | "between" | "end" }) {
  return (
    <Tag
      className={cx(styles.cluster, className)}
      data-align={align}
      data-justify={justify}
      style={{ "--gap": gap(space) } as CSSProperties}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export function Inline({ as: Tag = "div", space = "2", align = "center", className, children, ...rest }: Box & { space?: Space; align?: "start" | "center" | "baseline" }) {
  return (
    <Tag className={cx(styles.inline, className)} data-align={align} style={{ "--gap": gap(space) } as CSSProperties} {...rest}>
      {children}
    </Tag>
  );
}

/** Two columns, `ratio` start:end, stacked below `below` (a review width). */
export function Split({
  as: Tag = "div",
  space = "8",
  ratio = "1:1",
  below = "1024",
  className,
  children,
  ...rest
}: Box & { space?: Space; ratio?: "1:1" | "5:7" | "7:5" | "2:1" | "1:2"; below?: "768" | "1024" | "1280" }) {
  return (
    <Tag className={cx(styles.split, className)} data-ratio={ratio} data-below={below} style={{ "--gap": gap(space) } as CSSProperties} {...rest}>
      {children}
    </Tag>
  );
}

export function Page({ as: Tag = "div", width = "admin", className, children, ...rest }: Box & { width?: "admin" | "full" }) {
  return (
    <Tag className={cx(styles.page, className)} data-width={width} {...rest}>
      {children}
    </Tag>
  );
}

/**
 * One row of the reading grammar. `margin` holds exhibit numbers aligned to the first line
 * of the content; `notes` holds sidenotes, shown only where there is room for them.
 */
export function MarginGrid({
  as: Tag = "div",
  margin,
  notes,
  className,
  children,
  ...rest
}: Box & { margin?: ReactNode; notes?: ReactNode }) {
  return (
    <Tag className={cx(styles.marginGrid, className)} {...rest}>
      <div className={styles.margin}>{margin}</div>
      <div className={styles.measure}>{children}</div>
      {notes !== undefined && <div className={styles.notes}>{notes}</div>}
    </Tag>
  );
}
