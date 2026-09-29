import type { ReactNode } from "react";

import styles from "./Tag.module.css";

export interface TagProps {
  children: ReactNode;
  tone?: "default" | "muted" | "inverse" | "quiet";
  icon?: ReactNode;
  title?: string;
}

/** A mono, uppercase, ink-outlined pill for categorical facts (source type, sensitivity). */
export function Tag({ children, tone = "default", icon, title }: TagProps) {
  return (
    <span className={[styles.tag, tone !== "default" && styles[tone]].filter(Boolean).join(" ")} title={title}>
      {icon && (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      )}
      {children}
    </span>
  );
}
