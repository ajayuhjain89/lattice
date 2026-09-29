import type { ReactNode } from "react";

import styles from "./NoticeLine.module.css";

export interface NoticeLineProps {
  icon: ReactNode;
  tone?: "info" | "warning" | "neutral" | "conflict";
  /** Tinted row (the stale notice); otherwise a quiet line. */
  boxed?: boolean;
  children: ReactNode;
}

/** One-line notice: icon + text, never colour alone. */
export function NoticeLine({ icon, tone = "info", boxed = false, children }: NoticeLineProps) {
  return (
    <div className={[styles.line, styles[tone], boxed && styles.boxed].filter(Boolean).join(" ")}>
      <span className={styles.icon} aria-hidden="true">
        {icon}
      </span>
      <span className={styles.text}>{children}</span>
    </div>
  );
}
