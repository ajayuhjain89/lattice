/**
 * Boundary treatment (design-direction §5.4): hatching plus a dashed edge -- Lattice's
 * word for "not crossed". Every boundary says in words what it is; the texture is never
 * the only signal, and forced-colours mode keeps only the edge.
 *
 * Rule (§6.4): use it only around things the viewer is entitled to know exist -- their own
 * readable evidence that policy kept from the model, a "not available" page, their own
 * read-only settings, an acknowledgement such as "private channels you aren't in". Never
 * as a placeholder for an item the viewer can't read: no hatched row, slot or count.
 *
 *   band    a strip across a region ("Read-only here…", "Development sign-in")
 *   edge    a hatched leading edge on a row or block (a withheld exhibit)
 *   inline  a hatched cell or phrase ("Content not visible to administrators")
 */
import type { ElementType, ReactNode } from "react";

import { BoundaryGlyph } from "./glyphs";
import { Icon } from "./icons";
import styles from "./Boundary.module.css";

export function BoundaryBand({ children, icon = true, as: Tag = "div", className }: { children: ReactNode; icon?: boolean; as?: ElementType; className?: string }) {
  return (
    <Tag className={[styles.band, className].filter(Boolean).join(" ")}>
      {icon && (
        <span className={styles.icon}>
          <Icon as={BoundaryGlyph} />
        </span>
      )}
      <span className={styles.text}>{children}</span>
    </Tag>
  );
}

export function BoundaryEdge({ children, as: Tag = "div", className }: { children: ReactNode; as?: ElementType; className?: string }) {
  return <Tag className={[styles.edge, className].filter(Boolean).join(" ")}>{children}</Tag>;
}

export function BoundaryInline({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={[styles.inline, className].filter(Boolean).join(" ")}>
      <span className={styles.text}>{children}</span>
    </span>
  );
}
