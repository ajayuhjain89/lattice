import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { Icon } from "./icons";
import styles from "./ProvenanceTrail.module.css";

export interface TrailSegment {
  /** Stable key; also how tests find a segment. */
  key: string;
  label: ReactNode;
  /** Visually hidden prefix naming the segment for screen readers ("Source", "Version"). */
  kind?: string;
}

/**
 * The provenance trail (design-direction §5.2): source › document › version › range ›
 * verified. Every segment is a fact the API returned; callers omit absent ones rather
 * than inventing placeholders (D-8 governs any account segment). It's an ordered list
 * because the order is the chain of custody.
 */
export function ProvenanceTrail({
  segments,
  verified,
  label = "Provenance",
}: {
  segments: readonly (TrailSegment | null | false | undefined)[];
  /** The last step: the quote was checked verbatim against this text. */
  verified?: ReactNode;
  label?: string;
}) {
  const shown = segments.filter((s): s is TrailSegment => Boolean(s));
  if (!shown.length && !verified) return null;
  return (
    <ol className={styles.trail} aria-label={label}>
      {shown.map((s) => (
        <li key={s.key} className={styles.segment} data-segment={s.key}>
          {s.kind && <span className="visually-hidden">{s.kind}: </span>}
          {s.label}
        </li>
      ))}
      {verified && (
        <li className={`${styles.segment} ${styles.verified}`} data-segment="verified">
          <Icon as={Check} size={12} />
          {verified}
        </li>
      )}
    </ol>
  );
}
