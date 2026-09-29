import type { ReactNode } from "react";

import styles from "./Callout.module.css";

export type CalloutTone = "neutral" | "plain" | "info" | "warning" | "error" | "conflict";

export interface CalloutProps {
  tone: CalloutTone;
  label: string;
  icon?: ReactNode;
  children: ReactNode;
  /** Heading level of the label, when the callout is a section of the page. */
  labelAs?: "h2" | "h3" | "p";
  role?: "alert" | "status";
  "aria-labelledby"?: string;
  id?: string;
}

/** A tinted container that exists only for a real state (design-system §A3). */
export function Callout({ tone, label, icon, children, labelAs: Label = "p", role, id, ...rest }: CalloutProps) {
  const labelId = id ? `${id}-label` : undefined;
  return (
    <section
      className={`${styles.callout} ${styles[tone]}`}
      role={role}
      aria-labelledby={rest["aria-labelledby"] ?? labelId}
      id={id}
    >
      <div className={styles.header}>
        {icon && (
          <span className={styles.icon} aria-hidden="true">
            {icon}
          </span>
        )}
        <Label className={styles.label} id={labelId}>
          {label}
        </Label>
      </div>
      <div className={styles.body}>{children}</div>
    </section>
  );
}
