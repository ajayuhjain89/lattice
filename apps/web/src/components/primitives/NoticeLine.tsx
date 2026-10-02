import type { ReactNode } from "react";

import styles from "./NoticeLine.module.css";

export interface NoticeLineProps {
  icon: ReactNode;
  /**
   * neutral: a quiet line · time: the stale notice, the one tinted line · disagreement:
   * supersession and conflict notes · boundary: something kept on the other side of a
   * boundary (the AI data policy), drawn with a hatched leading edge.
   */
  tone?: "neutral" | "time" | "disagreement" | "boundary";
  children: ReactNode;
}

/** One-line notice: icon + text, never colour alone. */
export function NoticeLine({ icon, tone = "neutral", children }: NoticeLineProps) {
  return (
    <div className={[styles.line, styles[tone]].filter(Boolean).join(" ")}>
      <span className={styles.icon} aria-hidden="true">
        {icon}
      </span>
      <span className={styles.text}>{children}</span>
    </div>
  );
}
