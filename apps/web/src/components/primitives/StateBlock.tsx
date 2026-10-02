/**
 * The four state families (design-direction §6.10). Every empty, missing, failed and
 * limited state is one of them, with one look each:
 *
 *   quiet     nothing to show (no evidence, an empty list) -- information, not alarm
 *   boundary  not available: identical whether the thing doesn't exist or isn't readable
 *             (SEC-5), drawn with the boundary hatch
 *   fault     something failed: a vermilion margin bar, the API's fixed message, the
 *             request reference, and usually *Try again*
 *   wait      a limit: an ochre margin bar and the wait in plain words; nothing is lost
 *
 * The family label is written out ("Not available", "Error", "Limit"), so the state
 * never depends on its colour or texture.
 */
import { AlertTriangle, Hourglass, Inbox, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { BoundaryGlyph, type Glyph } from "./glyphs";
import { Icon } from "./icons";
import styles from "./StateBlock.module.css";

export type StateFamily = "quiet" | "boundary" | "fault" | "wait";

const ICON: Record<StateFamily, LucideIcon | Glyph> = { quiet: Inbox, boundary: BoundaryGlyph, fault: AlertTriangle, wait: Hourglass };
const LABEL: Record<StateFamily, string> = { quiet: "Nothing here", boundary: "Not available", fault: "Error", wait: "Limit" };

export interface StateBlockProps {
  family: StateFamily;
  heading: ReactNode;
  headingLevel?: "h1" | "h2" | "h3";
  /** Overrides the family's written label ("Not found", "Access"). */
  label?: string;
  children?: ReactNode;
  /** A request reference for support (fault). */
  reference?: string | null;
  actions?: ReactNode;
  /** alert for a blocking failure, status for a limit; none for quiet and boundary. */
  role?: "alert" | "status";
  /** Inside a panel or callout region: no outer padding. */
  compact?: boolean;
  id?: string;
}

export function StateBlock({ family, heading, headingLevel: H = "h2", label, children, reference, actions, role, compact = false, id }: StateBlockProps) {
  return (
    <section className={[styles.state, styles[family], compact && styles.compact].filter(Boolean).join(" ")} role={role} id={id} data-state-family={family}>
      <p className={styles.label}>
        <span className={styles.icon}>
          <Icon as={ICON[family]} />
        </span>
        {label ?? LABEL[family]}
      </p>
      <H className={styles.heading}>{heading}</H>
      {children && <div className={styles.body}>{children}</div>}
      {reference && <p className={styles.reference}>Reference {reference}</p>}
      {actions && <div className={styles.actions}>{actions}</div>}
    </section>
  );
}
