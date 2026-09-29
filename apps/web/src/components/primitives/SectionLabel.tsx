import type { ReactNode } from "react";

import styles from "./SectionLabel.module.css";

export interface SectionLabelProps {
  children: ReactNode;
  meta?: ReactNode;
  /** Heading level when the label titles a section (it is then the section's heading). */
  as?: "h2" | "h3" | "p";
  id?: string;
}

/** A mono section label followed by a hairline rule and optional right-aligned metadata. */
export function SectionLabel({ children, meta, as: Tag = "h2", id }: SectionLabelProps) {
  return (
    <div className={styles.row}>
      <Tag className={styles.label} id={id}>
        {children}
      </Tag>
      <span className={styles.rule} aria-hidden="true" />
      {meta !== undefined && <span className={styles.meta}>{meta}</span>}
    </div>
  );
}
