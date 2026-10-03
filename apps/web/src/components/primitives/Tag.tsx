import type { ReactNode } from "react";

import styles from "./Tag.module.css";

export interface TagProps {
  children: ReactNode;
  /** default: outlined; quiet: hairline, muted; inverse: ink-filled (Restricted only). */
  tone?: "default" | "quiet" | "inverse";
  icon?: ReactNode;
  title?: string;
}

/**
 * A squared label for a categorical fact: source type, sensitivity, identity provider.
 * Tags never carry a signal colour -- categories are stable facts, not states.
 */
export function Tag({ children, tone = "default", icon, title }: TagProps) {
  return (
    <span className={[styles.tag, tone !== "default" && styles[tone]].filter(Boolean).join(" ")} title={title}>
      {icon && (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      )}
      <span className={styles.text}>{children}</span>
    </span>
  );
}
